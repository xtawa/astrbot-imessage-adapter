# 本地验证记录

日期：2026-10-02（Asia/Shanghai）；版本 v1.2.0，仅保留 iMessage 平台能力。

## 已验证

| 检查 | 环境 / 版本 | 结果 |
| --- | --- | --- |
| AstrBot 平台集成、RPC、媒体、会话路由及安装脚本回归测试 | Windows；Python 3.12.6；AstrBot v4.28.2，源码 `3c7adafa1397e182d60b1016bf88759265113c8a` | **54 passed** |
| 同一套集成测试 | Windows；Python 3.12.6；AstrBot v4.29.0-beta.1，源码 `f20fba64c29623d248629f4d4bb9474bf4e745bc` | **54 passed** |
| Photon 内容构造器契约、消息归一化、附件流限额、原生提及、部分发送失败 | Node.js 22.17.0；官方 SDK 12.10.1 | **10 passed** |
| TypeScript 严格编译 | TypeScript 5.9.3 | 通过 |
| 锁文件安装 | `npm ci --no-audit --no-fund` | 通过 |
| 跨平台安装脚本 | `python scripts/setup_bridge.py`；Node 与 npm 位于不同目录 | 通过 |
| Python 静态检查与格式 | Ruff | 通过 |
| 配置文件解析 | 平台示例、配置元数据、npm manifest / lockfile | 通过 |
| 修改中的空白问题 | `git diff --check` | 通过 |

54 个 Python 测试在两版 AstrBot 各执行一次；另有 10 个 Node 测试，共 **64 个不同测试用例，118 次通过执行**。AstrBot 仅有自己的 `audioop` 弃用警告，测试没有失败或跳过。

测试加载官方 AstrBot 类、Star 和平台注册器，不以假 AstrBot 字典代替真实框架。Photon 网络操作使用替身；SDK 内容构造器与 TypeScript 类型来自实际安装的锁定 npm 包。stdio 测试启动真实本地子进程；HTTP 附件测试启动本地服务器，不向真实收件人发送消息。

## 插件范围与图标

- 入口只注册 Photon iMessage 平台，无对话增强处理器、LLM 请求/回复钩子、管理指令或后台搭话任务。
- 移除对话增强代码、配置、入站观察器及相关依赖；`_conf_schema.json` 为空对象。
- 使用 Photon 官方网站原始 180×180 PNG 图标作为 `logo.png`，未重绘或改色；核对 AstrBot 插件管理器自动识别根目录 `logo.png`。
- 安装包检查确保包含图标、平台元数据与桥接运行文件，且不包含已移除的功能模块或其测试。

## 尚未验证

- 未配置 Photon Project ID / Secret，未连接用户的真实 Photon 项目。
- 未进行 iPhone / Mac 的真实入站、回复、多媒体、原生回应或已读显示验证。
- 特效、群管理、投票、头像、背景等原生操作已接入当前 SDK 并通过编译，但没有使用真实账号执行；实际可用性受 Photon 套餐与 Apple 限制影响。
- CI 工作流已提供 Windows / Linux 与两版 AstrBot 的矩阵；远端 GitHub Actions 尚未运行。当前本地验证平台为 Windows。
- Dockerfile 为 Debian 系镜像的 Node 安装示例，未在当前 Windows 环境构建容器。
- 插件图标的文件与加载约定已核对，未启动 AstrBot WebUI 进行浏览器展示验收。

## 手机验收步骤

1. 安装插件并填入 Photon 项目凭据。共享线路填写 Photon 分配的机器人号码，并注册测试接收者。
2. 启用平台，确认 SDK 初始化，检查 WebUI `photon.connected`；这仅证明连接状态。
3. 向机器人发送蓝色 iMessage 文本，确认 AstrBot 收到事件、手机收到回复。
4. 分别发送与回复图片、语音、视频、文件，核对消息顺序与内容。
5. 核对手机显示已读、输入中、引用回复与 Tapback；群聊使用正常唤醒规则。
6. 保存 `unified_msg_origin`，重启适配器，再通过 AstrBot 主动发送 API 验证路由。
7. 在允许操作的测试群中验证群名、成员、头像、投票与背景接口。
