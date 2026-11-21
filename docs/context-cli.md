# 项目上下文 CLI

在已初始化的项目根目录执行命令。以下 `$CLI` 是仓库的 `worklog.py` 或 Skill 包的 `scripts/worklog.py` 的绝对路径。其他目录可在命令前使用 `--project /绝对路径`。默认路径为当前目录，不搜索父目录。成功回执沿用 CLI 的 JSON 信封。上下文结果内的 `context_version` 为 `"1.0"`，与外层的 `protocol_version: "2.0"` 独立。 [公共 CLI](cli.md)

## 备份和升级

```sh
python3 -B "$CLI" context
python3 -B "$CLI" backup --output /absolute/backup/project-before-upgrade.sqlite3
python3 -B "$CLI" upgrade --backup /absolute/backup/project-before-upgrade.sqlite3
```

- `backup` 使用 SQLite 在线备份，在临时文件中完成完整性和外键检查，再发布到指定路径。输出目录须已存在，目标文件须不存在。
- `upgrade` 在一个写事务内验证备份与当前数据库的逻辑数据一致，将 schema 1、2 或 3 一次升到 4，并检查完整性与外键。备份须来自同一项目，且与当前数据库是不同文件。备份后若有新写入，重新备份再试。提交前失败会回滚，修复后可用同一备份重试。新建项目直接使用 schema 4。
- `context` 和 `summaries` 可用于 schema 1 至 4。用 `context` 的 `result.schema_version` 核对数据库版本。本地原有接力写入仍可用。`record` 需要 schema 2 或以上。`task-start` 和 `task-complete` 需要 schema 3 或以上。公开只读命令要求 schema 4。版本不足时返回 `upgrade_required`。未初始化目录的只读命令返回 `not_initialized`，目录保持原样。
- 升级保留项目身份、数据代次、任务、接手、历史引用、变更编号和幂等回执。旧任务的优先级留空。迁移库从升级前最大变更编号接续读取公开增量，新库的最早接续编号为 0。 [公开只读协议](read-protocol.md)
- 升级成功后，使用支持 schema 4 的当前 CLI 或 Skill 继续操作。

### 从项目登记选择升级

Codex 和 OpenCode 通过同一 CLI 读写 `~/.local/share/worklog/projects.json`。文件独立于 Skill 安装目录和项目数据库。新项目初始化成功后自动登记。旧项目需要显式补登，CLI 会先读取实际项目身份。若初始化后登记失败，项目数据库可以正常使用，修复登记错误后可用补登命令重试。

```sh
python3 -B "$CLI" projects list
python3 -B "$CLI" --project "$PROJECT_ROOT" projects register
python3 -B "$CLI" --project "$NEW_ROOT" projects move --from "$OLD_ROOT" --project-id "$PROJECT_ID"
python3 -B "$CLI" --project "$PROJECT_ROOT" projects remove --project-id "$PROJECT_ID"
```

每条登记保存项目根目录和 `project_id`。列表会指出目录无法读取或身份不符的条目。项目移动后，先核对新目录的身份再更新登记。停用只移除登记，原数据库和历史仍可读取。重新登记同一路径时复用已有条目。

准备升级时，从列表中选定一个可读项目，核对其路径和项目 ID，再对它单独备份和升级。两条命令都在事务内核对用户选中的 ID，防止登记路径指向了另一个项目。

```sh
python3 -B "$CLI" --project "$PROJECT_ROOT" backup --output "$BACKUP_PATH" --project-id "$PROJECT_ID"
python3 -B "$CLI" --project "$PROJECT_ROOT" upgrade --backup "$BACKUP_PATH" --project-id "$PROJECT_ID"
```

### 在隔离目录验证恢复

将备份复制到新建的项目目录。以下命令中的 `OLD_CLI` 是创建该 schema 的旧版 CLI 的绝对路径，`ENTRY_ID` 是待核对的进展或交接 ID。

```sh
RESTORE=$(mktemp -d)
mkdir "$RESTORE/.worklog"
cp /absolute/backup/project-before-upgrade.sqlite3 "$RESTORE/.worklog/worklog.sqlite3"
python3 -B "$OLD_CLI" --project "$RESTORE" identity
python3 -B "$OLD_CLI" --project "$RESTORE" entry-get "$ENTRY_ID"
```

用旧版 CLI 核对备份时点的任务、原文及引用，并用原请求重试以核对回执。schema 2 和 3 可用旧版 `context` 与 `record-get` 核对上下文。恢复的 schema 3 库含有 `DONE` 任务时，用旧版 `context --task T001` 查看完成详情，其中 `T001` 换成实际任务编号。

