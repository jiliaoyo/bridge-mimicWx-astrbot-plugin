"""MimicWX-Linux platform adapter for AstrBot.

Registers ``mimicwx`` as a platform type that AstrBot can load from its
platform configuration.  This adapter:

1. Connects to MimicWX-Linux's WebSocket (``ws://host:port/ws``) to receive
   incoming WeChat messages in real time.
2. Parses each message and commits an ``AstrMessageEvent`` to AstrBot's
   internal event queue so the LLM / plugin pipeline can process it.
3. Sends replies via the MimicWX-Linux REST API (``POST /send``,
   ``POST /send_image``).

Configuration keys (all optional with defaults):

.. code-block:: yaml

    type: mimicwx
    id: mimicwx-0
    enable: true
    mimicwx_host: localhost        # MimicWX server host
    mimicwx_port: 8899             # MimicWX server port
    mimicwx_token: ""              # API bearer token (leave empty to disable)
    mimicwx_reconnect_interval: 5  # seconds between reconnect attempts
    mimicwx_max_reconnect_attempts: 0  # 0 = unlimited

"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import logger as astrbot_logger
from astrbot.api.platform import (
    AstrBotMessage,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)

from mimicwx_client import MimicWXClient, MimicWXClientError
from mimicwx_message_event import MimicWXMessageEvent
from mimicwx_message_parser import MimicWXMessageParser

logger = logging.getLogger("astrbot")


@register_platform_adapter(
    "mimicwx",
    "MimicWX-Linux 微信桥接适配器，通过 MimicWX-Linux REST/WebSocket API 接入微信消息",
    default_config_tmpl={
        "type": "mimicwx",
        "id": "mimicwx-0",
        "enable": False,
        "mimicwx_host": "localhost",
        "mimicwx_port": 8899,
        "mimicwx_token": "",
        "mimicwx_reconnect_interval": 5,
        "mimicwx_max_reconnect_attempts": 0,
    },
    adapter_display_name="MimicWX-Linux (微信桥接)",
    support_streaming_message=False,
)
class MimicWXPlatformAdapter(Platform):
    """AstrBot platform adapter bridging MimicWX-Linux ↔ AstrBot."""

    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings

        self.host: str = self.config.get("mimicwx_host", "localhost")
        self.port: int = int(self.config.get("mimicwx_port", 8899))
        self.token: str = self.config.get("mimicwx_token", "") or ""
        self.reconnect_interval: int = int(
            self.config.get("mimicwx_reconnect_interval", 5)
        )
        self.max_reconnect_attempts: int = int(
            self.config.get("mimicwx_max_reconnect_attempts", 0)
        )

        self.client = MimicWXClient(
            host=self.host,
            port=self.port,
            token=self.token,
        )
        self._parser = MimicWXMessageParser(bot_self_id="")
        self._running = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        # Maps session wxid → display name so outbound messages use names
        # that MimicWX can locate in the WeChat UI.
        self._session_to_name: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Platform abstract methods
    # ------------------------------------------------------------------

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="mimicwx",
            description=(
                "MimicWX-Linux 微信桥接适配器，通过 MimicWX-Linux REST/WebSocket API 接入微信消息"
            ),
            id=self.config.get("id", "mimicwx-0"),
            support_streaming_message=False,
        )

    async def run(self) -> None:
        """Main entry point — probe the server then start the WebSocket loop."""
        if not self.host:
            logger.error("[MimicWX] 配置缺少 mimicwx_host，无法启动适配器")
            return

        logger.info(
            "[MimicWX] 启动适配器 → %s:%s (token=%s)",
            self.host,
            self.port,
            "***" if self.token else "无",
        )

        # Probe the server to confirm it is reachable
        try:
            status = await self.client.get_status()
            logger.info(
                "[MimicWX] 服务状态: %s  版本: %s",
                status.get("status"),
                status.get("version"),
            )
            # Use service status info to set a meaningful self_id
            self.client_self_id = f"mimicwx_{self.host}_{self.port}"
            self._parser.bot_self_id = self.client_self_id
        except MimicWXClientError as exc:
            logger.error("[MimicWX] 无法连接到 MimicWX 服务器: %s", exc)
            return

        # Preload contact name mappings so outbound messages resolve correctly
        # even before any inbound message has been received.
        try:
            contacts_resp = await self.client.get_contacts()
            contacts_list = contacts_resp.get("contacts", [])
            for c in contacts_list:
                wxid = c.get("username", "")
                name = c.get("display_name", "") or c.get("nick_name", "")
                if wxid and name and name != wxid:
                    self._session_to_name[wxid] = name
            logger.info("[MimicWX] 预加载联系人映射: %d 条", len(self._session_to_name))
        except Exception as exc:
            logger.warning("[MimicWX] 预加载联系人失败（不影响运行）: %s", exc)

        self._running = True
        attempt = 0

        while self._running:
            try:
                attempt += 1
                logger.info("[MimicWX] WebSocket 连接尝试 #%d …", attempt)
                await self._ws_loop()
                if not self._running:
                    break
                logger.warning("[MimicWX] WebSocket 连接断开，准备重连 …")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[MimicWX] WebSocket 循环异常: %s", exc)

            if (
                self.max_reconnect_attempts > 0
                and attempt >= self.max_reconnect_attempts
            ):
                logger.error(
                    "[MimicWX] 已达最大重连次数 (%d)，停止重连",
                    self.max_reconnect_attempts,
                )
                break

            if self._running:
                logger.info(
                    "[MimicWX] %d 秒后重连 …", self.reconnect_interval
                )
                try:
                    await asyncio.sleep(self.reconnect_interval)
                except asyncio.CancelledError:
                    break

        self._running = False
        logger.info("[MimicWX] 适配器已停止")

    async def terminate(self) -> None:
        """Stop the adapter gracefully."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ------------------------------------------------------------------
    # WebSocket message loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Open a WebSocket connection and consume messages until disconnected."""
        ws_url = self.client.ws_url
        headers = self.client.auth_headers

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.ws_connect(
                ws_url,
                heartbeat=30,
                receive_timeout=90,
            ) as ws:
                self._ws = ws
                logger.info("[MimicWX] WebSocket 已连接: %s", ws_url)

                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_raw_text(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error("[MimicWX] WebSocket 错误: %s", ws.exception())
                        break
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        break

    async def _handle_raw_text(self, text: str) -> None:
        """Parse a raw WebSocket text frame and dispatch the message."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("[MimicWX] 无法解析 WebSocket 消息 JSON: %s", exc)
            return

        await self._dispatch_message(data)

    async def _dispatch_message(self, raw: dict[str, Any]) -> None:
        """Convert *raw* MimicWX message and commit it to the event queue."""
        abm = self._parser.parse_to_abm(raw)
        if abm is None:
            return

        platform_meta = self.meta()

        # Build session_id: group chats use group_id; private uses sender id
        if abm.group:
            session_id = abm.group.group_id
            # Cache group wxid → display name for outbound messages
            if abm.group.group_name and abm.group.group_name != session_id:
                self._session_to_name[session_id] = abm.group.group_name
        else:
            session_id = abm.sender.user_id
            # Prefer chat_display_name (MimicWX DB contact table, more reliable)
            # over sender.nickname which may be absent or equal to the wxid.
            abm_raw = getattr(abm, "raw_message", {}) or {}
            display = (
                abm_raw.get("chat_display_name")
                or abm.sender.nickname
                or ""
            )
            if display and display != session_id:
                self._session_to_name[session_id] = display

        event = MimicWXMessageEvent(
            message_str=abm.message_str,
            message_obj=abm,
            platform_meta=platform_meta,
            session_id=session_id,
            client=self.client,
            recipient=self._session_to_name.get(session_id, session_id),
        )
        self.commit_event(event)
        logger.debug(
            "[MimicWX] 提交事件: sender=%s chat=%s text=%.60s",
            abm.sender.user_id,
            session_id,
            abm.message_str,
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def _send_text_with_retry(self, recipient: str, text: str) -> None:
        """Send text once; retry once when server reports verified=false."""
        result = await self.client.send_text(to=recipient, text=text)
        verified = result.get("verified", True) if isinstance(result, dict) else True
        if verified is not False:
            return

        logger.warning("[MimicWX] 文本发送未验证，重试一次 → %s", recipient)
        try:
            await self.client.chat_with(who=recipient)
        except (MimicWXClientError, ValueError) as exc:
            logger.warning("[MimicWX] 重试前切换会话失败 → %s: %s", recipient, exc)

        retry_result = await self.client.send_text(to=recipient, text=text)
        retry_verified = (
            retry_result.get("verified", True)
            if isinstance(retry_result, dict)
            else True
        )
        if retry_verified is False:
            logger.warning("[MimicWX] 文本重试后仍未验证 → %s", recipient)

    async def send_by_session(self, session, message_chain) -> None:
        """Send *message_chain* to the WeChat contact identified by *session*."""
        session_id: str = session.session_id
        # Resolve to display name so MimicWX can locate the contact in the
        # WeChat UI (which shows names, not wxid identifiers).
        recipient: str = self._session_to_name.get(session_id, session_id)

        logger.info(
            "[MimicWX] send_by_session called: session=%s recipient=%s",
            session_id,
            recipient,
        )

        # Collect text segments and image segments separately.
        # Unsupported segments are ignored to avoid framework-level
        # error replies like "not a valid file" being sent to users.
        text_parts: list[str] = []
        image_segments: list = []

        for seg in message_chain.chain:
            if isinstance(seg, Comp.Plain):
                if seg.text is not None:
                    text_parts.append(seg.text)
            elif isinstance(seg, Comp.Image):
                image_segments.append(seg)
            else:
                logger.debug(
                    "[MimicWX] 忽略不支持的消息段类型: %s", type(seg).__name__
                )

        # Send merged text first
        if text_parts:
            merged_text = "".join(text_parts)
            if merged_text:
                try:
                    await self._send_text_with_retry(recipient, merged_text)
                    logger.debug(
                        "[MimicWX] 文本消息已发送 → %s: %.60s", recipient, merged_text
                    )
                except (MimicWXClientError, ValueError) as exc:
                    logger.error("[MimicWX] 发送文本失败 → %s: %s", recipient, exc)

        # Send each image
        for img in image_segments:
            try:
                b64 = await img.convert_to_base64()
                filename = getattr(img, "file", None) or "image.png"
                # Only use the basename for the filename hint
                if filename and "/" in filename:
                    filename = filename.rsplit("/", 1)[-1]
                await self.client.send_image(
                    to=recipient,
                    image_b64=b64,
                    name=filename or "image.png",
                )
                logger.debug("[MimicWX] 图片已发送 → %s", recipient)
            except (MimicWXClientError, ValueError) as exc:
                logger.error("[MimicWX] 发送图片失败 → %s: %s", recipient, exc)

        await super().send_by_session(session, message_chain)
