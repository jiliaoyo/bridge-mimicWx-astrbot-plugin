"""bridge-mimicWx-astrbot-plugin — AstrBot plugin entry point.

This plugin registers the MimicWX-Linux platform adapter so that AstrBot
can connect to WeChat via the MimicWX-Linux project's REST/WebSocket API.

Usage
-----
After installing this plugin, add a platform entry of type ``mimicwx`` to
AstrBot's platform configuration::

    - type: mimicwx
      id: mimicwx-0
      enable: true
      mimicwx_host: 192.168.1.100   # Host where MimicWX-Linux is running
      mimicwx_port: 8899
      mimicwx_token: your-secret-token   # Leave empty if no token configured

The adapter will:
1. Probe ``GET /status`` on startup to confirm the server is reachable.
2. Subscribe to ``ws://host:port/ws`` for real-time WeChat messages.
3. Parse each message and feed it into AstrBot's LLM / plugin pipeline.
4. Send replies back via ``POST /send`` (text) or ``POST /send_image`` (image).
"""

from __future__ import annotations

import os
import sys

from astrbot.api.star import Context, Star

# Ensure the plugin directory is on sys.path so that sibling modules
# (mimicwx_platform, mimicwx_client, …) can be found when AstrBot
# loads the plugin via a dotted-path __import__.
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.append(_plugin_dir)

# Importing the platform module causes the @register_platform_adapter
# decorator to run, registering "mimicwx" in AstrBot's platform registry.
import mimicwx_platform  # noqa: F401  (side-effect import)


class MimicWXBridgePlugin(Star):
    """MimicWX-Linux bridge plugin.

    Activating this plugin registers the ``mimicwx`` platform adapter type.
    Configure the adapter in AstrBot's platform settings (type: mimicwx).
    """

    author = "jiliaoyo"
    name = "bridge-mimicWx-astrbot-plugin"

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}

    async def initialize(self) -> None:
        from astrbot.api import logger

        host = self.config.get("mimicwx_host", "localhost")
        port = self.config.get("mimicwx_port", 8899)
        token = self.config.get("mimicwx_token", "")
        reconnect_interval = self.config.get("mimicwx_reconnect_interval", 5)
        max_reconnect = self.config.get("mimicwx_max_reconnect_attempts", 0)

        logger.info(
            "[MimicWX Bridge] 插件已加载 — 'mimicwx' 平台适配器已注册。"
            "当前插件配置: host=%s, port=%s, token=%s, "
            "reconnect_interval=%ss, max_reconnect=%s。"
            "请在 AstrBot 平台配置中添加 type: mimicwx 的平台条目以开始使用。",
            host,
            port,
            "***" if token else "未设置",
            reconnect_interval,
            max_reconnect if max_reconnect > 0 else "无限",
        )

    async def terminate(self) -> None:
        from astrbot.api import logger

        logger.info("[MimicWX Bridge] 插件已卸载。")
