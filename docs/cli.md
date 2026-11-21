# 公共 CLI 与 Skill 调用

## 运行

使用 Python 3.9 或以上版本及仓库源码，或使用 Skill 包的完整 `scripts/` 目录。标准库提供 SQLite 支持。默认使用当前目录，多个客户端也可显式指定同一项目根。`init` 是显式操作，每个实际项目应分别初始化。上下文读写、摘要筛选和旧库备份升级见 [项目上下文 CLI](context-cli.md)。

```sh
WORKLOG_REPO="/path/to/workLog"
PROJECT="$(mktemp -d)"
CLI="$WORKLOG_REPO/worklog.py"
printf '%s\n' '{"name":"北辰合成项目"}' | python3 "$CLI" --project "$PROJECT" init
python3 "$CLI" --project "$PROJECT" identity
python3 "$CLI" --version
```

数据位于项目根下的 `.worklog/worklog.sqlite3`。同一位置用原名称重试 `init` 返回原项目身份。用不同名称会收到 `already_initialized`。目录移动时应移动目录及其完整数据，并核对项目身份。

公开只读命令默认使用 2.0，要求数据库为 schema 4。也可在公开只读子命令前显式传入 `--protocol-version 2.0`。显式请求 1.0 会返回 `invalid_input`。旧库可用 `context` 核对身份及 schema 版本。升级时先用 `backup` 创建一致备份，再用 `upgrade` 执行升级。本地命令直接调用。 [公开只读协议](read-protocol.md) · [旧库升级](context-cli.md#备份和升级)

## 输入和回执

成功和失败都向 stdout 写一行 UTF-8 JSON。写命令从 stdin 读取一个 UTF-8 JSON 对象。程序退出后再消费输出。成功回执的 `protocol_version` 为 `2.0`。写操作的 `result` 保持对应命令的回执结构。帮助和版本开关输出可读文本。

| 命令 | 输入或参数 | 成功结果 `result` |
| --- | --- | --- |
| `init` | `name` | 项目名称和 CLI 声明的公开只读能力。已有旧库仍需先升级到 schema 4 才能公开读取。 |
| `task-create` | 公共写入字段与 `title`。schema 4 可另传 `priority`。 | 变化回执，含新任务 ID、项目内编号和版本 |
| `task-get T001` | 任务编号或内部 `task_id` | 返回任务的基础状态、显示状态、归档标记、优先级、接手及记录引用。完成的任务含 `completion`。 |
| `task-start` | 公共写入字段、`task_id`、`expected_revision` | `result.task` 为 `IN_PROGRESS`。无需接手。 |
| `task-pause` | 与 `task-start` 相同，要求 schema 4 | 将 `IN_PROGRESS` 设为 `PAUSED`，保留已有接手。 |
| `task-resume` | 与 `task-start` 相同，要求 schema 4 | 将 `PAUSED` 恢复为 `IN_PROGRESS`，保留已有接手。 |
| `task-complete` | 与 `task-start` 相同，另加 `completed_scope` 和 `evidence`。可传 `todo_dispositions`。 | `result.task` 为 `DONE`，含 `completion` 和原文引用。详见[开始和完成任务](context-cli.md#开始和完成任务)。 |
| `task-set-priority` | 公共写入字段、`task_id`、`expected_revision`、`priority` | `result.task` 含提交后的优先级和任务版本。详见[设置任务优先级](context-cli.md#设置任务优先级)。 |
| `claim` | 公共写入字段、`task_id`、`expected_revision`、`client`、`session_id` | 变化回执，含当前 `active_run.run_id` |
| `checkpoint` | 公共写入字段、`task_id`、`expected_revision`、`run_id`、`summary`、`body_md`、`next_step` | 变化回执，含更新后的任务与新记录引用 |
| `handoff` | 与 `checkpoint` 相同 | 新交接引用及下一步，当前接手关闭，任务保持交接前的 `IN_PROGRESS` 或 `PAUSED`。 |
| `identity`、`snapshot`、`changes`、`entry-get` | 见[只读协议](read-protocol.md) | 项目身份、快照、增量或原始工作记录 |

公共写入字段是 `operation_id`、`project_id` 和 `data_generation`。操作 ID 用调用方生成的 UUID 或在项目内稳定唯一的字符串。接手时使用本会话的稳定 `session_id`，可以采用原生会话 ID，也可以使用能与原生会话对应的本地标识。任务已有接手且业务上确认要替换时，先读取现状，再随 `claim` 传入 `replaces_run_id`。

样例中的项目身份、代次、任务、接手标识和版本都应替换为当前回执值。

```json
{"operation_id":"order-check-1","project_id":"<project_id>","data_generation":"<data_generation>","title":"核对样例订单金额"}
```

```json
{"operation_id":"order-claim-1","project_id":"<project_id>","data_generation":"<data_generation>","task_id":"<task_id>","expected_revision":1,"client":"codex","session_id":"synthetic-session-a"}
```

```json
{"operation_id":"order-progress-1","project_id":"<project_id>","data_generation":"<data_generation>","task_id":"<task_id>","expected_revision":2,"run_id":"<active run_id>","summary":"普通金额验证通过","body_md":"# 进展\n\n```python\nassert sum([2, 3]) == 5\n```\n","next_step":"验证零金额。"}
```

```json
{"operation_id":"order-handoff-1","project_id":"<project_id>","data_generation":"<data_generation>","task_id":"<task_id>","expected_revision":3,"run_id":"<active run_id>","summary":"交接剩余的边界验证","body_md":"# 交接\n\n普通金额的样例已验证。\n","next_step":"添加一笔金额为 0 的样例，检查合计。"}
```

上述四种写入的回执在 `result` 内给出 `seq`、`change_id`、`kind`、`committed_at`、提交后的 `task` 及新 `entry` 引用。

```json
{"ok":false,"error":{"code":"stale_run","message":"The supplied run is no longer the active task run.","details":{"active_run":null}}}
```

| 退出码 | 判定和调用方行为 |
| --- | --- |
| 0 | 成功，保存原始回执或继续查询 |
| 2 | `invalid_input` 或 `invalid_project_path`，更正参数 |
| 3 | `not_initialized`、`task_not_found` 或 `entry_not_found`，核对项目或对象 |
| 4 | 接手、任务版本、操作、项目身份、增量编号或分页标记冲突。根据 `error.code` 刷新或请用户处理 |
| 5 | `database_busy`，等待后用原请求和原操作 ID 重试 |
| 6 | `unsupported_schema`、`storage_error` 等，保留原请求并排查 |

任务状态操作及相关读取可能返回以下错误。

| `error.code` | 含义与处理 |
| --- | --- |
| `task_state_conflict` | 当前状态不允许请求的操作。用 `context --task` 获取最新状态。 |
| `invalid_evidence` | 依据不存在、归属不符或不是合格的有效记录。定位可用的原文后调整请求。 |
| `task_blocked` | 有未解除的任务级阻塞。先按现有记录流程处理。 |
| `todo_disposition_required` | 仍开放的任务待办需要逐项处置说明。刷新待办并补充说明。 |
| `upgrade_required` | 当前 schema 低于命令要求。使用项目授权的一致备份及显式升级流程。 |
| `cursor_stale` | 公开增量上次读到的编号早于最早接续编号。从新快照开始。本地摘要的分页标记失效时从第一页重新查询。 |
| `cursor_ahead` | 公开增量上次读到的编号高于当前最新编号。先核对身份与数据历史，确认后用新快照恢复。 |

## Skill 可执行的流程

1. 对已选定的 schema 4 项目运行 `identity` 和 `task-get`。读取 `latest_handoff.entry_id` 和需要的历史引用，调用 `entry-get` 取得 Markdown 原文。
2. 发起 `claim`，采用刚读到的任务 ID 和 `revision`。后续每次提交从最近一次成功回执取得新 `revision`，且使用同一接手的 `run_id`。
3. 完成一段工作后发起 `checkpoint`，保留 Markdown、客观结果和具体下一步。需要交接时用 `handoff` 提交交接原文并关闭接手。
4. 回执丢失时用相同命令和相同 JSON 内容重试。命令和字段值相同即可，JSON 的空白或键顺序可变。收到 `stale_run` 时停止本次接手的写入，收到 `revision_conflict` 时读回现状并核对。请求有意改变后使用新的操作 ID。

接手校验、版本校验、去重和数据事务均由公共工具执行。日常开始和完成任务可走[无须接手的流程](context-cli.md#开始和完成任务)。Codex 和 OpenCode 可使用同一个 [workLog Skill 安装包](skill.md)。

## 从不同目录复跑

以下只需要 Python、仓库文件和三个空目录。可在开着的两个客户端里各运行对应一段，也可直接运行下面的本机复跑。每行 JSON 记录了所调用的公开命令、请求、退出码和结果。

```sh
WORKLOG_REPO="/path/to/workLog"
CASE="$(mktemp -d)"
mkdir "$CASE/project" "$CASE/caller-a" "$CASE/caller-b"
(
  cd "$CASE/caller-a" &&
  python3 "$WORKLOG_REPO/examples/relay.py" --project "$CASE/project" start --client codex --session codex-demo
)
(
  cd "$CASE/caller-b" &&
  python3 "$WORKLOG_REPO/examples/relay.py" --project "$CASE/project" read &&
  python3 "$WORKLOG_REPO/examples/relay.py" --project "$CASE/project" resume --client opencode --session opencode-demo
)
python3 "$WORKLOG_REPO/examples/relay.py" --project "$CASE/project" verify
```

复跑使用合成请求正文。示例同时核对公开变更中的共有字段和重复请求的写回执。[合成请求正文](../examples/sample.json)
