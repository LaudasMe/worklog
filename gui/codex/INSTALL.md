# 安装 workLog Codex GUI

`gui-codex-v0.3.0` 完整包随附 workLog CLI／Skill `v0.6.0`。在 macOS 上需要 Python 3.9 或以上以及支持插件安装的 Codex 桌面端。

## 本地安装

解压完整包后，在终端进入 `worklog-codex-gui-v0.3.0` 目录运行以下命令。

```sh
python3 -B install.py
```

安装器会通过 Codex CLI 登记并启用本地侧栏。它会从 PATH 或应用安装目录查找 Codex CLI，也可以用 `--codex /absolute/path/to/codex` 指定。安装完成后可以移走解压目录。

Dashboard 和项目侧栏从 `~/.agents/skills/worklog/` 读取同一份 `gui/panel.html` 和 `gui-resource.json`。首次安装会将随包 CLI 和 Skill 放入这个目录。已有兼容版本时可以沿用。现有版本较旧、无法识别或与同版本随包文件有差异时，安装器会询问是否整套替换。共享资源缺失或不兼容时，需同意替换，或先安装兼容的独立 Skill，再运行 GUI 安装器。

如果 Codex 配置目录的 `skills/worklog/` 仍有旧安装，安装器会显示两处路径和版本，询问是否迁移。输入 `y` 后，安装器先校验 `~/.agents/skills/worklog/`，再将旧版移到 Codex 配置目录的 `worklog-skill-backups/` 并打印备份位置，最后启用 GUI。拒绝迁移或结束输入会保留原安装。安装失败时，安装器会恢复原 Skill、插件版本和启用状态，并显示失败步骤。

GUI 的本地插件文件保存在 Codex 配置目录的 `worklog/codex/`，默认配置目录为 `~/.codex`。项目级 Skill 由各项目维护。在不同项目中打开聊天时，通过「查看当前使用的 workLog 版本」可以确认实际选用的 Skill。

## 打开 Dashboard

安装后重启 Codex，从主侧栏的应用列表打开或固定 **workLog 全局总览**（**workLog Dashboard**）。Dashboard 可查看本机已登记的项目和任务，也可与项目侧栏同时使用。

## 打开项目侧栏

1. 从 workLog 项目根目录或其中的子目录新建本地 Codex 聊天。如果界面还处于草稿状态，先发一条消息建立聊天。
2. 在聊天里让 workLog Skill 读取任务，或者保存一条进展。可以先选中 `worklog` Skill，再说「查看 T001 的进展」或「记录 T001 的进展，并保存下一步」。
3. 在右侧选择「＋ → 更多工具 → 插件和 MCP → workLog 项目侧栏」，打开项目侧栏。此前安装的开发验证插件仍显示「workLog 任务列表」。
4. 展开任务查看上次交接、待办和全部历史。点击「刷新」读取聊天所对应项目的最新记录。侧栏支持 schema 4。

侧栏可手动检查两个组件的更新。点击「检查更新」，分别查看 Codex GUI 和当前聊天所用 CLI／Skill 的状态。如果当前聊天没有可确认的已加载版本，侧栏会显示「当前使用版本无法确认」。可以在聊天中先请求「查看当前使用的 workLog 版本」，然后在侧栏重新检查。要安装新版本，请打开对应 GitHub Release，手动下载并运行安装包。
