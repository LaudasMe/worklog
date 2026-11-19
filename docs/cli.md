# 用命令行操作 workLog

如果你平时在 Codex 或 OpenCode 里用自然语言操作，可以先看[用户指南](user-guide.md)。这份说明适合在终端直接运行命令，或者让其他程序调用 workLog。

## 运行

需要 Python 3.9 或以上。把下面的 `WORKLOG_REPO` 换成 workLog 源码目录。如果用的是安装包，也可以直接把 `CLI` 设为安装目录中 `scripts/worklog.py` 的完整路径。

下方的 `mktemp -d` 会新建临时目录。执行 `init` 时，会在这个临时目录下创建 `.worklog/worklog.sqlite3`，项目名叫「示例项目」。**实际使用时，把 `PROJECT` 换成你已有的项目目录的完整路径，并把 JSON 里的 `name` 换成自己的项目名。**

```sh
WORKLOG_REPO="/path/to/worklog"
PROJECT="$(mktemp -d)"
CLI="$WORKLOG_REPO/worklog.py"

printf '%s\n' '{"name":"示例项目"}' | python3 "$CLI" --project "$PROJECT" init
python3 "$CLI" --project "$PROJECT" identity
python3 "$CLI" --version
```

用同一个目录和同一个项目名再次执行 `init`，会返回该项目原有的 ID。若这个目录已经用另一个名字初始化，会返回 `already_initialized`。从其他目录调用时，照样用 `--project` 指定原项目目录。

## 常用命令

`--project` 要放在命令前面。比如查看 T001 时，运行 `python3 "$CLI" --project "$PROJECT" task-get T001`。

| 想做什么 | 命令 | 传入什么、会得到什么 |
| --- | --- | --- |
| 启用项目 | `init` | 传项目名 `name`。返回项目身份和可用的读取命令。 |
| 查看项目 | `identity` | 直接运行。返回项目名称和 ID。 |
| 新建任务 | `task-create` | 传任务标题 `title` 和下文的公共参数。结果里有新任务及其编号。 |
| 查看任务 | `task-get T001` | 也可用任务 ID。返回任务状态、当前接手、最近记录和下一步。 |
| 列出项目里的任务 | `snapshot` | 返回当前任务列表、工作记录的摘要和 ID，以及当时已保存的最后一个变更序号。 |
| 读取原文 | `entry-get <entry_id>` | 传记录 ID。结果中的 `body_md` 是保存时的 Markdown 原文。 |
| 接手 | `claim` | 传公共参数，以及 `task_id`、`expected_revision`、`client`、`session_id`。成功后有当前接手的 `run_id`。 |
| 保存进展 | `checkpoint` | 传公共参数、任务 ID、任务版本、接手 ID，以及摘要 `summary`、正文 `body_md`、下一步 `next_step`。 |
| 交接 | `handoff` | 参数和 `checkpoint` 相同。保存交接说明，并结束当前接手。 |
| 从上次读到的位置继续 | `changes` | 传项目标识和已读到的序号。详细用法见[只读接口说明](read-protocol.md)。 |

## 传入和读取 JSON

保存操作从标准输入读取一个 UTF-8 JSON 对象。成功结果的 `ok` 为 `true`，具体内容在 `result`。帮助和版本命令输出普通文本。

除初始化外，保存操作都要带三个公共参数。

- `operation_id` 是本次操作的唯一编号，可以使用 UUID。重试同一次请求时沿用同一个编号。
- `project_id` 和 `data_generation` 是确认项目和这份数据的两个标记，从 `identity` 等成功结果里复制。

对已有任务执行操作时，`task_id` 是任务的 ID。已有任务的操作使用刚读取的 `revision` 作为 `expected_revision`。保存进展或交接时，使用接手成功时得到的 `run_id`。如果要接替另一会话，核对后在 `claim` 传入它的 `run_id` 作为 `replaces_run_id`。

下面是新建、接手、保存进展和交接的参数示例。先把尖括号里的值和 `expected_revision` 换成自己项目和任务的实际值。JSON 字符串中的换行写成 `\n`。

```json
{"operation_id":"order-create-1","project_id":"<项目 ID>","data_generation":"<数据标记>","title":"核对订单合计"}
```

```json
{"operation_id":"order-claim-1","project_id":"<项目 ID>","data_generation":"<数据标记>","task_id":"<任务 ID>","expected_revision":1,"client":"codex","session_id":"example-session-a"}
```

```json
{"operation_id":"order-progress-1","project_id":"<项目 ID>","data_generation":"<数据标记>","task_id":"<任务 ID>","expected_revision":2,"run_id":"<接手 ID>","summary":"核对了两笔订单","body_md":"# 进展\n\n金额 2 和 3，合计为 5。\n","next_step":"加入一笔零金额订单并重算。"}
```

```json
{"operation_id":"order-handoff-1","project_id":"<项目 ID>","data_generation":"<数据标记>","task_id":"<任务 ID>","expected_revision":3,"run_id":"<接手 ID>","summary":"交接零金额检查","body_md":"# 交接\n\n两笔订单已核对。\n","next_step":"加入一笔零金额订单并重算。"}
```

保存成功后，`result.task` 是更新后的任务，`result.entry` 包含新记录的摘要和 ID（如有）。`seq` 是变更序号，`change_id` 是变更 ID，`kind` 是执行的操作，`committed_at` 是记录的保存时间。下次更新任务时用返回的最新 `revision`。

## 命令失败时

失败时读取 JSON 里的 `error.code`，结合退出码判断下一步。

| 退出码 | 怎么处理 |
| --- | --- |
| 0 | 操作成功。 |
| 2 | 检查输入 JSON、命令参数或项目目录。 |
| 3 | 检查项目是否初始化，以及任务或记录是否存在。 |
| 4 | 核对项目、任务版本或当前接手状态。例如 `stale_run` 表示这次接手已经失效，`revision_conflict` 表示任务已被更新。先重新读取任务。 |
| 5 | `database_busy` 表示数据库正忙。稍后用原操作 ID 和原请求重试。 |
| 6 | 检查数据库格式、文件访问权限和 `error` 里的详细信息。 |

如果命令可能已保存成功，只是没有收到结果，也用相同命令和相同参数重试。内容有变化时，分配一个新的操作 ID。

<a id="从不同目录复跑"></a>

## 在两个目录运行示例

以下命令建立一个临时项目。脚本从两个不同目录调用同一个 CLI，依次保存进展、查看交接、接手并检查结果。把 `WORKLOG_REPO` 换成源码目录。

```sh
WORKLOG_REPO="/path/to/worklog"
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

脚本使用[示例数据](../examples/sample.json)，每行输出包含一条命令的请求、退出码和结果。
