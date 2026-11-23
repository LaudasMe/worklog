# workLog

workLog 帮你在 Codex 和 OpenCode 之间接着做同一个项目。把任务、待办、进展和下一步记在项目里。换一个聊天或客户端时，打开同一个项目就能接着做。

## 一行安装 Codex 侧栏

在装有支持本地插件的 Codex 桌面端和 Python 3.9 或更新版本的 Mac 上，打开终端运行下面的命令。

```bash
(worklog_installer="$(curl -fsSL https://github.com/LaudasMe/worklog/releases/download/gui-codex-v0.1.0/install-codex.sh)" && /bin/bash -c "$worklog_installer")
```

脚本会下载并校验安装包，安装 Codex 侧栏，随包提供 workLog CLI 和 Skill。安装完成后重启 Codex。 [Codex 发布页](https://github.com/LaudasMe/worklog/releases/tag/gui-codex-v0.1.0)

使用 OpenCode，或只需要在聊天中管理任务，可以单独安装 Skill。 [CLI／Skill 发布页](https://github.com/LaudasMe/worklog/releases/tag/v0.4.0) · [Skill 安装说明](docs/skill.md)

## 快速上手

从要管理的项目根目录开启聊天并加载 `worklog` Skill。在 Codex 中用 Skill 选择器，在 OpenCode 中请 Agent 用内置 `skill` 工具加载。你可以在聊天里试试下面的指令。

1. **建任务**。说「为当前项目初始化 workLog，创建任务『核对订单金额』。」已有 workLog 的项目可以直接说「列出当前项目还没结束的任务」。
2. **记进展**。说「开始 T001。已核对普通订单，下一步核对零金额订单。请保存这条进展。」把示例中的 `T001` 换成 workLog 返回的任务编号。
3. **继续工作**。从同一项目目录开启新会话，加载 Skill 后说「读一下 T001 的进展、待办和下一步，然后继续核对。」

在 Codex 本地聊天右侧，点「＋ → 更多工具 → 插件和 MCP → workLog 项目侧栏」查看任务。升级早期版本时，旧项目需要先备份并升级到 schema 4 才能在侧栏查看。 [打开侧栏](gui/codex/INSTALL.md#打开侧栏) · [备份和升级](docs/context-cli.md#备份和升级)

## 项目特色

- **跨会话交接**。在 Codex 记下做了什么、还要做什么，再从 OpenCode 或新的 Codex 聊天读回交接原文并接手。
- **任务有完整记录**。可管理状态、优先级和待办，保留进展、决定、交接及完成依据，日后可按任务编号查回。
- **在聊天旁查看项目**。Codex 侧栏可查看当前进展、下一步、待办、上次交接和全部历史。通过聊天中的 Skill 更新任务。
- **数据保存在项目本地**。每个项目有独立的 SQLite 数据库，位于项目根目录的 `.worklog/`。CLI 可单独使用，也提供供其他工具读取的只读接口。

## 文档

[用户指南](docs/user-guide.md) · [CLI 命令](docs/cli.md) · [只读协议 2.0](docs/read-protocol.md) · [Apache 2.0 许可证](LICENSE)
