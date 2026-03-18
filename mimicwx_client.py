"""MimicWX-Linux HTTP + WebSocket client.

Wraps the MimicWX REST API and provides helpers for the platform adapter.

API reference: https://github.com/PigeonCoders/MimicWX-Linux
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

logger = logging.getLogger("astrbot")


class MimicWXClientError(Exception):
    """Raised when the MimicWX server returns an error or is unreachable."""


# Matches data-URI prefixes like "data:image/png;base64,"
_DATA_URI_RE = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


def strip_base64_prefix(data: str) -> str:
    """Remove any base64 transport prefix, returning raw base64 data.

    Handles:
    - ``data:image/png;base64,<data>``
    - ``base64://<data>``
    - Already-clean base64 (returned unchanged)
    """
    if not data:
        return data
    # Strip data-URI prefix (e.g. "data:image/jpeg;base64,")
    data = _DATA_URI_RE.sub("", data)
    # Strip base64:// prefix
    data = data.removeprefix("base64://")
    return data


class MimicWXClient:
    """Async HTTP client for the MimicWX-Linux REST API.

    Args:
        host: MimicWX server hostname or IP.
        port: MimicWX server port (default 8899).
        token: API bearer token; leave empty to disable authentication.
        timeout: Per-request timeout in seconds (default 30).
    """

    def __init__(
        self,
        host: str,
        port: int = 8899,
        token: str = "",
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        url = f"ws://{self.host}:{self.port}/ws"
        if self.token:
            url += f"?token={self.token}"
        return url

    @property
    def auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession(
                headers=self.auth_headers,
                timeout=self.timeout,
            ) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise MimicWXClientError(
                            f"GET {path} failed with status {resp.status}: {text}"
                        )
                    return await resp.json()
        except aiohttp.ClientConnectorError as exc:
            raise MimicWXClientError(
                f"Connection to MimicWX server {self.host}:{self.port} failed: {exc}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise MimicWXClientError(f"HTTP request failed: {exc}") from exc

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession(
                headers=self.auth_headers,
                timeout=self.timeout,
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise MimicWXClientError(
                            f"POST {path} failed with status {resp.status}: {text}"
                        )
                    return await resp.json()
        except aiohttp.ClientConnectorError as exc:
            raise MimicWXClientError(
                f"Connection to MimicWX server {self.host}:{self.port} failed: {exc}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise MimicWXClientError(f"HTTP request failed: {exc}") from exc

    async def _delete(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession(
                headers=self.auth_headers,
                timeout=self.timeout,
            ) as session:
                async with session.delete(url, json=payload) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise MimicWXClientError(
                            f"DELETE {path} failed with status {resp.status}: {text}"
                        )
                    return await resp.json()
        except aiohttp.ClientConnectorError as exc:
            raise MimicWXClientError(
                f"Connection to MimicWX server {self.host}:{self.port} failed: {exc}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise MimicWXClientError(f"HTTP request failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """GET /status — 服务状态（免认证）。"""
        return await self._get("/status")

    async def get_contacts(self) -> dict[str, Any]:
        """GET /contacts — 联系人列表。"""
        return await self._get("/contacts")

    async def get_sessions(self) -> dict[str, Any]:
        """GET /sessions — 会话列表。"""
        return await self._get("/sessions")

    async def send_text(self, to: str, text: str) -> dict[str, Any]:
        """POST /send — 发送文本消息。

        Args:
            to: 接收方名称或 wxid。
            text: 消息文本内容。

        Raises:
            ValueError: 参数校验失败。
            MimicWXClientError: 服务端错误或网络错误。
        """
        if not to:
            raise ValueError("recipient 'to' must not be empty")
        if not text:
            raise ValueError("text must not be empty")
        return await self._post("/send", {"to": to, "text": text})

    async def send_image(
        self,
        to: str,
        image_b64: str,
        name: str = "image.png",
    ) -> dict[str, Any]:
        """POST /send_image — 发送图片（base64 编码）。

        Args:
            to: 接收方名称或 wxid。
            image_b64: Base64 编码的图片数据（不含 data URI 前缀）。
            name: 文件名（用于推断 MIME 类型）。

        Raises:
            ValueError: 参数校验失败。
            MimicWXClientError: 服务端错误或网络错误。
        """
        if not to:
            raise ValueError("recipient 'to' must not be empty")
        if not image_b64:
            raise ValueError("image data must not be empty")
        # Ensure raw base64 without any data-URI or base64:// prefix
        clean_b64 = strip_base64_prefix(image_b64)
        return await self._post("/send_image", {"to": to, "file": clean_b64, "name": name})

    async def add_listen(self, who: str) -> dict[str, Any]:
        """POST /listen — 添加独立聊天窗口监听。

        Args:
            who: 联系人或群名称。
        """
        if not who:
            raise ValueError("'who' must not be empty")
        return await self._post("/listen", {"who": who})

    async def remove_listen(self, who: str) -> dict[str, Any]:
        """DELETE /listen — 移除独立聊天窗口监听。

        Args:
            who: 联系人或群名称。
        """
        if not who:
            raise ValueError("'who' must not be empty")
        return await self._delete("/listen", {"who": who})

    async def get_listen_list(self) -> dict[str, Any]:
        """GET /listen — 获取当前监听列表。"""
        return await self._get("/listen")

    async def chat_with(self, who: str) -> dict[str, Any]:
        """POST /chat — 切换聊天目标。

        Args:
            who: 联系人或群名称。
        """
        if not who:
            raise ValueError("'who' must not be empty")
        return await self._post("/chat", {"who": who})
