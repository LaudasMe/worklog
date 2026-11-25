# Codex 会话侧栏

workLog 在 Codex 本地聊天右侧读取当前项目的未归档任务、当前进展、下一步、上次交接、待办和全部历史。从「检查更新」可查询 GUI 和当前聊天所用 CLI／Skill 的版本。

## 构建完整包

在 workLog 仓库根目录运行以下命令。

```sh
python3 -B gui/codex/build_local.py
```

脚本在 `dist/` 输出 `worklog-codex-gui-v0.2.0-full.zip`，也可用 `--output-dir` 选择目录。完整包包含独立安装器、侧栏、CLI、Skill 及使用说明。安装步骤见 [INSTALL.md](INSTALL.md)。

准备 GitHub Release 附件时，运行以下命令。

```sh
python3 -B gui/codex/build_local.py --release-assets --output-dir dist/gui-codex-v0.2.0
```

该目录包含 `worklog-codex-gui-v0.2.0-full.zip`、`install-codex.sh` 和 `SHA256SUMS`。发布时把三份文件附到同一个 `gui-codex-v0.2.0` Release。下载脚本固定使用该版本和本次生成的 ZIP 校验值。

## 验证

```sh
python3 -B -m unittest discover -s gui/codex -q
python3 -B -m unittest discover -s tests -q
```

GUI 回归覆盖 Dashboard 和项目侧栏的跨项目读取、任务写入与继续任务草稿参数。安装测试覆盖整套替换、恢复以及校验失败，并在能够定位到真实 Codex CLI 时复跑独立安装。测试中的 Codex 配置和 Skill 目录均指向临时用户目录。

用正式 GUI 0.1.1 和 0.2.0 附件复跑实际安装后的回滚。把两个路径替换为对应 ZIP 的绝对路径。

```sh
WORKLOG_ROLLBACK_BASELINE=/path/to/worklog-codex-gui-v0.1.1-full.zip \
WORKLOG_ROLLBACK_CANDIDATE=/path/to/worklog-codex-gui-v0.2.0-full.zip \
python3 -B -m unittest discover -s gui/codex -p 'test_install.py' -k test_real_codex_restores_plugin_state_after_install_failure -v
```

使用 Chromium 验证两个面板的分页、分组、滚动位置和新增任务定位。把下方 Chromium 路径替换成本机可执行文件。

```sh
node gui/codex/test_pagination.mjs /absolute/path/to/chromium gui/codex/worklog-panel/panel.html
```

插件使用 Codex 本地兼容格式。安装器生成 MCP 配置时省略 `cwd`，侧栏由当前聊天对应的工作目录启动。项目定位会查找最近的 workLog 数据根，对已登记的项目核对数据库身份，且遇到当前 Git 仓库或 worktree 的根目录时停止向上查找。代码仓和项目管理目录分开的情况，请从项目管理目录新建本地聊天。

## 首版验收

在 macOS Codex 桌面端验证了完整安装、进展读回，以及 Codex 和 OpenCode 两次交接的摘要与原文。双版本验收中，聊天和侧栏均报告实际加载的 CLI／Skill `v0.4.1`，完整包随附 `v0.4.0`。未加载 Skill 的聊天提示「当前使用版本无法确认」。
