# workLog

workLog 把散落在聊天里的推进事项整理成可追踪的任务、待办和进展记录。你和 Agent 照常讨论工作。需要记录时，一句话就能让 Agent 从当前聊天自动提取已确认的事项并写入项目。后续有新进展，也可以直接让 Agent 更新记录，省下逐条建任务、手动记进度和反复交代背景的时间。

## 安装

### CLI 版（通用 Skill）

安装并在聊天中加载 `worklog` Skill。你用自然语言发指令，Agent 调用 CLI 执行。 [CLI／Skill 下载](https://github.com/floohubAI/worklog/releases/tag/v0.4.0) · [Skill 安装说明](docs/skill.md)

### GUI 版（Codex 桌面端）

GUI 版随包提供 CLI 和 Skill，并在 Codex 聊天旁增加只读任务面板。在装有支持本地插件的 Codex 桌面端和 Python 3.9 或更新版本的 Mac 上，运行下面的一行命令。安装后重启 Codex。

```bash
(worklog_installer="$(curl -fsSL https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.1.0/install-codex.sh)" && /bin/bash -c "$worklog_installer")
```

[Codex GUI 发布页](https://github.com/floohubAI/worklog/releases/tag/gui-codex-v0.1.0) · [手动安装](gui/codex/INSTALL.md)

## 三个使用场景

先从项目根目录开启聊天，加载 `worklog` Skill。如果项目是第一次使用 workLog，先说「为当前项目初始化 workLog，项目名用『示例项目』。」

### 1. 新建任务

- **CLI 版**。讨论清楚后，在聊天中说「用 workLog 把刚才确认的订单金额核对建成任务，把核对普通订单和核对零金额订单分别列为待办。」
- **GUI 版**。在 Codex 聊天中发送同样的指令。打开项目侧栏（入口见下方），点「刷新」即可看到新任务。

### 2. 更新任务

- **CLI 版**。有新结果时，说「用 workLog 更新 T001。普通订单合计 5 元，把核对普通订单这条待办标为完成，下一步核对零金额订单。」用 workLog 返回的任务编号替换 `T001`。
- **GUI 版**。在 Codex 聊天中发送同样的更新指令。保存后点侧栏的「刷新」，检查进展、待办和下一步。

### 3. 查看任务

- **CLI 版**。在聊天中说「用 workLog 查看 T001 的进展、未完成的待办和下一步。」还不知道任务编号时，可以先问「当前项目有哪些任务还没结束？」
- **GUI 版**。在本地聊天右侧点「＋ → 更多工具 → 插件和 MCP → workLog 项目侧栏」。可以按状态筛选任务，点开任务查看待办和历史记录。 [打开侧栏](gui/codex/INSTALL.md#打开侧栏)

<img src="docs/images/codex-task-panel.png" alt="Codex 侧栏显示核对订单金额任务的进展、待办和历史" width="452">

*Codex 任务侧栏预览，使用示例数据。*

早期版本的项目需要先备份并升级到 schema 4，才能在侧栏查看。 [备份和升级](docs/context-cli.md#备份和升级)

## 项目特色

- **从聊天里提取要做的事**。Agent 根据上下文把已明确的工作整理成任务和待办，并列出需要确认的问题。
- **按任务追踪进展**。把结果、决定、待办的处理情况和下一步记到对应任务上。之后按任务编号就能查回记录和完成依据。
- **记录保存在项目本地**。每个项目都有独立的 SQLite 数据库，位于项目根目录的 `.worklog/`。CLI 可单独使用，并提供只读接口供其他工具查询。

## 文档

[用户指南](docs/user-guide.md) · [CLI 命令](docs/cli.md) · [只读协议 2.0](docs/read-protocol.md) · [Apache 2.0 许可证](LICENSE)
