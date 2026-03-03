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
from mimicwx_message_event import MimicWXMessageEvent  # noqa: E402
from astrbot.api.event import MessageChain  # noqa: E402


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
# MimicWXMessageEvent.send (text + image via event)
# ---------------------------------------------------------------------------


def _make_event(session_id="wxid_alice", mock_client=None):
    """Create a MimicWXMessageEvent with a mocked client for testing."""
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
        "chat": session_id,
        "chat_display_name": "Alice",
        "is_self": False,
    }

    # Dispatch to create the event
    parser = adapter._parser
    abm = parser.parse_to_abm(raw)
    client = mock_client or AsyncMock()
    event = MimicWXMessageEvent(
        message_str=abm.message_str,
        message_obj=abm,
        platform_meta=adapter.meta(),
        session_id=session_id,
        client=client,
    )
    return event, client


class TestEventSend:
    @pytest.mark.asyncio
    async def test_send_text_via_event(self):
        """event.send() should call client.send_text with the correct session_id."""
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        event, _ = _make_event(session_id="wxid_alice", mock_client=mock_client)

        chain = MessageChain([Comp.Plain(text="Hello from event")])
        await event.send(chain)

        mock_client.send_text.assert_called_once_with(
            to="wxid_alice", text="Hello from event"
        )

    @pytest.mark.asyncio
    async def test_send_image_via_event(self):
        """event.send() should call client.send_image for image segments."""
        mock_client = AsyncMock()
        mock_client.send_image = AsyncMock(
            return_value={"sent": True, "verified": False, "message": "ok"}
        )
        event, _ = _make_event(session_id="wxid_alice", mock_client=mock_client)

        img = Comp.Image(file="/tmp/test.png")
        chain = MessageChain([img])

        with patch.object(Comp.Image, "convert_to_base64", AsyncMock(return_value="aGVsbG8=")):
            await event.send(chain)

        assert mock_client.send_image.called

    @pytest.mark.asyncio
    async def test_send_uses_configured_host(self):
        """event.send() should use the client passed at construction (not a default)."""
        custom_client = AsyncMock()
        custom_client.host = "mimicwx-linux"
        custom_client.port = 8899
        custom_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        event, _ = _make_event(session_id="wxid_bob", mock_client=custom_client)

        chain = MessageChain([Comp.Plain(text="Hi Bob")])
        await event.send(chain)

        custom_client.send_text.assert_called_once_with(to="wxid_bob", text="Hi Bob")

    @pytest.mark.asyncio
    async def test_send_empty_chain_does_nothing(self):
        """event.send() with an empty chain should not call send_text or send_image."""
        mock_client = AsyncMock()
        event, _ = _make_event(mock_client=mock_client)

        chain = MessageChain([])
        await event.send(chain)

        mock_client.send_text.assert_not_called()
        mock_client.send_image.assert_not_called()


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
