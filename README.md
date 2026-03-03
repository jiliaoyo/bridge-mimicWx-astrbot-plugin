# bridge-mimicWx-astrbot-plugin

**AstrBot 插件** — 将 [MimicWX-Linux](https://github.com/PigeonCoders/MimicWX-Linux) 的微信消息桥接到 AstrBot 的 LLM / 插件流水线。

## 背景

AstrBot 通过「反向 WebSocket」接入外部 Bot，该协议（OneBot V11）由大多数 QQ Bot 框架实现，但 MimicWX-Linux 尚未完整实现该协议。
本插件通过直接对接 MimicWX-Linux 自身的 REST + WebSocket API，将微信消息作为 AstrBot 的一个**自定义平台适配器**接入，无需依赖 OneBot V11。

---

## 架构

```
  WeChat ──► MimicWX-Linux ──► (WebSocket ws://host:port/ws)
                                          │
                               bridge-mimicWx-astrbot-plugin
                               (MimicWXPlatformAdapter)
                                          │
                               AstrBot 事件队列
                               (LLM / 插件流水线)
                                          │
                               REST API  POST /send
                               POST /send_image
                                          │
                               MimicWX-Linux ──► WeChat
```

---

## 安装

1. 确认已部署并运行 [MimicWX-Linux](https://github.com/PigeonCoders/MimicWX-Linux)（默认端口 8899）。
2. 将本插件目录放入 AstrBot 的 `data/plugins/` 目录，或通过 AstrBot WebUI 安装。
3. 在 AstrBot 的平台配置中添加以下条目：

```yaml
platform:
  - type: mimicwx
    id: mimicwx-0
    enable: true
    mimicwx_host: 192.168.1.100   # MimicWX-Linux 运行的主机
    mimicwx_port: 8899
    mimicwx_token: your-secret-token   # 与 MimicWX config.toml 中的 token 一致；不启用认证则留空
    mimicwx_reconnect_interval: 5       # 断线重连间隔（秒）
    mimicwx_max_reconnect_attempts: 0   # 0 = 无限重连
```

---

## 支持的消息类型

| 类型 | 接收 | 发送 |
|------|:----:|:----:|
| 文本 | ✅ | ✅ |
| 图片 | ✅（显示为 `[图片]`） | ✅（base64） |
| 语音 | ✅（显示为 `[语音 Xs]`） | ❌ |
| 视频 | ✅（显示为 `[视频]`） | ❌ |
| 表情 | ✅（显示为 `[表情]`） | ❌ |
| 链接/小程序 | ✅（显示标题） | ❌ |
| 系统消息 | 过滤，不处理 | — |

---

## 模块说明

| 文件 | 用途 |
|------|------|
| `main.py` | Star 插件入口，导入平台模块完成注册 |
| `mimicwx_platform.py` | 平台适配器（`@register_platform_adapter("mimicwx", ...)`） |
| `mimicwx_client.py` | MimicWX-Linux HTTP/WebSocket 客户端 |
| `mimicwx_message_parser.py` | DbMessage → AstrBotMessage 转换 |
| `mimicwx_message_event.py` | AstrMessageEvent 子类 |
| `metadata.yaml` | AstrBot 插件元数据 |
| `tests/` | 单元测试（58 个，覆盖客户端、消息解析、平台适配器） |

---

## 开发 / 测试

```bash
pip install pytest pytest-asyncio aiohttp astrbot
python -m pytest tests/ -v
```

---

## License

MIT
