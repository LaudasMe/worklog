# workLog for DeepSeek Harness

workLog DSH 插件 `0.1.0` 面向 DeepSeek Harness 桌面 `0.2.2-rc.5`。包内附 workLog CLI／Skill 及共用任务面板。运行需要 Python 3.9 或更新版本，并能通过 `python3` 调用。

## 构建和安装

构建需要 Python 和 npm。完整代码仓内运行以下命令，生成自包含的 `worklog-dsh-0.1.0.tgz`。包使用标准 workLog Skill 打包入口，可以离线安装。

```sh
python3 -B plugins/dsh/build.py --output-dir dist
```

验收发布前的 CLI／Skill 版本时，可以在命令末尾附加 `--candidate-cli-version v0.6.0`。正式发布从对应 Git 提交按上面的命令构建。

从 [workLog Releases](https://github.com/floohubAI/worklog/releases) 下载发布版本，或使用本地候选包。退出 DeepSeek Harness，然后用其「管理 dsh 命令」安装的 `dsh` 执行：

```sh
dsh --version
dsh plugin --profile desktop add /absolute/path/worklog-dsh-0.1.0.tgz
```

重启 DeepSeek Harness。「workLog Dashboard」在左侧全局入口，查看当前设备已登记的项目。进入本地会话后，打开右侧栏并从添加面板的选项中选择「workLog 项目侧栏」，查看当前会话目录里的 workLog 项目。切换会话后以新侧栏为准；刚提交的保存可以在原项目读回。

如果项目含自己的同名 Skill，DeepSeek Harness 会先使用项目配置。验收随包 Skill 时使用不含同名 Skill 的合成项目，检查加载的 `SKILL.md` 路径是否在插件包的 `skills/worklog/` 内，并用相邻的 `scripts/worklog.py release-version` 核对 CLI／Skill 版本。

## 隔离验收

准备独立的 DSH 配置目录、共享 Agent 配置目录和 Electron 用户目录。启动测试桌面时使用同一组测试目录，保持日常应用和项目的配置独立。首次启动隔离桌面，按欢迎页引导进入后退出，再向隔离 profile 安装候选包。

在两个合成项目分别创建同号任务，保存一次进展记录用于完成验收。分别从 Dashboard 和两个项目侧栏测试新增、各适用状态变更、手动设置和解除阻塞、完成、设置和清空优先级。每次保存后刷新同一个面板，并运行包内 CLI 的 `context --task` 核对任务 UUID、修订号和结果。一个会话提交后切到另一个会话，再打开原会话核对保存结果。对保存前已变化的任务核对版本冲突提示和原值。

验证安装和页面后，另外在同一合成项目执行 Codex 创建 → DSH 接手并保存 → OpenCode 读回的 CLI 接力。正式发行版本需从发布页重新下载并校验后复验。

## 卸载

退出 DeepSeek Harness，通过 `dsh plugin --profile desktop remove worklog-dsh` 卸载，然后重启。项目下的 workLog 记录仍可使用共同的 CLI 读取。
