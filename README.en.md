# workLog

[简体中文](README.md)

workLog turns information scattered across your chats with an agent into tasks, to-dos, and progress records you can track. Discuss the work as usual, then ask workLog to pull the agreed action items from the current chat and save them. As work progresses, ask your agent to update the records. This saves time you would spend creating tasks one by one, tracking progress by hand, and repeating context.

## Highlights

- **Find action items in chat.** The agent turns agreed work into tasks and to-dos and lists questions that still need a decision.
- **Track progress by task.** Save results, decisions, updates to to-dos, and next steps under the relevant task. Use the task ID to find its history and evidence of completion later.
- **Keep project data local.** Each project has its own SQLite database in `.worklog/` at the project root. The CLI can run on its own and provides a read-only interface for other tools.
- **Bridge task across Agents.** Record your task context, progress and goal independent of agents. Your tasks could be continued from codex to DSH.

## What's new in GUI 0.2.0 and CLI / Skill v0.5.0

- **Pick up work in a new chat.** Authorize workLog to follow one or more tasks in Codex. A new chat can read saved progress, to-dos, and blockers, then save its own results with their source. Completing the whole task still requires a separate confirmation.
- **Manage blockers and to-dos from either panel.** Set and clear blockers one at a time in the Dashboard or project sidebar. Both show to-do outcomes and their explanations. Recording an outcome for an older to-do preserves the original entry and completion evidence.
- **Browse and filter by page.** The Dashboard shows 20 tasks per page, and the project sidebar shows 12. Changing a filter returns to page one. Adding a task opens the page that contains it.
- **Read history and upgrade.** Open history entries with the mouse or keyboard. If GUI installation fails, the installer restores the previous plugin installation and enabled state. Existing schema 4 projects still work with read-only protocol 2.0.

## Installation

Choose an edition and paste its installation prompt into an agent client that can run commands on your computer.

### CLI edition (general-purpose Skill)

Requires macOS or Linux with Python 3.9 or later. Copy the entire block below into your agent chat.

```text
Please install the workLog CLI and its general-purpose Skill on this computer. Download the official installer below, run it with Bash, and tell me whether the installation succeeded and where it was installed.
https://raw.githubusercontent.com/floohubAI/worklog/main/scripts/install-cli.sh
```

The installer verifies the release package and installs the CLI and Skill to `~/.agents/skills/worklog`. If it finds an existing user-level installation, it keeps that version and shows its location. After installation, start a new chat and load the `worklog` Skill. [CLI and Skill release](https://github.com/floohubAI/worklog/releases/tag/v0.5.0) · [Manual installation and upgrades (Chinese)](docs/skill.md)

### GUI edition (Codex desktop)

Requires a Mac with a version of Codex desktop that supports local plugins and Python 3.9 or later. The GUI edition bundles the CLI and Skill and provides the workLog Dashboard and a project task sidebar in Codex.

Copy the entire block below into your Codex chat.

```text
Please install the workLog Codex GUI on this Mac. Download the official installer below, run it with Bash, and tell me whether the installation succeeded.
https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.2.0/install-codex.sh
```

Restart Codex when the installation finishes. Open **workLog Dashboard** from the app list in the main sidebar, or open the workLog project sidebar on the right of a local project chat. [Codex GUI 0.2.0 release](https://github.com/floohubAI/worklog/releases/tag/gui-codex-v0.2.0) · [Manual installation (Chinese)](docs/user-guide.md#在-macos-上安装-codex-gui)

## Getting started

Start a chat from your project's root directory and load the `worklog` Skill. If this is the first time using workLog in the project, say “Initialize workLog for this project.”

### 1. Create a task

- **CLI edition.** Once you've agreed on the work, say “Use workLog to create a task to verify the order amounts we just discussed. Add one to-do for regular orders and another for zero-amount orders.”
- **GUI edition.** Send the same request in Codex. Open the project sidebar as described below and click **Refresh** (`刷新`) to see the new task.

### 2. Update a task

- **CLI edition.** When you have new results, say “Use workLog to update T001. The zero-amount orders are verified. Next, spot-check the refunds.” Replace `T001` with the task ID that workLog returned.
- **GUI edition.** Click **Continue task** (`继续任务`) in the Dashboard or project sidebar to open an editable chat draft in the project's workLog directory. Send the prefilled draft to authorize that chat to follow the task. Click **Refresh** (`刷新`) after saving an update.

### 3. View a task

- **CLI edition.** Say “Use workLog to show the progress, open to-dos, and next step for T001.” If you don't know the task ID yet, ask “Which tasks are still open in this project?”
- **GUI edition.** In a local Codex chat, open the right sidebar and select **+ → More tools → Plugins and MCP → workLog 项目侧栏** (workLog Project Sidebar). Filter tasks by status and expand a task to see its to-dos and history. [Open the sidebar (Chinese)](docs/user-guide.md#3-在-codex-侧栏查看任务)

## Screenshots

The Dashboard and project sidebar from the Codex GUI 0.2.0 release, shown with sample order-reconciliation data in the Chinese interface.

[![Side-by-side view of the workLog Dashboard task list and pagination and the project sidebar task details and to-dos](docs/images/codex-panels.png)](docs/images/codex-panels.png)

[Original Dashboard screenshot](docs/images/codex-dashboard.png) · [Original project sidebar screenshot](docs/images/codex-task-panel.png)

## Documentation

[User guide (Chinese)](docs/user-guide.md) · [CLI reference (Chinese)](docs/cli.md) · [Read-only protocol 2.0 (Chinese)](docs/read-protocol.md) · [Apache 2.0 license](LICENSE)
