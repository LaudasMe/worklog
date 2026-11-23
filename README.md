# workLog

workLog 把散落在聊天里的推进事项整理成可追踪的任务、待办和进展记录。你和 Agent 照常讨论工作。需要记录时，一句话就能让 Agent 从当前聊天自动提取已确认的事项并写入项目。后续有新进展，也可以直接让 Agent 更新记录，省下逐条建任务、手动记进度和反复交代背景的时间。

## 一行安装 Codex 侧栏

在装有支持本地插件的 Codex 桌面端和 Python 3.9 或更新版本的 Mac 上，打开终端运行下面的命令。

```bash
(worklog_installer="$(curl -fsSL https://github.com/LaudasMe/worklog/releases/download/gui-codex-v0.1.0/install-codex.sh)" && /bin/bash -c "$worklog_installer")
```

脚本会下载并校验安装包，安装 Codex 侧栏，随包提供 workLog CLI 和 Skill。安装完成后重启 Codex。 [Codex 发布页](https://github.com/LaudasMe/worklog/releases/tag/gui-codex-v0.1.0)

也可以在 Codex 或 OpenCode 中单独安装 Skill。 [CLI／Skill 发布页](https://github.com/LaudasMe/worklog/releases/tag/v0.4.0) · [Skill 安装说明](docs/skill.md)

## 快速上手

从要管理的项目根目录开启聊天，加载 `worklog` Skill。

1. **首次启用**。说「为当前项目初始化 workLog，项目名称用『示例项目』。」已经启用的项目可以直接开始整理。
2. **从讨论中提取任务**。在聊天里确定了要做的事，就说「用 workLog 把这次聊天里已确定要推进的事项建成任务和待办，记下已经完成的工作和下一步。需要确认的事项先列出来。」
3. **跟踪后续进展**。有新结果时，说「用 workLog 根据刚才的进展更新对应任务，标记已完成的待办，记下结果和下一步。」

在 Codex 本地聊天右侧，点「＋ → 更多工具 → 插件和 MCP → workLog 项目侧栏」查看任务。升级早期版本时，旧项目需要先备份并升级到 schema 4 才能在侧栏查看。 [打开侧栏](gui/codex/INSTALL.md#打开侧栏) · [备份和升级](docs/context-cli.md#备份和升级)

## 项目特色

- **从聊天里提取要做的事**。Agent 根据上下文把已明确的工作整理成任务和待办，并列出需要确认的问题。
- **按任务追踪进展**。把结果、决定、待办的处理情况和下一步记到对应任务上。之后按任务编号就能查回记录和完成依据。
- **在聊天旁查看任务**。Codex 侧栏可以查看当前进展、下一步、待办和历史记录。需要更新时，可以在聊天里交给 Agent。
- **记录保存在项目本地**。每个项目都有独立的 SQLite 数据库，位于项目根目录的 `.worklog/`。CLI 可单独使用，并提供只读接口供其他工具查询。

## 文档

[用户指南](docs/user-guide.md) · [CLI 命令](docs/cli.md) · [只读协议 2.0](docs/read-protocol.md) · [Apache 2.0 许可证](LICENSE)