完成旧版核对后，再用当前 CLI 升级隔离目录中的数据库。之后可用当前 CLI 的 `context --task` 再次核对完成详情。

```sh
python3 -B "$CLI" --project "$RESTORE" upgrade --backup /absolute/backup/project-before-upgrade.sqlite3
```

## 读取

```sh
python3 -B "$CLI" summaries --type task --status IN_PROGRESS
python3 -B "$CLI" summaries --type task --status DONE
python3 -B "$CLI" summaries --type task --view pending --display-status BLOCKED --priority p0
python3 -B "$CLI" summaries --type task --view archived --status DONE --priority p1
python3 -B "$CLI" context
python3 -B "$CLI" context --task T001
python3 -B "$CLI" summaries --type record --status OPEN --task T001
python3 -B "$CLI" summaries --type record --status RESOLVED --kind BLOCKER --task T001
python3 -B "$CLI" record-get <record_id>
python3 -B "$CLI" entry-get <entry_id>
```

- `context` 返回目标 `goal`、已处理范围 `processed_scope`、决定 `decisions`、影响 `impacts`、未决问题 `open_questions`、待办 `todos`、阻塞 `blockers`，另有约束、资料和进展。已关闭的事项由摘要列表和原文继续追溯。缺项列在 `unrecorded`，空数组是已记录的空清单时按字段本身解释。资料或文件依据带 `availability`，`NEEDS_CHECK` 表示当前无法在项目内找到；文件内容应按需打开。
- `impacts.latest` 指向最近一条有效进展，当轮明确无影响时 `value` 是 `[]`，最近一条为旧历史时是 `null`。`impacts.history` 按有效进展中的目标汇总并带各轮依据。`legacy_latest` 提供最近旧进展和交接，schema 4 的任务详情也提供 `entry_refs`，可逐条用 `entry-get` 读取。全部旧摘要由 `summaries --type record --status RECORDED` 查询。
- 任务读取带 `task`（包括旧工作流 `revision` 和上下文 `context_position`）。`project_context` 带项目约束、决定、资料和关联该任务的项目进展。项目读取带任务摘要 `related_tasks` 及任务级未解除阻塞 `task_blockers`。项目记录的 `scope` 保持 `PROJECT`，任务关联标在 `related_task_id`。
- `context --task T001` 可查看 `DONE` 任务的 `result.task.completion`，包括完成时间、范围、原文引用和遗留待办处置。schema 4 的任务详情还提供 `lifecycle_history`、`run_history`、`action_history` 和 `reopen_candidates`。历次完成的范围、待办处置和依据都可从 `lifecycle_history` 的 `task-complete` 记录中回查。`action_history` 提供当前已不生效的待办和阻塞，原文可按 `source` 打开。
- 所有记录的 `source` 提供命令和 ID。`record-get` 返回保存的 `body_md`、`details`、`refs`、`replaces_id` 及 `superseded_by`。schema 4 的进展和交接原文用 `entry-get`。如果需要在升级前核对旧库的进展和交接原文，用支持该 schema 的旧版 CLI 在隔离备份上读取。
- 摘要列表直接返回保存的 `summary`、身份、状态、时间和 `source`，合法条件无匹配时 `items: []`。任务摘要取本任务最近的有效进展或旧交接，缺少时为 `null`。
- 默认任务列表和项目概览显示全部未归档任务，包含 `DONE` 和 `CANCELLED`。`--view pending` 仅显示未归档且显示为 `TODO`、`IN_PROGRESS`、`BLOCKED` 或 `PAUSED` 的任务。`--view archived` 筛选归档任务，`context --task` 仍可按编号或 UUID 读取。
- `--status` 筛选基础状态，`--display-status` 筛选显示状态，`--blocked` 查询生效且未解除的任务级阻塞，`--priority` 筛选明确设置的 `p0`、`p1`、`p2` 或 `p3`。列表范围和这些条件同时应用。任务详情及摘要中的 `priority: null` 表示未设置。
- 项目关联记录可出现在任务筛选的记录列表。记录状态为 `OPEN`、`RESOLVED`、`CANCELLED`、`CURRENT`、`SUPERSEDED`、`RECORDED`。记录的 `--blocked` 等价于开放阻塞记录筛选，可叠加任务条件。默认省略被替换记录。记录支持 `--kind`。
- 先筛选再按更新时间倒序，同一时间按固定 ID 升序。`--limit` 为 1–500，默认 100。下一页沿用筛选条件并传入 `--cursor` 和上一页 `next_cursor`，`null` 表示结束。中途有写入时旧游标返回 `cursor_stale`，可从第一页刷新。

## 开始和完成任务

