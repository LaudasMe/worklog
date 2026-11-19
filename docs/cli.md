# 公共 CLI 与 Skill 调用

## 运行

在安装 Python 3.9 或以上的本机上，保留同一版本的 `worklog.py`、`worklog_store.py` 和 `schema.sql`。标准库提供 SQLite 支持。创建一个项目目录后，用绝对路径调用 CLI。多个客户端可从各自目录指定同一个项目根。`init` 是显式操作，每个实际项目应分别初始化。

下面的示例用 `mktemp -d` 创建临时目录，并在该目录下创建 `.worklog/worklog.sqlite3`。实际使用时，将 `PROJECT` 设为你已有的项目目录的绝对路径，并把 JSON 中的 `name` 改成自己的项目名称。

```sh
WORKLOG_REPO="/path/to/workLog"
PROJECT="$(mktemp -d)"
CLI="$WORKLOG_REPO/worklog.py"
printf '%s\n' '{"name":"示例项目"}' | python3 "$CLI" --project "$PROJECT" init
python3 "$CLI" --project "$PROJECT" identity
python3 "$CLI" --version
```

数据位于项目根下的 `.worklog/worklog.sqlite3`。同一位置用原名称重试 `init` 返回原项目身份。用不同名称会收到 `already_initialized`。目录移动时应移动目录及其完整数据，并用 `identity` 核对身份。生产备份和恢复需要单独验证。

## 输入和回执

成功和失败都向 stdout 写一行 UTF-8 JSON。写命令从 stdin 读取一个 UTF-8 JSON 对象。程序退出后再消费输出。帮助和版本开关输出可读文本。

| 命令 | 输入或参数 | 成功结果 `result` |
| --- | --- | --- |
| `init` | `name` | 项目名称和只读能力列表 |
| `task-create` | 公共写入字段与 `title` | 变化回执，含新任务 ID、项目内编号和版本 |
| `task-get T001` | 任务编号或内部 `task_id` | 当前任务、接手标识、最新记录及交接引用、下一步 |
| `claim` | 公共写入字段、`task_id`、`expected_revision`、`client`、`session_id` | 变化回执，含当前 `active_run.run_id` |
| `checkpoint` | 公共写入字段、`task_id`、`expected_revision`、`run_id`、`summary`、`body_md`、`next_step` | 变化回执，含更新后的任务与新记录引用 |
| `handoff` | 与 `checkpoint` 相同 | 新交接引用及下一步，当前接手关闭，任务仍为 `IN_PROGRESS` |
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

成功的业务回执在 `result` 内给出 `seq`、`change_id`、`kind`、`committed_at`、提交后的 `task` 及新 `entry` 引用。下方合成流程会输出完整请求和回执。

```json
{"ok":false,"error":{"code":"stale_run","message":"The supplied run is no longer the active task run.","details":{"active_run":null}}}
```

| 退出码 | 判定和调用方行为 |
| --- | --- |
| 0 | 成功，保存原始回执或继续查询 |
| 2 | `invalid_input` 或 `invalid_project_path`，更正参数 |
| 3 | `not_initialized`、`task_not_found` 或 `entry_not_found`，核对项目或对象 |
| 4 | 接手、任务版本、操作、项目身份或游标冲突。根据 `error.code` 刷新或请用户处理 |
| 5 | `database_busy`，等待后用原请求和原操作 ID 重试 |
| 6 | `unsupported_schema`、`storage_error` 等，保留原请求并排查 |

## Skill 可执行的流程

1. 从已选项目位置运行 `identity` 和 `task-get`。读取 `latest_handoff.entry_id` 和需要的历史引用，调用 `entry-get` 取得 Markdown 原文。
2. 发起 `claim`，采用刚读到的任务 ID 和 `revision`。后续每次提交从最近一次成功回执取得新 `revision`，且使用同一接手的 `run_id`。
3. 完成一段工作后发起 `checkpoint`，保留 Markdown、客观结果和具体下一步。需要交接时用 `handoff` 提交交接原文并关闭接手。
4. 回执丢失时用相同命令和相同 JSON 内容重试。命令和字段值相同即可，JSON 的空白或键顺序可变。收到 `stale_run` 时停止本次接手的写入，收到 `revision_conflict` 时读回现状并核对。请求有意改变后使用新的操作 ID。

接手校验、版本校验、去重和数据事务均由公共工具执行。[独立 Skill v0.1](skill.md) 用同一份说明封装上述步骤，已完成 Codex 和 OpenCode 的项目级安装验收。事件钩子及自动收尾属于后续范围。

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

复跑使用 [合成请求正文](../examples/sample.json)。
