"""MimicWX-Linux message event class.

Wraps AstrMessageEvent with MimicWX-specific send capabilities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

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
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._client = client
