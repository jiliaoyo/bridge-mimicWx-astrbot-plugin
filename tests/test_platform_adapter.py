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

    @pytest.mark.asyncio
    async def test_send_ignores_unsupported_segment(self):
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(return_value={"sent": True})
        mock_client.send_image = AsyncMock(return_value={"sent": True})
        adapter.client = mock_client

        class _UnsupportedSeg:
            pass

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        chain = _FakeMessageChain([Comp.Plain(text="hello"), _UnsupportedSeg()])

        await adapter.send_by_session(session, chain)

        mock_client.send_text.assert_called_once_with(to="wxid_alice", text="hello")
        mock_client.send_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_send_when_recipient_is_raw_chatroom_id(self):
        """If recipient is still xxx@chatroom, adapter must skip outbound send."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(return_value={"sent": True})
        mock_client.send_image = AsyncMock(return_value={"sent": True})
        adapter.client = mock_client

        session = _FakeSession(session_id="24654903245@chatroom", message_type="GroupMessage")
        chain = _FakeMessageChain([Comp.Plain(text="hello group")])

        await adapter.send_by_session(session, chain)

        mock_client.send_text.assert_not_called()
        mock_client.send_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_send_works_when_group_name_is_cached(self):
        """If group display name is cached, outbound should send to that name."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        adapter._session_to_name["24654903245@chatroom"] = "资源专享"

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(return_value={"sent": True})
        adapter.client = mock_client

        session = _FakeSession(session_id="24654903245@chatroom", message_type="GroupMessage")
        chain = _FakeMessageChain([Comp.Plain(text="hello group")])

        await adapter.send_by_session(session, chain)

        mock_client.send_text.assert_called_once_with(to="资源专享", text="hello group")


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
    async def test_send_ignores_unsupported_segment_via_event(self):
        """event.send() should ignore unsupported segments without file errors."""
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        mock_client.send_image = AsyncMock(
            return_value={"sent": True, "verified": False, "message": "ok"}
        )
        event, _ = _make_event(session_id="wxid_alice", mock_client=mock_client)

        class _UnsupportedSeg:
            pass

        chain = _FakeMessageChain([Comp.Plain(text="hello"), _UnsupportedSeg()])
        await event.send(chain)

        mock_client.send_text.assert_called_once_with(to="wxid_alice", text="hello")
        mock_client.send_image.assert_not_called()

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
# send_by_session: Plain text with None / empty text
# ---------------------------------------------------------------------------


class TestSendBySessionTextHandling:
    @pytest.mark.asyncio
    async def test_plain_empty_string_not_filtered(self):
        """Comp.Plain with empty text must not be dropped (only None is filtered out)."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        # Empty string followed by real text: joined result is "hello".
        chain = _FakeMessageChain([Comp.Plain(text=""), Comp.Plain(text="hello")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="wxid_alice", text="hello"
        )

    @pytest.mark.asyncio
    async def test_non_plain_non_image_segment_ignored(self):
        """Segments that are neither Comp.Plain nor Comp.Image must be ignored."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        # Simulate a Quote/unknown segment followed by a Plain segment.
        class _FakeQuote:
            pass

        session = _FakeSession(session_id="wxid_alice", message_type="FriendMessage")
        chain = _FakeMessageChain([_FakeQuote(), Comp.Plain(text="二次测试也完美通过！")])

        await adapter.send_by_session(session, chain)
        mock_client.send_text.assert_called_once_with(
            to="wxid_alice", text="二次测试也完美通过！"
        )


# ---------------------------------------------------------------------------
# _dispatch_message: chat_display_name preferred over sender.nickname
# ---------------------------------------------------------------------------