得到用户明确指示或授权后，使用 `context --task T001` 读取当前的项目身份、任务版本与已保存的进展。以下命令从 stdin 读取一个 JSON 对象，`task_id` 接受任务 UUID 或 `T001` 形式的项目内编号。

`task-start` 将 `TODO` 改为 `IN_PROGRESS`。开始任务无需 `run_id`，不会创建接手。之后可继续使用原有的 `record` 保存日志。

```json
{
  "project_id": "<project_id>",
  "data_generation": "<data_generation>",
  "operation_id": "<本次开始操作的 UUID>",
  "task_id": "T001",
  "expected_revision": 1
}
```

`task-complete` 将已有成果的 `TODO`、`IN_PROGRESS` 或 `PAUSED` 任务改为 `DONE`。沿用开始请求的公共字段，使用本次完成的操作 ID 和最新任务版本，再加入非空的 `completed_scope` 和至少一条已保存的 `evidence`。

```json
{
  "project_id": "<project_id>",
  "data_generation": "<data_generation>",
  "operation_id": "<本次完成操作的 UUID>",
  "task_id": "T001",
  "expected_revision": 2,
  "completed_scope": "普通金额和零金额样本均已复测。",
  "evidence": [{"type": "RECORD", "target_record_id": "<已有进展的 record_id>"}],
  "todo_dispositions": {"<仍开放的待办 record_id>": "旧样本归档另行处理。"}
}
```

