# AstrBot iMessage Adapter · Photon

![Photon](logo.png)
通过 Photon 官方 Spectrum SDK，让 AstrBot 在 iMessage 中接收消息、调用原有 LLM / 插件并回复。加载本插件后在「消息平台」选择 **iMessage (Photon)**，平台类型为 `photon_imessage`。

Python 端实现 AstrBot 平台接口，Node.js 桥接锁定 `@spectrum-ts/core` 和 `@spectrum-ts/imessage` **12.10.1**。无需自己维护 Mac，也不需要开放 HTTP / Webhook 端口。

插件仅提供 iMessage 平台能力；回复内容交由 AstrBot 已配置的模型与插件处理。平台功能在 WebUI「消息平台 → iMessage (Photon)」配置。插件图标使用 [Photon 官网](https://photon.codes/) 的原始 [Apple touch icon](https://framerusercontent.com/images/USAoq7kC2cVRVRlromUpxkzPw.png)，根目录 `logo.png` 由 AstrBot 自动识别。


## 功能

| 功能 | 行为 |
| --- | --- |
| 私聊、群聊 | 分开识别发送者与聊天；遵循 AstrBot 唤醒词、权限与插件规则 |
| 文本、Markdown | 中文、emoji、原生格式文本；长文本优先在换行处分段 |
| 图片、语音、视频、文件 | 双向映射 `Image / Record / Video / File`；多附件保留顺序 |
| 文件来源 | 本地文件、`file://`、HTTP(S)、`base64://`、base64 data URI |
| 原生引用回复 | 入站引用映射 `Reply`，发送 `Reply` 使用原生线程回复，可默认引用入站消息 |
| Tapback / emoji | `event.react()` 使用原生回应；接收回应默认不触发机器人，可按配置开启 |
| 已读回执 | 接受入站事件后标记**整个聊天**已读；失败不阻断处理 |
| 输入中 | 对接 AstrBot `send_typing / stop_typing` |
| 主动消息 | 保存完整 UMO 后可使用 `context.send_message()`，重启后仍能恢复路由 |
| 多线路 / 多实例 | 按入站服务号码回复，各实例独立维护数据和子进程 |
| 群内独立上下文 | 尊重 AstrBot `unique_session`，实际回复仍发往原群 |
| 链接预览 | 单独发送 HTTP(S) 链接时生成原生预览，可关闭 |
| 转发、At、JSON、位置、音乐 | 转发展开为文本 / 附件；At 为文本标记；位置转 Apple Maps 链接 |
| 联系人与 App 卡片 | 入站卡片转可读文本；插件接口支持发送 vCard、App 卡片和机器人名片 |
| 特效、投票 | 插件接口支持原生消息特效、2–10 选项投票 |
| 编辑、撤回 | 编辑 / 撤回**机器人发送的**消息，以及撤销自己的回应 |
| 群管理 | 查询群名 / 成员、改群名、加人 / 移人、离群、设置 / 清除头像 |
| 聊天背景 | 插件接口支持设置 / 清除背景 |
| 稳定性与运维 | 连接与令牌由 SDK 管理；子进程退避重启、持久化去重、有界队列、运行计数、附件限制与过期清理 |

**真实流式传输未声明支持。** `send_streaming()` 会汇总增量，保留媒体和 Markdown 设置，再发送普通消息并清理输入状态，不通过反复编辑模拟流式输出。

## 安装

需要 **AstrBot ≥ 4.28.2、Python ≥ 3.12、Node.js ≥ 22（含 npm）**。Node 必须安装在运行 AstrBot 的同一个环境；宿主机有 Node 不代表 Docker 容器也有。

1. 在 [Photon 控制台](https://app.photon.codes/) 创建项目，获取 Project ID、Project Secret 和机器人号码。
2. 将本仓库放到 AstrBot 的 `data/plugins/astrbot_imessage_adapter`，或从插件管理页导入 ZIP。
3. 加载 / 重载插件，在「消息平台」添加 **iMessage (Photon)**。
4. 填写 `project_id` 和 `project_secret`。共享线路填写 `bot_number` 为 Photon 分配的完整号码，例如 `+15551234567`，**不是你自己的发送号码**。专用线路按 SDK 提供的服务号码识别机器人。
5. 启用平台。默认 `auto_install=true` 会使用锁文件执行 `npm ci` 和 TypeScript 构建，不会自动安装 Node。多实例通过安装锁避免重复安装冲突。
6. 从 iPhone / Mac 向机器人号码发送**蓝色 iMessage**。共享线路接收者需先注册为 Photon 项目用户，具体权限由你的套餐决定。

也可以在运行 AstrBot 的环境中设置 `PHOTON_PROJECT_ID`、`PHOTON_PROJECT_SECRET`，界面对应字段留空。

如需提前安装或使用只读插件目录，在插件目录执行：

```sh
python scripts/setup_bridge.py
```

或者手动执行：

```sh
cd sidecar
npm ci
npm run build
```

之后关闭 `auto_install`。保留 `node_modules` 和 `dist/index.js`；升级插件后重新构建。Node 不在 PATH 时设置 `node_path` 为绝对路径。

Docker 用户可参考 [examples/Dockerfile](examples/Dockerfile)，在现有 **Debian 系** AstrBot 镜像中加入 Node，并保留原启动方式与数据卷。Alpine 需安装对应发行版的 Node 包。插件目录需可写，或先构建侧车再关闭自动安装。

## 配置

WebUI 提供全部字段的说明；配置示例见 [examples/platform.json](examples/platform.json)。

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `mark_read` | `true` | 接收成功后发送已读回执 |
| `typing_indicator` | `true` | 生成回复时显示输入中 |
| `reply_to_message` | `false` | 默认引用回复；显式 `Reply` 总是优先 |
| `allow_groups` | `true` | 是否允许群聊 |
| `allowed_senders` | `[]` | 入站发送者白名单，完整号码 / 邮箱；空表示不限 |
| `allowed_chats` | `[]` | 原始 chat GUID 白名单，限制入站及适配器消息链发送 |
| `receive_reactions` | `false` | 将收到的回应作为文本消息送入 AstrBot |
| `markdown` / `link_preview` | `true` | 原生格式与纯链接预览 |
| `max_text_length` | `4000` | 每段文本字符数，100–20000 |
| `max_attachment_mb` | `20` | 接收 / 发送的单附件限制，1–100 MB |
| `media_cache_mb` | `512` | 媒体缓存容量，20–4096 MB，满额时拒绝新附件 |
| `media_retention_hours` | `24` | 媒体保留时间，1–720 小时；每 5 分钟清理过期文件 |
| `rpc_timeout` | `60` | 操作超时秒数，5–300 |
| `startup_timeout` | `120` | Photon 连接超时秒数，10–600 |

附件保存在 `data/plugin_data/astrbot_imessage_adapter/<实例摘要>/media/`；需长期保留的文件应复制到调用插件的数据目录。SQLite 仅保存最近七天、最多五万条已接受消息的去重 ID，不保存正文或凭据。这避免常见重放，但不保证崩溃时端到端 exactly-once，也不提供离线消息存档。

## AstrBot 插件接口

事件提供标准消息组件、`get_sender_id()`、`get_group_id()`、`get_self_id()` 和 `unified_msg_origin`。`get_group_id()` 是包含服务号码的编码路由；原始 Photon GUID 在 `event.message_obj.raw_message["chat_id"]` 中。不要把发送者号码当作聊天 GUID。

```python
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Reply

await event.send(MessageChain([Plain("你好")]))
await event.send(
    MessageChain([Reply(id=event.message_obj.message_id), Plain("引用回复")])
)
await event.react("❤️")

# 保存完整 UMO，重启后通过同一个平台实例发送。
umo = event.unified_msg_origin
await context.send_message(umo, MessageChain([Plain("提醒时间到了")]))
```

事件还支持 `mark_read()`、`get_group()`、`edit_message(id, text)`、`unsend_message(id)`。`event.last_sent_ids` 保存最近成功发送的 ID。平台条件使用 `event.get_platform_name() == "photon_imessage"` 判断，普通命令和 ALL 平台监听无需修改。

原生扩展通过 `event.adapter.get_client()` 获取；建立 Photon 连接后可调用：

```python
client = event.adapter.get_client()
route = event.route

ids = await client.send(
    route, [{"type": "text", "text": "生日快乐！"}], effect="confetti"
)
await client.edit(route, ids[0], "生日快乐，祝你一切顺利！")
await client.unsend(route, ids[0])

reaction_id = await client.react(route, event.message_obj.message_id, "👍")
if reaction_id:
    await client.unsend(route, reaction_id)

await client.send_poll(route, "晚饭选哪个？", ["面条", "米饭"])
await client.send_app_card(route, "https://example.com/order", "订单状态", "已经发货")
await client.send_contact(
    route, "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice\r\nTEL:+15551234567\r\nEND:VCARD"
)
await client.share_contact_card(route)

# 以下群管理操作需要群聊路由。
info = await client.group_info(route)
await client.rename_group(route, "新群名")
await client.group_members(route, ["+15551234567"])
await client.group_members(route, ["+15551234567"], remove=True)
await client.set_group_avatar(route, "/absolute/path/icon.png", "image/png")
await client.set_group_avatar(route)  # 清除头像
await client.set_background(route, "/absolute/path/background.jpg", "image/jpeg")
await client.set_background(route)  # 清除背景
# await client.leave_group(route)

# 新建私聊；群创建传多个地址，需要 Photon 支持的专用线路。
dm = await client.create_chat(["+15551234567"], phone="+15559999999")
await client.send(dm, [{"type": "text", "text": "订阅提醒"}])
```

原生接口由插件根据自己的权限和交互规则调用，不自动暴露为全局 LLM 管理工具。`allowed_senders` 只控制入站；扩展客户端由调用插件决定目标和权限。头像 / 背景使用本地文件并应用附件上限。

特效名称：`balloons`, `celebration`, `confetti`, `echo`, `fireworks`, `gentle`, `heart`, `invisible`, `lasers`, `loud`, `slam`, `sparkles`, `spotlight`。特效限文本 / Markdown / 附件，不能与语音、投票、联系人或链接预览混用。

## 能力边界与排错

- 「Photon bridge initialized」表示 SDK 初始化成功，**不代表真实 iMessage 收发已验证**。分别检查手机入站、AstrBot 回复、多媒体与手机显示已读。
- 共享线路限制接收者注册；多专用线路主动创建 / 获取聊天需指定服务号码；群创建与管理还受套餐及 Apple 权限限制。
- 已读粒度为整个聊天；编辑 / 撤回受 Apple 时间与次数限制。不能撤回别人发来的消息。
- 当前 SDK 消息流不会完整映射编辑、撤回、回应删除、贴纸放置等控制事件，也没有通用分页聊天历史接口。联系人 / App 卡片入站转可读文本；投票选择更新不触发机器人。
- 普通 `At / AtAll` 发送为文本提示；收到的原生提及映射为 `At`。不支持的组件显式报错。
- 子进程使用私有 stdio，不监听网络端口；凭据不放命令行。日志不打印 SDK 原始诊断、正文或密钥，额外 SDK 遥测关闭。
- 超时 / 断线时发送可能已到达对方，**不会自动重发**。消息链逐次投递，后续部分失败可能留下已发送部分；`BridgeError.sent_ids` 返回已确认 ID。结果未知时先核实以免重复发送。
- 附件超限、缓存满或下载失败会转为文字占位；其他有效消息段可继续处理。未知音频格式应先转为 `.m4a / .wav / .mp3` 等兼容格式。
- 群消息未触发 LLM 时检查 AstrBot 唤醒设置、白名单和机器人号码；确认蓝色 iMessage 与正确项目的接收者注册。
- `python scripts/setup_bridge.py` 可显示 Node / npm 安装诊断。连接失败时核对凭据、套餐与出口网络；不要把凭据放入 Git。

## 开发与验证

测试使用 AstrBot 官方源码的真实类与注册器，以及已安装的 Photon SDK 内容构造器；仅替代网络操作。Python 测试包含实际子进程 stdio、并发 RPC、部分成功、断线、消息链、路由与本地 HTTP 附件下载。

```sh
git clone --depth 1 --branch v4.28.2 https://github.com/AstrBotDevs/AstrBot.git ../AstrBot
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r ../AstrBot/pyproject.toml
uv pip install --python .venv/bin/python pytest pytest-asyncio ruff
ASTRBOT_SOURCE=/absolute/path/AstrBot .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
cd sidecar
npm ci
npm test
```

Windows 程序位于 `.venv/Scripts/`，使用 PowerShell 设置 `$env:ASTRBOT_SOURCE`。CI 配置覆盖 Windows / Linux、AstrBot 4.28.2 / 4.29.0-beta.1，并编译测试桥接。

本地结果见 [VALIDATION.md](VALIDATION.md)，不替代带 Photon 凭据的真实手机验收。

参考：[AstrBot 平台源码](https://github.com/AstrBotDevs/AstrBot/tree/v4.28.2/astrbot/core/platform)、[Photon iMessage 文档](https://photon.codes/docs/spectrum-ts/providers/imessage)、[Spectrum 官方源码](https://github.com/photon-hq/spectrum-ts)。
