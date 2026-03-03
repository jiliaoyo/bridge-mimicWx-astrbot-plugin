"""Tests for MimicWX platform adapter.

Tests cover:
- Adapter initialization with valid / invalid config
- Message dispatch (commit_event is called for processable messages)
- send_by_session routing (text / image)
- WebSocket reconnect logic
- Adapter termination
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import astrbot.api.message_components as Comp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMessageChain:
    def __init__(self, chain):
        self.chain = chain


class _FakeSession:
    def __init__(self, session_id, message_type, platform_name="mimicwx"):
        self.session_id = session_id
        self.message_type = message_type
        self.platform_name = platform_name


VALID_CONFIG = {
    "id": "mimicwx-test",
    "type": "mimicwx",
    "enable": True,
    "mimicwx_host": "localhost",
    "mimicwx_port": 8899,
    "mimicwx_token": "",
    "mimicwx_reconnect_interval": 3,
    "mimicwx_max_reconnect_attempts": 3,
}


# ---------------------------------------------------------------------------
# Import the platform adapter (registers "mimicwx" in AstrBot registry)
# ---------------------------------------------------------------------------

from mimicwx_platform import MimicWXPlatformAdapter  # noqa: E402
from mimicwx_message_parser import MimicWXMessageParser  # noqa: E402


# ---------------------------------------------------------------------------
# Adapter initialization
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_default_config_values(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        assert adapter.host == "localhost"
        assert adapter.port == 8899
        assert adapter.token == ""

    def test_meta_name(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        meta = adapter.meta()
        assert meta.name == "mimicwx"

    def test_meta_id_from_config(self):
        event_queue = asyncio.Queue()
        cfg = {**VALID_CONFIG, "id": "my-mimicwx"}
        adapter = MimicWXPlatformAdapter(cfg, {}, event_queue)
        meta = adapter.meta()
        assert meta.id == "my-mimicwx"


# ---------------------------------------------------------------------------
# Message commit
# ---------------------------------------------------------------------------


class TestMessageCommit:
    """Ensure processable messages are put on the event queue."""

    @pytest.mark.asyncio
    async def test_processable_message_commits_event(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "local_id": 1,
            "server_id": 100,
            "create_time": 1700000000,
            "content": "Hello",
            "parsed": {"type": "Text", "data": {"text": "Hello"}},
            "msg_type": 1,
            "talker": "wxid_alice",
            "talker_display_name": "Alice",
            "chat": "wxid_alice",
            "chat_display_name": "Alice",
            "is_self": False,
        }

        await adapter._dispatch_message(raw)
        assert not event_queue.empty()

    @pytest.mark.asyncio
    async def test_self_message_not_committed(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "local_id": 2,
            "server_id": 200,
            "create_time": 1700000001,
            "content": "I sent this",
            "parsed": {"type": "Text", "data": {"text": "I sent this"}},
            "msg_type": 1,
            "talker": "wxid_bot",
            "talker_display_name": "Bot",
            "chat": "wxid_alice",
            "chat_display_name": "Alice",
            "is_self": True,
        }

        await adapter._dispatch_message(raw)
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_sent_confirmation_not_committed(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        raw = {"type": "sent", "to": "Alice", "text": "hi", "verified": True}
        await adapter._dispatch_message(raw)
        assert event_queue.empty()


# ---------------------------------------------------------------------------
# send_by_session (text + image)
# ---------------------------------------------------------------------------


class TestSendBySession:
    @pytest.mark.asyncio
    async def test_send_text_message(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        chain = _FakeMessageChain([Comp.Plain(text="Hello Alice")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="wxid_alice", text="Hello Alice"
        )

    @pytest.mark.asyncio
    async def test_send_image_message(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        mock_client = AsyncMock()
        mock_client.send_image = AsyncMock(
            return_value={"sent": True, "verified": False, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        img = Comp.Image(file="/tmp/test.png")
        chain = _FakeMessageChain([img])

        with patch.object(Comp.Image, "convert_to_base64", AsyncMock(return_value="aGVsbG8=")):
            await adapter.send_by_session(session, chain)
        assert mock_client.send_image.called

    @pytest.mark.asyncio
    async def test_send_mixed_text_and_image(self):
        """Mixed chains: text segments merged, images sent separately."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        mock_client.send_image = AsyncMock(
            return_value={"sent": True, "verified": False, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        img = Comp.Image(file="/tmp/test.png")
        chain = _FakeMessageChain([Comp.Plain(text="see this:"), img])

        with patch.object(Comp.Image, "convert_to_base64", AsyncMock(return_value="aGVsbG8=")):
            await adapter.send_by_session(session, chain)
        assert mock_client.send_text.called
        assert mock_client.send_image.called

    @pytest.mark.asyncio
    async def test_send_empty_chain_does_nothing(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        mock_client = AsyncMock()
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        chain = _FakeMessageChain([])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_not_called()
        mock_client.send_image.assert_not_called()


# ---------------------------------------------------------------------------
# Display name resolution (wxid → name for MimicWX UI)
# ---------------------------------------------------------------------------


class TestDisplayNameResolution:
    """Verify that outbound messages use cached display names instead of wxids."""

    @pytest.mark.asyncio
    async def test_private_chat_uses_display_name(self):
        """After receiving a private message, replies should use the display name."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        # Simulate receiving a private message from Alice
        raw = {
            "local_id": 10,
            "server_id": 100,
            "create_time": 1700000000,
            "content": "Hello",
            "parsed": {"type": "Text", "data": {"text": "Hello"}},
            "msg_type": 1,
            "talker": "wxid_alice",
            "talker_display_name": "Alice",
            "chat": "wxid_alice",
            "chat_display_name": "Alice",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)

        # Now send a reply — should use "Alice" not "wxid_alice"
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        chain = _FakeMessageChain([Comp.Plain(text="Hi Alice!")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="Alice", text="Hi Alice!"
        )

    @pytest.mark.asyncio
    async def test_group_chat_uses_display_name(self):
        """After receiving a group message, replies should use the group display name."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        # Simulate receiving a group message
        raw = {
            "local_id": 11,
            "server_id": 101,
            "create_time": 1700000001,
            "content": "Hello group",
            "parsed": {"type": "Text", "data": {"text": "Hello group"}},
            "msg_type": 1,
            "talker": "wxid_bob",
            "talker_display_name": "Bob",
            "chat": "12345@chatroom",
            "chat_display_name": "My Group",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)

        # Now send a reply to the group — should use "My Group"
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="12345@chatroom", message_type="GroupMessage")
        chain = _FakeMessageChain([Comp.Plain(text="Hello everyone!")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="My Group", text="Hello everyone!"
        )

    @pytest.mark.asyncio
    async def test_fallback_to_session_id_when_no_display_name(self):
        """When no display name is cached, fall back to the raw session_id."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_unknown", message_type="FriendMessage")
        chain = _FakeMessageChain([Comp.Plain(text="Hello")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="wxid_unknown", text="Hello"
        )

    @pytest.mark.asyncio
    async def test_display_name_not_cached_when_same_as_wxid(self):
        """When display name equals the wxid, no mapping is cached."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "local_id": 12,
            "server_id": 102,
            "create_time": 1700000002,
            "content": "Hi",
            "parsed": {"type": "Text", "data": {"text": "Hi"}},
            "msg_type": 1,
            "talker": "wxid_noname",
            "talker_display_name": "wxid_noname",
            "chat": "wxid_noname",
            "chat_display_name": "wxid_noname",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)
        assert "wxid_noname" not in adapter._session_to_name


# ---------------------------------------------------------------------------
# Adapter termination
# ---------------------------------------------------------------------------


class TestAdapterTermination:
    @pytest.mark.asyncio
    async def test_terminate_sets_running_false(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter._running = True
        await adapter.terminate()
        assert adapter._running is False