- `RECORD` 引用当前有效的任务级 `PROGRESS`，或通过 `related_task_id` 关联本任务的当前有效项目级 `PROGRESS`。`ENTRY` 用 `target_entry_id` 引用同任务的旧进展或交接。原文继续由 `record-get` 或 `entry-get` 读取。
- 有未解除的任务级阻塞时，先按原有记录流程处理。仍开放的任务待办各需一条非空处置说明。用这些待办当前版本的 `record_id` 作 `todo_dispositions` 的键。没有开放待办时可省略该字段。处置说明保存在完成信息里，待办状态由原有记录流程管理。
- 成功回执在 `result.task` 返回新状态及版本。完成时，`completion` 包含 `completed_at`、`completed_scope`、`evidence`（每条为 `{command, id}`）和 `todo_dispositions`（无待办处置时为 `{}`）。已有活跃接手会随完成事务一并关闭。之后可用 `context --task T001` 复查。
- 回执丢失时使用同一操作 ID 和相同请求重试。遇到版本、状态或依据冲突，先刷新任务和记录。修正内容后使用新的操作 ID。错误代码见[公共 CLI](cli.md#输入和回执)。

## 设置任务优先级

schema 4 的 `task-create` 可以附带 `priority`。省略该字段或传入 `null` 时，任务优先级为未设置。创建回执继续返回原字段，可用 `context --task T001` 核对优先级。

`task-set-priority` 使用公共写入字段、`task_id` 和最新的 `expected_revision`，并要求显式传入 `priority`。取值为 `p0`、`p1`、`p2`、`p3` 或 `null`。用 `null` 清空已有档位。

```json
{
  "project_id": "<project_id>",
  "data_generation": "<data_generation>",
  "operation_id": "<本次修改操作的 UUID>",
  "task_id": "T001",
  "expected_revision": 2,
  "priority": "p1"
}
```

成功回执在 `result.task` 返回提交后的优先级和版本。归档、取消归档和重开均保留优先级。

## 取消、归档和重开任务

以下写操作需要 schema 4，沿用开始任务请求的公共字段。用户确认操作后，每次使用最新任务版本和该操作对应的操作 ID。

- `task-cancel` 接受 `TODO`、`IN_PROGRESS` 或 `PAUSED` 任务，须提供非空 `reason`。取消同时关闭活跃接手。开放的待办和阻塞转入历史，仍可读取原记录。
- `task-archive` 和 `task-unarchive` 分别归档和取消归档 `DONE` 或 `CANCELLED` 任务。请求使用公共字段。操作仅改变列表可见性。
- `task-reopen` 接受 `DONE` 或 `CANCELLED` 任务，须提供非空 `reason` 和非空 `evidence` 数组。可使用符合完成规则的日志引用，也可使用 `{"type": "USER_CONFIRMATION", "note": "用户要求复查零金额样本并重开。"}`。重开后恢复可见，任务回到 `TODO`，可新建接手或按完成规则直接完成。
- 重开成功回执的 `result.lifecycle.reopen_candidates` 提供可带回的历史待办或阻塞。取消前的待办和阻塞经显式选择后才能带回。已完成任务重开时仍开放且生效的待办继续生效，完成期间补记的阻塞经显式选择后才能带回。

使用下节的 `record` 请求带回以 `record_id` 标识的事项。在新的任务级 `OPEN` 待办或阻塞中加入 `"carry_forward_id": "<原 record_id>"`，事项类型须与原记录一致。成功后新记录的 `refs` 带原文的读取位置。处理新事项时可继续用 `replaces_id`，原记录保持其历史状态。

## 写回

`record` 从 stdin 读 JSON。必填公共字段如下。示例 JSON 中的标识取本项目查询结果。

```json
{
  "project_id": "<project_id>",
  "data_generation": "<data_generation>",
  "operation_id": "<新请求 UUID>",
  "scope": "TASK",
  "task_id": "<task_id>",
  "expected_revision": 4,
  "expected_context_position": 11,
  "records": [
    {
      "kind": "PROGRESS",
      "summary": "零金额样本合计为 5",
      "body_md": "## 实测\n实际计算 2 + 3 + 0 = 5。\n",
      "details": {
        "processed_scope": ["零金额边界"],
        "impacts": [{"target_type": "FILE", "target": "docs/zero-amount-check.md", "effect": "记录结果"}]
      }
    },
    {
      "kind": "TODO",
      "summary": "样本复测完成",
      "body_md": "完成实际计算。",
      "details": {"status": "RESOLVED", "outcome_md": "结果与期望一致。"},
      "replaces_id": "<原 OPEN 待办 record_id>",
      "refs": [{"type": "RECORD", "batch_index": 0}]
    }
  ]
}
```

- 项目记录使用 `scope: "PROJECT"`，省略任务版本和接手字段。单条记录可用 `related_task_id` 关联现有任务。任务写入使用 `scope: "TASK"`，提供 `task_id`（也可用编号）、`expected_revision` 和 `expected_context_position`；有接手时须传当前 `run_id`，旧接手返回 `stale_run`。可成对提供 `client` 和 `session_id`；有接手时使用它的来源。
- 单条记录必填 `kind`、`summary`、`body_md` 和 `details`，可带 `refs`、`replaces_id`。对重开时提示的任务事项，可使用 `carry_forward_id` 显式带回。除空清单和空 details 对象外，文本须为非空字符串。未知字段会报错。

| `kind` | `details` 字段 |
| --- | --- |
| `GOAL` | 可选 `completion_criteria` 字符串数组。同一范围的现行目标须显式替换。 |
| `CONSTRAINT`、`DECISION` | 空对象，至少提供一个 `refs` 依据。 |
| `DOCUMENT` | `path` 项目相对路径、`purpose` 和可选 `locator`。 |
| `PROGRESS` | `processed_scope` 字符串数组、`impacts` 对象数组、可选 `suggested_next_step`。每个影响对象包含 `target_type`（`FILE` 或 `SCOPE`）、`target`、`effect`，可带 `note`。 |
| `QUESTION`、`TODO`、`BLOCKER` | 新建时 `status: "OPEN"`。关闭时显式替换，状态为 `RESOLVED` 或 `CANCELLED` 并提供 `outcome_md` 及至少一个 `refs`。`BLOCKER` 另需 `blocked_work` 和 `unblock_condition`。 |

依据对象通过 `type` 选择 `FILE`（`path`、可选 `locator`）、`RECORD`（`target_record_id` 或同批中前一项的零起点 `batch_index`）、`ENTRY`（`target_entry_id`）、`COMMIT`（`commit_hash`）或 `USER_CONFIRMATION`（`note`）。文件路径须是项目内相对路径。替换保留原范围、归属和类型，用完整版本写入。`record_conflict` 表示现行版本冲突，应先重新读取。

`result` 返回 `operation_id`、`committed_at`、`scope`、`task_id`、`context_position` 及 `records`（每项含 `record_id`、`position`、`kind`、`replaces_id`）。丢失回执时，原请求原样重试便可读回已保存结果。批次的记录、依据和回执同事务提交。上下文回执保存在独立的数据表中。`record` 推进上下文位置，旧任务 `revision` 和公开增量沿用原有规则。

## 合成项目复跑

```sh
CASE="$(mktemp -d)"
mkdir "$CASE/north" "$CASE/south"
python3 -B examples/context_seed.py "$CASE/north"
python3 -B examples/context_seed.py "$CASE/south" --alternate
(cd "$CASE/north" && python3 -B "$CLI" summaries --type task --status IN_PROGRESS)
(cd "$CASE/south" && python3 -B "$CLI" summaries --type task --status IN_PROGRESS)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

回归测试用公开的 `v0.1.0`、`v0.2.0` 以及仓库历史中的 schema 3 运行源码分别生成旧库，并核对升级和隔离恢复。两份合成项目各有一个 T001。北辰含带依据的目标、历史决定、复测待办和阻塞，南星用于隔离检查。实际新会话复跑见 [Skill 安装和调用](skill.md)。
