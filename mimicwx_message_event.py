"""MimicWX-Linux message event class.

Wraps AstrMessageEvent with MimicWX-specific send capabilities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain

from mimicwx_client import MimicWXClientError, strip_base64_prefix

if TYPE_CHECKING:
    from mimicwx_client import MimicWXClient

logger = logging.getLogger("astrbot")


class MimicWXMessageEvent(AstrMessageEvent):
    """AstrMessageEvent subclass that carries a reference to the MimicWX client."""

    def __init__(
        self,
        message_str: str,
        message_obj,
        platform_meta,
        session_id: str,
        client: "MimicWXClient",
        recipient: str | None = None,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._client = client
        self._recipient = recipient or session_id

    async def send(self, message: MessageChain) -> None:
        """Send *message* to the WeChat contact via the MimicWX REST API."""
        session_id: str = self.session_id
        recipient: str = self._recipient

        text_parts: list[str] = []
        image_segments: list = []

        for seg in message.chain:
            if isinstance(seg, Comp.Plain):
                if seg.text is not None:
                    text_parts.append(seg.text)
            elif isinstance(seg, Comp.Image):
                image_segments.append(seg)
            else:
                logger.debug(
                    "[MimicWX] 忽略不支持的消息段类型: %s", type(seg).__name__
                )

        if text_parts:
            merged_text = "".join(text_parts)
            try:
                await self._client.send_text(to=recipient, text=merged_text)
                logger.debug(
                    "[MimicWX] 文本消息已发送 → %s: %.60s", recipient, merged_text
                )
            except (MimicWXClientError, ValueError) as exc:
                logger.error("[MimicWX] 发送文本失败 → %s: %s", recipient, exc)

        for img in image_segments:
            try:
                b64 = await img.convert_to_base64()
                b64 = strip_base64_prefix(b64)
                filename = getattr(img, "file", None) or "image.png"
                if filename and "/" in filename:
                    filename = filename.rsplit("/", 1)[-1]
                await self._client.send_image(
                    to=recipient,
                    image_b64=b64,
                    name=filename or "image.png",
                )
                logger.debug("[MimicWX] 图片已发送 → %s", recipient)
            except (MimicWXClientError, ValueError) as exc:
                logger.error("[MimicWX] 发送图片失败 → %s: %s", recipient, exc)

        await super().send(message)
