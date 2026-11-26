# workLog OpenCode GUI

适配目标为 OpenCode Desktop `2.4.1`。Dashboard 和项目侧栏使用本机 `~/.agents/skills/worklog/` 下的共享页面、配置和操作桥。项目侧栏随 OpenCode 内置服务的本机会话定位项目。

## 构建候选包

在代码仓根目录运行 `npm pack --ignore-scripts ./gui/opencode`。生成的 `worklog-opencode-gui-0.1.0.tgz` 可离线解包。该包只有 GUI 适配器。先安装包含共享前端的 workLog CLI／Skill `v0.6.0`，再安装 GUI。

## 安装

1. 验证已安装 Skill 的共享资源及兼容性。

   ```sh
   root="$HOME/.agents/skills/worklog"
   for surface in dashboard project; do
     python3 -B "$root/scripts/panel_assets.py" --skill "$root" --surface "$surface" >/dev/null || exit 1
   done
   ```

   检查失败时更新 Skill 后重试。缺少资源、摘要不匹配和契约版本不受支持都会使页面停止加载。
2. 将 `.tgz` 解包到固定位置，保留包内 `package/` 根目录。
3. 在 OpenCode 的用户级 `opencode.json` 或 `opencode.jsonc` 的 `plugins` 数组中追加解包后的 `package/` **绝对路径**。保留已有配置。

   ```json
   { "plugins": ["/absolute/path/to/extracted/package"] }
   ```

4. 重启 OpenCode Desktop。点击标题栏中的 **workLog Dashboard**，也可以通过命令面板打开。聊天内从右侧栏的「+」菜单选择 **workLog 项目侧栏**。

卸载时从 `plugins` 删除该路径并重启。任务和记录继续保存在原 workLog 项目中。

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
