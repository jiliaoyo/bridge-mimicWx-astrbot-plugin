"""MimicWX message parsing utilities.

Converts raw MimicWX WebSocket JSON payloads (DbMessage + MsgContent)
into AstrBot AstrBotMessage objects ready for the event queue.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("astrbot")

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_group_chat(chat_id: str) -> bool:
    """Return True if *chat_id* identifies a WeChat group chatroom.

    Group chat IDs in WeChat end with ``@chatroom``.
    """
    if not chat_id:
        return False
    return chat_id.endswith("@chatroom")


def extract_text_content(parsed: dict[str, Any] | None) -> str:
    """Convert a MsgContent dict (tagged-enum serde) to a human-readable string.

    Args:
        parsed: The ``parsed`` field of a DbMessage, e.g.
                ``{"type": "Text", "data": {"text": "hello"}}``.

    Returns:
        A plain-text representation of the message content.
    """
    if not parsed or not isinstance(parsed, dict):
        return ""

    msg_type = parsed.get("type", "Unknown")
    data: dict = parsed.get("data") or {}

    match msg_type:
        case "Text":
            return data.get("text", "")
        case "Image":
            return "[图片]"
        case "Voice":
            duration = data.get("duration_ms")
            if duration and duration >= 1000:
                return f"[语音 {duration // 1000}s]"
            return "[语音]"
        case "Video":
            return "[视频]"
        case "Emoji":
            return "[表情]"
        case "App":
            title = data.get("title") or ""
            desc = data.get("desc") or ""
            app_type = data.get("app_type")
            label = _app_type_label(app_type, title)
            body = title or desc
            return f"[{label}] {body}".strip() if body else f"[{label}]"
        case "System":
            return f"[系统消息] {data.get('text', '')}"
        case "Unknown":
            return f"[未知消息 type={data.get('msg_type', '?')}]"
        case _:
            return f"[{msg_type}]"


def _app_type_label(app_type: int | None, title: str) -> str:
    """Infer a human-readable label for App-type messages."""
    _TYPE_MAP = {
        3: "音乐",
        6: "文件",
        19: "转发",
        33: "小程序",
        36: "小程序",
        42: "名片",
        2000: "转账",
        2001: "红包",
    }
    if app_type and app_type in _TYPE_MAP:
        return _TYPE_MAP[app_type]
    # Infer from file extension in title
    if title:
        tl = title.lower()
        file_exts = (
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".zip", ".rar", ".7z", ".txt", ".csv", ".apk", ".exe", ".dmg",
        )
        if any(tl.endswith(ext) for ext in file_exts):
            return "文件"
    return "链接"


def _normalize_msg_type(raw_msg_type: Any) -> int:
    """Extract base message type from raw/packed values."""
    try:
        msg_type = int(raw_msg_type)
    except (TypeError, ValueError):
        return 0
    if msg_type > 0xFFFF:
        return msg_type & 0xFFFF
    return msg_type


def _resolve_talker(raw: dict[str, Any]) -> str:
    """Resolve sender wxid from possible field aliases."""
    for key in ("talker", "sender", "from_user", "from_user_name"):
        value = raw.get(key, "")
        if isinstance(value, str) and value:
            return value
    return ""


def _resolve_chat(raw: dict[str, Any]) -> str:
    """Resolve chat/session id from possible field aliases."""
    for key in ("chat", "user_name", "room_id"):
        value = raw.get(key, "")
        if isinstance(value, str) and value:
            return value
    return ""


def _normalize_sender_and_chat(raw: dict[str, Any]) -> tuple[str, str]:
    """Return normalized (talker, chat) for private/group messages."""
    talker = _resolve_talker(raw)
    chat = _resolve_chat(raw)

    # Some payloads may place group-id in talker and sender-id in chat.
    if is_group_chat(talker) and not is_group_chat(chat):
        possible_sender = _resolve_talker(
            {
                "talker": raw.get("sender", ""),
                "sender": raw.get("from_user", ""),
                "from_user": raw.get("from_user_name", ""),
            }
        )
        if possible_sender:
            chat = talker
            talker = possible_sender

    return talker, chat


def _normalize_incoming_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize incoming MimicWX payload keys to stable canonical fields."""
    normalized = dict(raw)

    talker, chat = _normalize_sender_and_chat(raw)
    if talker:
        normalized["talker"] = talker
    if chat:
        normalized["chat"] = chat

    normalized["msg_type"] = _normalize_msg_type(raw.get("msg_type", 0))

    if not normalized.get("talker_display_name"):
        normalized["talker_display_name"] = (
            raw.get("talker_display")
            or raw.get("sender_display")
            or raw.get("sender_display_name")
            or ""
        )
    if not normalized.get("chat_display_name"):
        normalized["chat_display_name"] = (
            raw.get("chat_display")
            or raw.get("room_display_name")
            or ""
        )

    return normalized