class TestDispatchMessageDisplayName:
    @pytest.mark.asyncio
    async def test_chat_display_name_preferred_over_nickname(self):
        """chat_display_name should be used for caching when talker_display_name differs."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "local_id": 20,
            "server_id": 200,
            "create_time": 1700000010,
            "content": "Hi",
            "parsed": {"type": "Text", "data": {"text": "Hi"}},
            "msg_type": 1,
            "talker": "wxid_carol",
            # talker_display_name is absent / empty — as described in the issue
            "talker_display_name": "",
            "chat": "wxid_carol",
            "chat_display_name": "Carol",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)
        # chat_display_name "Carol" must win even though talker_display_name is empty
        assert adapter._session_to_name.get("wxid_carol") == "Carol"

    @pytest.mark.asyncio
    async def test_fallback_to_nickname_when_chat_display_name_absent(self):
        """sender.nickname is used when chat_display_name is missing."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "local_id": 21,
            "server_id": 201,
            "create_time": 1700000011,
            "content": "Hi",
            "parsed": {"type": "Text", "data": {"text": "Hi"}},
            "msg_type": 1,
            "talker": "wxid_dave",
            "talker_display_name": "Dave",
            "chat": "wxid_dave",
            # chat_display_name absent
            "is_self": False,
        }
        await adapter._dispatch_message(raw)
        assert adapter._session_to_name.get("wxid_dave") == "Dave"

    @pytest.mark.asyncio
    async def test_ws_alias_display_fields_are_accepted(self):
        """MimicWX WS uses chat_display/talker_display aliases."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        raw = {
            "type": "db_message",
            "local_id": 22,
            "server_id": 202,
            "create_time": 1700000012,
            "content": "Hello",
            "parsed": {"type": "Text", "data": {"text": "Hello"}},
            "msg_type": 1,
            "talker": "wxid_eve",
            "talker_display": "Eve Alias",
            "chat": "wxid_eve",
            "chat_display": "Eve Chat Alias",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)
        assert adapter._session_to_name.get("wxid_eve") == "Eve Chat Alias"

    @pytest.mark.asyncio
    async def test_group_event_send_uses_group_display_name(self):
        """event.send() should use cached group display name as recipient."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)
        adapter.client_self_id = "wxid_bot"

        mock_client = AsyncMock()
        mock_client.send_text = AsyncMock(
            return_value={"sent": True, "verified": True, "message": "ok"}
        )
        adapter.client = mock_client

        raw = {
            "type": "db_message",
            "local_id": 23,
            "server_id": 203,
            "create_time": 1700000013,
            "content": "群里问好",
            "parsed": {"type": "Text", "data": {"text": "群里问好"}},
            "msg_type": 1,
            "talker": "wxid_alice",
            "talker_display": "Alice",
            "chat": "49573410323@chatroom",
            "chat_display": "资源专享",
            "is_self": False,
        }
        await adapter._dispatch_message(raw)
        event = await event_queue.get()

        chain = MessageChain([Comp.Plain(text="收到")])
        await event.send(chain)

        mock_client.send_text.assert_called_once_with(to="资源专享", text="收到")


# ---------------------------------------------------------------------------
# run(): contact preloading
# ---------------------------------------------------------------------------


class TestContactPreloading:
    @pytest.mark.asyncio
    async def test_preload_contacts_populates_session_to_name(self):
        """run() should preload _session_to_name from GET /contacts."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        mock_client = AsyncMock()
        mock_client.get_status = AsyncMock(
            return_value={"status": "ok", "version": "1.0"}
        )
        mock_client.get_contacts = AsyncMock(
            return_value={
                "contacts": [
                    {"username": "wxid_alice", "display_name": "Alice", "nick_name": "alice_nick"},
                    {"username": "wxid_bob", "display_name": "", "nick_name": "Bob"},
                    # display_name == username: should NOT be cached
                    {"username": "wxid_raw", "display_name": "wxid_raw", "nick_name": ""},
                ]
            }
        )
        mock_client.ws_url = "ws://localhost:8899/ws"
        mock_client.auth_headers = {}
        adapter.client = mock_client

        # Stop after preload by having _running turn False right away
        async def fake_ws_loop():
            adapter._running = False

        adapter._ws_loop = fake_ws_loop

        await adapter.run()

        assert adapter._session_to_name.get("wxid_alice") == "Alice"
        # display_name empty → fall back to nick_name
        assert adapter._session_to_name.get("wxid_bob") == "Bob"
        # display_name == username → not cached
        assert "wxid_raw" not in adapter._session_to_name

    @pytest.mark.asyncio
    async def test_preload_contacts_failure_does_not_abort_run(self):
        """A failure in GET /contacts must only log a warning, not stop the adapter."""
        event_queue = asyncio.Queue()
        adapter = MimicWXPlatformAdapter(VALID_CONFIG.copy(), {}, event_queue)

        mock_client = AsyncMock()
        mock_client.get_status = AsyncMock(
            return_value={"status": "ok", "version": "1.0"}
        )
        mock_client.get_contacts = AsyncMock(side_effect=Exception("network error"))
        mock_client.ws_url = "ws://localhost:8899/ws"
        mock_client.auth_headers = {}
        adapter.client = mock_client

        async def fake_ws_loop():
            adapter._running = False

        adapter._ws_loop = fake_ws_loop

        # Should complete without raising
        await adapter.run()
        assert adapter._session_to_name == {}


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
