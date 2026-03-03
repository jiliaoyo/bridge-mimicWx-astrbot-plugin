"""Tests for MimicWX message conversion to AstrBot format.

Tests conversion of raw MimicWX WebSocket JSON payloads into
AstrBotMessage objects with correct fields.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import astrbot.api.message_components as Comp
from mimicwx_message_parser import (
    MimicWXMessageParser,
    is_group_chat,
    extract_text_content,
)


# ---------------------------------------------------------------------------
# Sample MimicWX message payloads
# ---------------------------------------------------------------------------

PRIVATE_TEXT_MSG = {
    "local_id": 1,
    "server_id": 100,
    "create_time": 1700000000,
    "content": "Hello World",
    "parsed": {"type": "Text", "data": {"text": "Hello World"}},
    "msg_type": 1,
    "talker": "wxid_alice",
    "talker_display_name": "Alice",
    "chat": "wxid_alice",
    "chat_display_name": "Alice",
    "is_self": False,
}

GROUP_TEXT_MSG = {
    "local_id": 2,
    "server_id": 200,
    "create_time": 1700000001,
    "content": "Group hello",
    "parsed": {"type": "Text", "data": {"text": "Group hello"}},
    "msg_type": 1,
    "talker": "wxid_bob",
    "talker_display_name": "Bob",
    "chat": "12345678@chatroom",
    "chat_display_name": "Dev Group",
    "is_self": False,
}

SELF_MSG = {
    "local_id": 3,
    "server_id": 300,
    "create_time": 1700000002,
    "content": "I said this",
    "parsed": {"type": "Text", "data": {"text": "I said this"}},
    "msg_type": 1,
    "talker": "wxid_self",
    "talker_display_name": "Me",
    "chat": "wxid_alice",
    "chat_display_name": "Alice",
    "is_self": True,
}

IMAGE_MSG = {
    "local_id": 4,
    "server_id": 400,
    "create_time": 1700000003,
    "content": "",
    "parsed": {"type": "Image", "data": {"path": "/tmp/img.jpg"}},
    "msg_type": 3,
    "talker": "wxid_carol",
    "talker_display_name": "Carol",
    "chat": "wxid_carol",
    "chat_display_name": "Carol",
    "is_self": False,
}

SYSTEM_MSG = {
    "local_id": 5,
    "server_id": 500,
    "create_time": 1700000004,
    "content": "Alice joined the group",
    "parsed": {"type": "System", "data": {"text": "Alice joined the group"}},
    "msg_type": 10000,
    "talker": "",
    "talker_display_name": "",
    "chat": "12345678@chatroom",
    "chat_display_name": "Dev Group",
    "is_self": False,
}

SENT_CONFIRMATION = {
    "type": "sent",
    "to": "Alice",
    "text": "reply",
    "verified": True,
}

UNKNOWN_MSG = {
    "local_id": 6,
    "server_id": 600,
    "create_time": 1700000005,
    "content": "raw",
    "parsed": {"type": "Unknown", "data": {"raw": "raw", "msg_type": 9999}},
    "msg_type": 9999,
    "talker": "wxid_dave",
    "talker_display_name": "Dave",
    "chat": "wxid_dave",
    "chat_display_name": "Dave",
    "is_self": False,
}


# ---------------------------------------------------------------------------
# is_group_chat helper
# ---------------------------------------------------------------------------


class TestIsGroupChat:
    def test_group_chat_id(self):
        assert is_group_chat("12345678@chatroom") is True

    def test_private_chat_id(self):
        assert is_group_chat("wxid_alice") is False

    def test_empty_string(self):
        assert is_group_chat("") is False

    def test_chatroom_suffix(self):
        assert is_group_chat("abc@chatroom") is True

    def test_no_at_symbol(self):
        assert is_group_chat("notachatroom") is False


# ---------------------------------------------------------------------------
# extract_text_content helper
# ---------------------------------------------------------------------------


class TestExtractTextContent:
    def test_text_type(self):
        parsed = {"type": "Text", "data": {"text": "hello"}}
        assert extract_text_content(parsed) == "hello"

    def test_image_type(self):
        parsed = {"type": "Image", "data": {"path": "/tmp/x.jpg"}}
        assert extract_text_content(parsed) == "[图片]"

    def test_voice_type_with_duration(self):
        parsed = {"type": "Voice", "data": {"duration_ms": 3000}}
        assert extract_text_content(parsed) == "[语音 3s]"

    def test_voice_type_no_duration(self):
        parsed = {"type": "Voice", "data": {"duration_ms": None}}
        assert extract_text_content(parsed) == "[语音]"

    def test_video_type(self):
        parsed = {"type": "Video", "data": {}}
        assert extract_text_content(parsed) == "[视频]"

    def test_emoji_type(self):
        parsed = {"type": "Emoji", "data": {"url": "http://example.com/e.gif"}}
        assert extract_text_content(parsed) == "[表情]"

    def test_app_type_with_title(self):
        parsed = {"type": "App", "data": {"title": "Music", "desc": None, "url": None, "app_type": None}}
        assert "Music" in extract_text_content(parsed)

    def test_system_type(self):
        parsed = {"type": "System", "data": {"text": "User left"}}
        assert extract_text_content(parsed) == "[系统消息] User left"

    def test_unknown_type(self):
        parsed = {"type": "Unknown", "data": {"raw": "???", "msg_type": 9999}}
        assert "[未知消息" in extract_text_content(parsed)

    def test_missing_parsed_field(self):
        assert extract_text_content(None) == ""
        assert extract_text_content({}) == ""


# ---------------------------------------------------------------------------
# MimicWXMessageParser
# ---------------------------------------------------------------------------


class TestMimicWXMessageParser:
    def setup_method(self):
        self.parser = MimicWXMessageParser(bot_self_id="wxid_self")

    # --- should_process ---

    def test_should_process_text_message(self):
        assert self.parser.should_process(PRIVATE_TEXT_MSG) is True

    def test_should_process_db_message_event(self):
        msg = {**PRIVATE_TEXT_MSG, "type": "db_message"}
        assert self.parser.should_process(msg) is True

    def test_should_not_process_self_message(self):
        assert self.parser.should_process(SELF_MSG) is False

    def test_should_not_process_sent_confirmation(self):
        assert self.parser.should_process(SENT_CONFIRMATION) is False

    def test_should_not_process_system_message(self):
        assert self.parser.should_process(SYSTEM_MSG) is False

    def test_should_not_process_missing_talker(self):
        msg = {**PRIVATE_TEXT_MSG, "talker": ""}
        assert self.parser.should_process(msg) is False

    def test_should_not_process_packed_system_msg_type(self):
        packed_system = {**PRIVATE_TEXT_MSG, "msg_type": (12345 << 16) | 10000}
        assert self.parser.should_process(packed_system) is False

    def test_should_not_process_unknown_event_type(self):
        msg = {**PRIVATE_TEXT_MSG, "type": "heartbeat"}
        assert self.parser.should_process(msg) is False

    # --- parse_to_abm ---

    def test_parse_private_text(self):
        abm = self.parser.parse_to_abm(PRIVATE_TEXT_MSG)
        assert abm is not None
        assert abm.message_str == "Hello World"
        assert abm.sender.user_id == "wxid_alice"
        assert abm.sender.nickname == "Alice"
        assert abm.group is None
        assert abm.self_id == "wxid_self"

    def test_parse_group_text(self):
        abm = self.parser.parse_to_abm(GROUP_TEXT_MSG)
        assert abm is not None
        assert abm.message_str == "Group hello"
        assert abm.sender.user_id == "wxid_bob"
        assert abm.group is not None
        assert abm.group.group_id == "12345678@chatroom"
        assert abm.group.group_name == "Dev Group"

    def test_parse_group_with_user_name_field(self):
        msg = {
            **GROUP_TEXT_MSG,
            "chat": "",
            "user_name": "12345678@chatroom",
        }
        abm = self.parser.parse_to_abm(msg)
        assert abm is not None
        assert abm.group is not None
        assert abm.group.group_id == "12345678@chatroom"

    def test_parse_group_with_display_alias_fields(self):
        msg = {
            **GROUP_TEXT_MSG,
            "talker_display_name": "",
            "chat_display_name": "",
            "talker_display": "Bob Alias",
            "chat_display": "Dev Group Alias",
        }
        abm = self.parser.parse_to_abm(msg)
        assert abm is not None
        assert abm.sender.nickname == "Bob Alias"
        assert abm.group is not None
        assert abm.group.group_name == "Dev Group Alias"

    def test_parse_private_with_display_alias_fields(self):
        msg = {
            **PRIVATE_TEXT_MSG,
            "talker_display_name": "",
            "chat_display_name": "",
            "talker_display": "Alice Alias",
            "chat_display": "Alice Chat Alias",
        }
        abm = self.parser.parse_to_abm(msg)
        assert abm is not None
        assert abm.sender.nickname == "Alice Alias"

    def test_parse_image_message(self):
        abm = self.parser.parse_to_abm(IMAGE_MSG)
        assert abm is not None
        assert "[图片]" in abm.message_str

    def test_parse_image_with_invalid_path_falls_back_to_plain(self):
        msg = {
            **IMAGE_MSG,
            "parsed": {
                "type": "Image",
                "data": {
                    "path": "3057020100044b3049020100020464edd7cb02032dd0e9"
                },
            },
        }
        abm = self.parser.parse_to_abm(msg)
        assert abm is not None
        assert len(abm.message) == 1
        assert isinstance(abm.message[0], Comp.Plain)
        assert abm.message[0].text == "[图片]"

    def test_parse_image_with_existing_file_still_uses_plain_placeholder(self):
        msg = {
            **IMAGE_MSG,
            "parsed": {"type": "Image", "data": {"path": "/tmp/existing.jpg"}},
        }
        abm = self.parser.parse_to_abm(msg)
        assert abm is not None
        assert len(abm.message) == 1
        assert isinstance(abm.message[0], Comp.Plain)
        assert abm.message[0].text == "[图片]"

    def test_parse_returns_none_for_self(self):
        result = self.parser.parse_to_abm(SELF_MSG)
        assert result is None

    def test_parse_returns_none_for_sent_confirmation(self):
        result = self.parser.parse_to_abm(SENT_CONFIRMATION)
        assert result is None

    def test_message_id_is_set(self):
        abm = self.parser.parse_to_abm(PRIVATE_TEXT_MSG)
        assert abm.message_id == "1"

    def test_message_timestamp_is_set(self):
        abm = self.parser.parse_to_abm(PRIVATE_TEXT_MSG)
        assert abm.timestamp == 1700000000

    def test_parse_unknown_message_produces_placeholder(self):
        abm = self.parser.parse_to_abm(UNKNOWN_MSG)
        assert abm is not None
        assert "[未知消息" in abm.message_str

    def test_parse_invalid_json_structure_returns_none(self):
        result = self.parser.parse_to_abm({"garbage": "data"})
        assert result is None

    # --- nickname fallback (private chat) ---

    def test_private_chat_nickname_uses_talker_display_name(self):
        """talker_display_name is present and non-empty — use it directly."""
        msg = {**PRIVATE_TEXT_MSG, "talker_display_name": "Alice Display", "chat_display_name": "Alice Chat"}
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "Alice Display"

    def test_private_chat_nickname_falls_back_to_chat_display_name(self):
        """talker_display_name is empty string — fall back to chat_display_name."""
        msg = {**PRIVATE_TEXT_MSG, "talker_display_name": "", "chat_display_name": "我要吃大汉堡"}
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "我要吃大汉堡"

    def test_private_chat_nickname_falls_back_to_wxid(self):
        """Both display names are empty — fall back to talker wxid."""
        msg = {**PRIVATE_TEXT_MSG, "talker_display_name": "", "chat_display_name": ""}
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "wxid_alice"

    def test_private_chat_nickname_missing_talker_display_name(self):
        """talker_display_name key absent — fall back to chat_display_name."""
        msg = {k: v for k, v in PRIVATE_TEXT_MSG.items() if k != "talker_display_name"}
        msg["chat_display_name"] = "从联系人表"
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "从联系人表"

    # --- group chat nickname unaffected ---

    def test_group_chat_sender_nickname_uses_talker_display_name(self):
        """In group chats talker_display_name (member nickname) should still be used."""
        msg = {**GROUP_TEXT_MSG, "talker_display_name": "Bob In Group", "chat_display_name": "Dev Group"}
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "Bob In Group"
        assert abm.group.group_name == "Dev Group"

    def test_group_chat_sender_nickname_falls_back_to_wxid_not_group_name(self):
        """If talker_display_name is empty in a group, fall back to wxid, not group name."""
        msg = {**GROUP_TEXT_MSG, "talker_display_name": "", "chat_display_name": "Dev Group"}
        abm = self.parser.parse_to_abm(msg)
        assert abm.sender.nickname == "wxid_bob"
        assert abm.group.group_name == "Dev Group"
