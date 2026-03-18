"""Tests for MimicWXClient (HTTP + WebSocket client).

These tests mock the network layer so they run without a real MimicWX server.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mimicwx_client import MimicWXClient, MimicWXClientError, strip_base64_prefix


# ---------------------------------------------------------------------------
# strip_base64_prefix unit tests
# ---------------------------------------------------------------------------


class TestStripBase64Prefix:
    def test_pure_base64_unchanged(self):
        assert strip_base64_prefix("aGVsbG8=") == "aGVsbG8="

    def test_strip_data_uri_png(self):
        assert strip_base64_prefix("data:image/png;base64,aGVsbG8=") == "aGVsbG8="

    def test_strip_data_uri_jpeg(self):
        assert strip_base64_prefix("data:image/jpeg;base64,aGVsbG8=") == "aGVsbG8="

    def test_strip_data_uri_case_insensitive(self):
        assert strip_base64_prefix("Data:Image/PNG;Base64,aGVsbG8=") == "aGVsbG8="

    def test_strip_base64_protocol(self):
        assert strip_base64_prefix("base64://aGVsbG8=") == "aGVsbG8="

    def test_empty_string(self):
        assert strip_base64_prefix("") == ""

    def test_non_prefixed_data(self):
        raw = "iVBORw0KGgoAAAANSUhEUgAA"
        assert strip_base64_prefix(raw) == raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_json_response(data: dict, status: int = 200):
    """Return a mock aiohttp response that yields JSON."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)
    resp.text = AsyncMock(return_value=json.dumps(data))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# MimicWXClient construction
# ---------------------------------------------------------------------------


class TestMimicWXClientInit:
    def test_default_port(self):
        client = MimicWXClient(host="localhost")
        assert client.host == "localhost"
        assert client.port == 8899

    def test_custom_port_and_token(self):
        client = MimicWXClient(host="192.168.1.100", port=9000, token="secret")
        assert client.port == 9000
        assert client.token == "secret"

    def test_base_url_http(self):
        client = MimicWXClient(host="10.0.0.1", port=8899)
        assert client.base_url == "http://10.0.0.1:8899"

    def test_ws_url(self):
        client = MimicWXClient(host="10.0.0.1", port=8899)
        assert client.ws_url == "ws://10.0.0.1:8899/ws"

    def test_ws_url_with_token(self):
        client = MimicWXClient(host="10.0.0.1", port=8899, token="tok123")
        assert "token=tok123" in client.ws_url

    def test_auth_headers_with_token(self):
        client = MimicWXClient(host="localhost", token="mytoken")
        headers = client.auth_headers
        assert headers.get("Authorization") == "Bearer mytoken"

    def test_auth_headers_without_token(self):
        client = MimicWXClient(host="localhost")
        assert client.auth_headers == {}


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_success(self):
        payload = {"status": "LoggedIn", "version": "0.5.0", "db_available": True}
        client = MimicWXClient(host="localhost")
        with patch("aiohttp.ClientSession.get", return_value=_make_json_response(payload)):
            result = await client.get_status()
        assert result["status"] == "LoggedIn"

    @pytest.mark.asyncio
    async def test_get_status_connection_error(self):
        client = MimicWXClient(host="localhost")
        import aiohttp

        with patch(
            "aiohttp.ClientSession.get",
            side_effect=aiohttp.ClientError("connection refused"),
        ):
            with pytest.raises(MimicWXClientError):
                await client.get_status()


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


class TestSendText:
    @pytest.mark.asyncio
    async def test_send_text_success(self):
        payload = {"sent": True, "verified": True, "message": "ok"}
        client = MimicWXClient(host="localhost")
        with patch("aiohttp.ClientSession.post", return_value=_make_json_response(payload)):
            result = await client.send_text(to="Alice", text="Hello")
        assert result["sent"] is True

    @pytest.mark.asyncio
    async def test_send_text_server_error(self):
        payload = {"error": "WeChat not running"}
        client = MimicWXClient(host="localhost")
        with patch(
            "aiohttp.ClientSession.post",
            return_value=_make_json_response(payload, status=503),
        ):
            with pytest.raises(MimicWXClientError, match="503"):
                await client.send_text(to="Alice", text="Hello")

    @pytest.mark.asyncio
    async def test_send_text_empty_recipient_raises(self):
        client = MimicWXClient(host="localhost")
        with pytest.raises(ValueError, match="recipient"):
            await client.send_text(to="", text="Hello")

    @pytest.mark.asyncio
    async def test_send_text_empty_text_raises(self):
        client = MimicWXClient(host="localhost")
        with pytest.raises(ValueError, match="text"):
            await client.send_text(to="Alice", text="")


# ---------------------------------------------------------------------------
# send_image
# ---------------------------------------------------------------------------


class TestSendImage:
    @pytest.mark.asyncio
    async def test_send_image_success(self):
        payload = {"sent": True, "verified": False, "message": "ok"}
        client = MimicWXClient(host="localhost")
        with patch("aiohttp.ClientSession.post", return_value=_make_json_response(payload)):
            result = await client.send_image(to="Bob", image_b64="aGVsbG8=", name="img.png")
        assert result["sent"] is True

    @pytest.mark.asyncio
    async def test_send_image_empty_recipient_raises(self):
        client = MimicWXClient(host="localhost")
        with pytest.raises(ValueError, match="recipient"):
            await client.send_image(to="", image_b64="aGVsbG8=")

    @pytest.mark.asyncio
    async def test_send_image_empty_data_raises(self):
        client = MimicWXClient(host="localhost")
        with pytest.raises(ValueError, match="image"):
            await client.send_image(to="Bob", image_b64="")

    @pytest.mark.asyncio
    async def test_send_image_strips_data_uri_prefix(self):
        """send_image must strip data:image/...;base64, before POSTing."""
        payload = {"sent": True, "verified": False, "message": "ok"}
        client = MimicWXClient(host="localhost")
        captured_payloads = []

        async def _capture_post(path, payload):
            captured_payloads.append(payload)
            return {"sent": True, "verified": False, "message": "ok"}

        client._post = _capture_post
        await client.send_image(
            to="Bob",
            image_b64="data:image/png;base64,aGVsbG8=",
            name="test.png",
        )
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["file"] == "aGVsbG8="

    @pytest.mark.asyncio
    async def test_send_image_strips_base64_protocol_prefix(self):
        """send_image must strip base64:// before POSTing."""
        client = MimicWXClient(host="localhost")
        captured_payloads = []

        async def _capture_post(path, payload):
            captured_payloads.append(payload)
            return {"sent": True, "verified": False, "message": "ok"}

        client._post = _capture_post
        await client.send_image(
            to="Bob",
            image_b64="base64://aGVsbG8=",
            name="test.png",
        )
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["file"] == "aGVsbG8="


# ---------------------------------------------------------------------------
# add_listen / remove_listen
# ---------------------------------------------------------------------------


class TestListenManagement:
    @pytest.mark.asyncio
    async def test_add_listen_success(self):
        payload = {"success": True, "message": "ok"}
        client = MimicWXClient(host="localhost")
        with patch("aiohttp.ClientSession.post", return_value=_make_json_response(payload)):
            result = await client.add_listen("FileHelper")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_remove_listen_success(self):
        payload = {"success": True, "message": "removed"}
        client = MimicWXClient(host="localhost")
        # DELETE method mock
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_resp = _make_json_response(payload)
        mock_session.delete = MagicMock(return_value=mock_resp)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await client.remove_listen("FileHelper")
        assert result["success"] is True
