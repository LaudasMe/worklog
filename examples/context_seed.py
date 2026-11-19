#!/usr/bin/env python3
"""Seed an empty synthetic project, using only the public CLI."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def seed(directory, cli, alternate):
    def call(*args, request=None):
        result = subprocess.run(
            [sys.executable, "-B", str(cli), *args], cwd=directory,
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True, check=True,
        )
        return json.loads(result.stdout)

    identity = call("init", request={"name": "南星合成项目" if alternate else "北辰合成项目"})
    common = {key: identity[key] for key in ("project_id", "data_generation")}

    def write(command, operation_id, **fields):
        return call(command, request={**common, "operation_id": operation_id, **fields})["result"]

    def item(kind, summary, details, **fields):
        return {"kind": kind, "summary": summary, "body_md": fields.pop("body_md", f"## {summary}\n"),
                "details": details, **fields}

    def workflow(task):
        return {"task_id": task["task_id"], "expected_revision": task["revision"]}

    title = "核对税率样例" if alternate else "复查零金额边界"
    task = write("task-create", "sample-task", title=title)["task"]
    run = write("claim", "sample-claim", client="fixture", session_id="fixture-before-handoff",
                **workflow(task))["task"]
    original = write("checkpoint", "sample-progress", **workflow(run), run_id=run["active_run"]["run_id"],
                     summary="南星税率基线已核对" if alternate else "北辰普通订单合计为 5",
                     body_md="# 原始验证\n\n```python\nassert 2 + 3 == 5\n```\n",
                     next_step="保留样本用于后续读回。")
    handoff = write("handoff", "sample-handoff", **workflow(original["task"]),
                    run_id=run["active_run"]["run_id"], body_md="# 合成交接\n\n请沿用原证据。\n",
                    summary="南星等待税率复核" if alternate else "北辰普通订单已验证，待复查零金额",
                    next_step="按当前项目中的资料执行复查。")
    result = {**common, "task_id": task["task_id"], "legacy_entry_id": original["entry"]["entry_id"]}
    if alternate:
        return result

    for folder in ("docs", "fixtures"):
        (directory / folder).mkdir(exist_ok=True)
    (directory / "docs/scope.md").write_text("# 样例范围\n\n首期仅验证本地计算和 SQLite 记录。\n", encoding="utf-8")
    (directory / "fixtures/order_amounts.json").write_text(
        json.dumps({"amounts": [2, 3, 0], "expected_total": 5}) + "\n", encoding="utf-8"
    )
    old = write("record", "sample-project", scope="PROJECT", records=[
        item("GOAL", "让新会话接续本地订单复测", {}),
        item("CONSTRAINT", "仅使用本地合成数据", {},
             refs=[{"type": "FILE", "path": "docs/scope.md"}]),
        item("DOCUMENT", "首期范围", {"path": "docs/scope.md", "purpose": "核对当前正式范围"}),
        item("DECISION", "初版决定：SQLite 保存普通订单验证", {}, related_task_id="T001",
             body_md="# 初版决定\n\n首轮验证只核对普通订单。\n",
             refs=[{"type": "ENTRY", "target_entry_id": original["entry"]["entry_id"]}]),
    ])["records"][-1]["record_id"]
    current = write("record", "sample-decision-update", scope="PROJECT", records=[
        item("DECISION", "现行决定：沿用 SQLite，增加零金额回归", {}, related_task_id="T001",
             replaces_id=old, refs=[{"type": "USER_CONFIRMATION", "note": "合成项目已确认增加零金额。"},
                                    {"type": "RECORD", "target_record_id": old}]),
        item("PROGRESS", "项目已准备第二份订单样本",
             {"processed_scope": ["合成边界样本已准备"], "impacts": [
                 {"target_type": "FILE", "target": "fixtures/order_amounts.json", "effect": "准备复测输入"}
             ]}, related_task_id="T001", refs=[{"type": "FILE", "path": "fixtures/order_amounts.json"}]),
    ])["records"][0]["record_id"]
    work = write("record", "sample-task-context", scope="TASK", **workflow(handoff["task"]),
                 expected_context_position=0, records=[
        item("GOAL", "复查零金额订单是否改变合计", {"completion_criteria": ["实际计算结果必须为 5"]}),
        item("PROGRESS", "普通金额基线已记录",
             {"processed_scope": ["2 + 3 合计为 5"], "impacts": [],
              "suggested_next_step": "复查包含零金额的样本。"},
             refs=[{"type": "ENTRY", "target_entry_id": original["entry"]["entry_id"]}]),
        item("QUESTION", "是否在正式说明中单列旧交接检查", {"status": "OPEN"}),
        item("TODO", "运行第二份样本并写下实际结果", {"status": "OPEN"},
             body_md="用 Python 读取 fixtures/order_amounts.json，实际求和并比对期望。"
                     "将验证命令和实测结果写进 docs/zero-amount-check.md。"
                     "保存进展后可完成此待办。\n"),
        item("BLOCKER", "第二份样本尚未经复测", {"status": "OPEN", "blocked_work": "确认零金额边界",
                                              "unblock_condition": "实际运行样本并得到合计 5"}),
    ])["records"]
    return {**result, "old_decision_id": old, "current_decision_id": current,
            "question_id": work[2]["record_id"], "todo_id": work[3]["record_id"],
            "blocker_id": work[4]["record_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Existing project directory without a workLog database")
    parser.add_argument("--cli", type=Path, default=ROOT / "worklog.py")
    parser.add_argument("--alternate", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if (directory / ".worklog").exists():
        parser.error("Use a fresh project directory.")
    print(json.dumps(seed(directory, args.cli.resolve(), args.alternate), ensure_ascii=False))


if __name__ == "__main__":
    main()