# ---------------------------------------------------------------------------
# MimicWXMessageParser
# ---------------------------------------------------------------------------


class MimicWXMessageParser:
    """Converts raw MimicWX ``DbMessage`` dicts into ``AstrBotMessage`` objects.

    Args:
        bot_self_id: The wxid of the bot account (used to filter self-sent
                     messages).  Can be updated after initialisation via the
                     ``bot_self_id`` attribute.
    """

    def __init__(self, bot_self_id: str = "") -> None:
        self.bot_self_id = bot_self_id

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_process(self, raw: dict[str, Any]) -> bool:
        """Return True if *raw* is a processable inbound WeChat message.

        Messages are rejected if:
        - They are ``sent`` confirmations (``{"type": "sent", ...}``).
        - They are sent by the bot itself (``is_self == True``).
        - They are system messages (``msg_type`` 10000 / 10002).
        - They have no talker (anonymous / internal notifications).
        """
        raw = _normalize_incoming_raw(raw)

        event_type = raw.get("type")
        if event_type and event_type not in ("db_message", "sent"):
            return False

        # Sent-confirmation events broadcast by MimicWX after we POST /send
        if event_type == "sent":
            return False

        is_self = raw.get("is_self", False)
        if is_self:
            return False

        talker, _chat = _normalize_sender_and_chat(raw)
        if not talker:
            return False

        msg_type = raw.get("msg_type", 0)
        if msg_type in (10000, 10002):
            return False

        return True

    def parse_to_abm(self, raw: dict[str, Any]):
        """Convert *raw* MimicWX message to an ``AstrBotMessage``.

        Returns:
            An ``AstrBotMessage`` instance, or ``None`` if the message
            should not be processed (filtered by :meth:`should_process`
            or structurally invalid).
        """
        raw = _normalize_incoming_raw(raw)

        if not self.should_process(raw):
            return None

        # Require at minimum a chat field to build a session
        _talker, chat = _normalize_sender_and_chat(raw)
        if not chat:
            logger.debug("[MimicWX] Dropping message with empty chat field")
            return None

        try:
            abm = self._build_abm(raw)
            if abm is None:
                return None
            return abm
        except Exception as exc:
            logger.warning("[MimicWX] Failed to parse message: %s | raw=%s", exc, raw)
            return None

    # ------------------------------------------------------------------
    # Internal builder
    # ------------------------------------------------------------------

    def _build_abm(self, raw: dict[str, Any]):
        """Build the AstrBotMessage — imported lazily to avoid circular deps."""
        # Lazy import so the module can be tested without a full AstrBot install
        from astrbot.api.platform import AstrBotMessage, Group, MessageMember, MessageType
        import astrbot.api.message_components as Comp

        abm = AstrBotMessage()

        abm.self_id = self.bot_self_id
        abm.message_id = str(raw.get("local_id", ""))
        abm.timestamp = int(raw.get("create_time", 0))

        talker, chat = _normalize_sender_and_chat(raw)
        talker_display = raw.get("talker_display_name", "")
        chat_display = raw.get("chat_display_name", "")

        if is_group_chat(chat):
            # Group chat: talker_display is the member's in-group nickname
            talker_name = talker_display or talker
            chat_name = chat_display or chat
        else:
            # Private chat: prefer talker_display, fall back to chat_display
            # (contact display name from DB), then wxid
            talker_name = talker_display or chat_display or talker
            chat_name = chat_display or chat

        abm.sender = MessageMember(user_id=talker, nickname=talker_name)

        if is_group_chat(chat):
            abm.type = MessageType.GROUP_MESSAGE
            abm.group = Group(group_id=chat, group_name=chat_name)
            abm.session_id = chat
        else:
            abm.type = MessageType.FRIEND_MESSAGE
            abm.group = None
            abm.session_id = chat

        # Build message chain
        parsed = raw.get("parsed")
        text_content = extract_text_content(parsed)
        abm.message_str = text_content

        components: list = []
        msg_type_str = (parsed or {}).get("type", "Unknown")
        data: dict = (parsed or {}).get("data") or {}

        if msg_type_str == "Text":
            text = data.get("text", "")
            if text:
                components.append(Comp.Plain(text=text))
        elif msg_type_str == "Image":
            # Align with MimicWX.js adapter: inbound images are represented
            # as a plain placeholder to avoid invalid local-file errors.
            components.append(Comp.Plain(text="[图片]"))
        else:
            # For all other types use plain text representation
            if text_content:
                components.append(Comp.Plain(text=text_content))

        abm.message = components
        abm.raw_message = raw
        return abm
