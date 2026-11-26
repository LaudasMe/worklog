# workLog OpenCode GUI

OpenCode GUI `0.1.0` 已在 macOS arm64 的 OpenCode Desktop `2.4.2` 验证。Dashboard 和项目侧栏使用本机 `~/.agents/skills/worklog/` 下的共享页面、配置和操作桥。项目侧栏根据本地会话目录定位项目。

## 安装

1. 安装通用 CLI／Skill `v0.6.0` 到 `~/.agents/skills/worklog/`，然后核对版本并验证共享资源。如果通用脚本只识别到 Codex 旧路径，按手动步骤安装到这里。 [手动安装 Skill](https://github.com/floohubAI/worklog/blob/main/docs/user-guide.md#手动安装-skill)

   ```sh
   root="$HOME/.agents/skills/worklog"
   (python3 -B "$root/scripts/worklog.py" release-version &&
     for surface in dashboard project; do
       python3 -B "$root/scripts/panel_assets.py" --skill "$root" --surface "$surface" >/dev/null || exit 1
     done)
   ```

   检查失败时更新 Skill 后重试。缺少资源、摘要不匹配和契约版本不受支持都会使页面停止加载。
2. 下载 `worklog-opencode-gui-0.1.0.tgz` 和同页的 `SHA256SUMS`。在下载目录运行 `shasum -a 256 -c SHA256SUMS`。校验通过后，将 TGZ 解包到固定位置，保留包内的 `package/` 根目录。 [OpenCode GUI 发布页](https://github.com/floohubAI/worklog/releases/tag/gui-opencode-v0.1.0)
3. 在 OpenCode 的用户级配置目录 `~/.config/opencode/` 编辑实际使用的 `opencode.json` 或 `opencode.jsonc`。首次配置时可创建 `opencode.json`。在 `plugins` 数组登记解包后 `package/` 的**绝对路径**，保留其余配置。升级时替换原有 workLog 条目的路径。

   ```json
   { "plugins": ["/absolute/path/to/extracted/package"] }
   ```

4. 重启 OpenCode Desktop。主页没有聊天时，也可以从标题栏打开 **workLog Dashboard**。在本地聊天中，从右侧栏的「+」菜单选择 **workLog 项目侧栏**。

卸载时从 `plugins` 删除该路径并重启。任务和记录继续保存在原 workLog 项目中。

## 构建候选包

在代码仓根目录运行 `npm pack --ignore-scripts ./gui/opencode`。生成的 `worklog-opencode-gui-0.1.0.tgz` 可离线解包。

## 隔离桌面验收

使用测试用户目录和独立 `OPENCODE_DESKTOP_TEST_ROOT`，在其 `config/opencode/opencode.json` 中登记解包的候选包。通过临时用户目录中的 `~/.agents/skills/worklog/` 安装本版 Skill，并使用同一个测试用户目录创建两个合成 workLog 项目。给两个项目各建一个 `T001`，记录其 UUID、修订号和数据代次。

- 关闭所有聊天和草稿，回到主页。点击标题栏中的 Dashboard 入口，核对两个登记项目。记录应用版本、安装包 SHA-256 和实际加载的 Skill 路径。
- 打开两个本机会话。分别在右侧栏的「+」菜单打开 workLog，核对两个面板加载实际共享页面，以及当前侧栏的项目与会话工作目录一致。
- 在 Dashboard 和侧栏验证新增、开始、暂停、恢复、取消、重新打开、设置及解除阻塞、完成、优先级设置及清空。用 CLI 回读任务状态及记录。用外部 CLI 先写入再提交旧页面请求，确认返回版本冲突。
- 在 A 提交写入后迅速切换到 B，确认 B 不显示 A 的回执，也没有改动 B 的同号任务。重新打开 A，并分别用两个项目的 CLI 读回。提交前切换会话时，核对旧页操作已失效。

运行 `npm --prefix gui/opencode test` 可单独验证适配层的消息和绑定行为。

要用候选包复跑 macOS 隔离桌面验收，指定 Skill ZIP、OpenCode GUI `.tgz` 和一个新的证据文件路径。脚本使用独立实例、独立随机端口，并在结束后关闭测试实例和服务。

```sh
python3 -B gui/opencode/test_desktop.py \
  --skill /absolute/path/worklog-skill-v0.6.0.zip \
  --plugin /absolute/path/worklog-opencode-gui-0.1.0.tgz \
  --evidence /absolute/path/opencode-desktop-evidence.json
```
