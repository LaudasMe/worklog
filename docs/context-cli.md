# 项目上下文 CLI

在已初始化的项目根目录执行命令。以下 `$CLI` 是仓库的 `worklog.py` 或 Skill 包的 `scripts/worklog.py` 的绝对路径。其他目录可在命令前使用 `--project /绝对路径`。默认路径为当前目录，不搜索父目录。初始输出仍是 [CLI](cli.md) 定义的 JSON 信封。新增业务结果内的 `context_version` 为 `"1.0"`，与外部只读协议版本独立。

## 备份和升级

```sh
python3 -B "$CLI" identity
python3 -B "$CLI" backup --output /absolute/backup/worklog-v1.sqlite3
python3 -B "$CLI" upgrade --backup /absolute/backup/worklog-v1.sqlite3
```

- `backup` 使用 SQLite 在线备份，在临时文件中完成完整性和外键检查，再发布到指定路径。输出目录须已存在，目标文件须不存在。
- `upgrade` 在写事务内验证备份与当前 schema 1 的逻辑数据一致，再增加上下文存储并将内部 schema 升为 2。期间有旧版写入时，重新备份再试。失败会回滚，备份保留。新建项目直接使用 schema 2。
- 升级前新版可以查询旧项目、任务和历史。新版 `record` 在 schema 1 返回 `upgrade_required`。未初始化目录的只读命令返回 `not_initialized`，不创建数据。
- 原项目身份、数据代次、原接手、任务、历史及幂等回执保持一致。升级后使用 v0.2.0 CLI 或 Skill。协议 1.0 的消费者可保持原调用。

## 读取

```sh
python3 -B "$CLI" summaries --type task --status IN_PROGRESS
python3 -B "$CLI" context
python3 -B "$CLI" context --task T001
python3 -B "$CLI" summaries --type record --status OPEN --task T001
python3 -B "$CLI" summaries --type record --status RESOLVED --kind BLOCKER --task T001
python3 -B "$CLI" record-get "$RECORD_ID"
python3 -B "$CLI" entry-get "$ENTRY_ID"
```

将 `RECORD_ID` 和 `ENTRY_ID` 设为查询结果里相应的记录 ID。

- `context` 返回目标 `goal`、已处理范围 `processed_scope`、决定 `decisions`、影响 `impacts`、未决问题 `open_questions`、待办 `todos`、阻塞 `blockers`，另有约束、资料和进展。已关闭的事项由摘要列表和原文继续追溯。缺项列在 `unrecorded`，空数组是已记录的空清单时按字段本身解释。资料或文件依据带 `availability`，`NEEDS_CHECK` 表示当前无法在项目内找到；文件内容应按需打开。
- `impacts.latest` 指向最近一条有效进展，当轮明确无影响时 `value` 是 `[]`，最近一条为旧历史时是 `null`。`impacts.history` 按有效进展中的目标汇总并带各轮依据。`legacy_latest` 提供最近旧进展和交接，全部旧摘要由 `summaries --type record --status RECORDED` 查询。
- 任务读取带 `task`（包括旧工作流 `revision` 和上下文 `context_position`）。`project_context` 带项目约束、决定、资料和关联该任务的项目进展。项目读取带任务摘要 `related_tasks` 及任务级未解除阻塞 `task_blockers`。项目记录的 `scope` 保持 `PROJECT`，任务关联标在 `related_task_id`。
- 所有记录的 `source` 提供命令和 ID。`record-get` 返回保存的 `body_md`、`details`、`refs`、`replaces_id` 及 `superseded_by`。旧原文用 `entry-get`。
- 摘要列表直接返回保存的 `summary`、身份、状态、时间和 `source`，合法条件无匹配时 `items: []`。任务摘要取本任务最近的有效进展或旧交接，缺少时为 `null`。项目关联记录可出现在任务筛选的记录列表。任务状态为 `TODO` 或 `IN_PROGRESS`，`--blocked` 查任务级未解除阻塞。记录状态为 `OPEN`、`RESOLVED`、`CANCELLED`、`CURRENT`、`SUPERSEDED`、`RECORDED`。记录的 `--blocked` 等价于未解除阻塞筛选，可叠加任务条件。默认省略被替换记录。记录支持 `--kind`。
- 先筛选再按更新时间倒序，同一时间按固定 ID 升序。`--limit` 为 1–500，默认 100。下一页沿用筛选条件并传入 `--cursor` 和上一页 `next_cursor`，`null` 表示结束。中途有写入时旧游标返回 `cursor_stale`，可从第一页刷新。

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
- 单条记录必填 `kind`、`summary`、`body_md` 和 `details`，可带 `refs`、`replaces_id`。文本字段须为非空字符串。传入未知字段会报错。

| `kind` | `details` 字段 |
| --- | --- |
| `GOAL` | 可选 `completion_criteria` 字符串数组。同一范围的现行目标须显式替换。 |
| `CONSTRAINT`、`DECISION` | 空对象，至少提供一个 `refs` 依据。 |
| `DOCUMENT` | `path` 项目相对路径、`purpose` 和可选 `locator`。 |
| `PROGRESS` | `processed_scope` 字符串数组、`impacts` 对象数组、可选 `suggested_next_step`。每个影响对象包含 `target_type`（`FILE` 或 `SCOPE`）、`target`、`effect`，可带 `note`。 |
| `QUESTION`、`TODO`、`BLOCKER` | 新建时 `status: "OPEN"`。关闭时显式替换，状态为 `RESOLVED` 或 `CANCELLED` 并提供 `outcome_md` 及至少一个 `refs`。`BLOCKER` 另需 `blocked_work` 和 `unblock_condition`。 |

依据对象通过 `type` 选择 `FILE`（`path`、可选 `locator`）、`RECORD`（`target_record_id` 或同批中前一项的零起点 `batch_index`）、`ENTRY`（`target_entry_id`）、`COMMIT`（`commit_hash`）或 `USER_CONFIRMATION`（`note`）。文件路径须是项目内相对路径。替换保留原范围、归属和类型，用完整版本写入。`record_conflict` 表示现行版本冲突，应先重新读取。

`result` 返回 `operation_id`、`committed_at`、`scope`、`task_id`、`context_position` 及 `records`（每项含 `record_id`、`position`、`kind`、`replaces_id`）。丢失回执时，原请求原样重试便可读回已保存结果。批次的记录、依据和回执同事务提交。上下文回执保存在独立的数据表中，旧协议的快照、旧任务 `revision`、增量和原文仍反映旧工作流。

## 合成项目复跑

从 workLog Git 仓库根目录执行。

```sh
CLI="$(pwd)/worklog.py"
CASE="$(mktemp -d)"
mkdir "$CASE/north" "$CASE/south"
python3 -B examples/context_seed.py "$CASE/north"
python3 -B examples/context_seed.py "$CASE/south" --alternate
(cd "$CASE/north" && python3 -B "$CLI" summaries --type task --status IN_PROGRESS)
(cd "$CASE/south" && python3 -B "$CLI" summaries --type task --status IN_PROGRESS)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

回归测试从 `v0.1.0` 标签取旧版源码建库。两份合成项目各有一个 T001。北辰含带依据的目标、历史决定、复测待办和阻塞，南星用于隔离检查。接续示例见[用户指南](user-guide.md#从当前项目接着做)。
