#!/usr/bin/env python3
"""Build the offline workLog Skill release ZIP from tracked source files."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import stat
import sys
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZipInfo, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "gui/shared"
sys.path.insert(0, str(SHARED))

from panel_assets import CONFIG_FILE, CONTRACT_VERSION, PANEL_FILE, render_panel

FILES = (
    ("worklog/SKILL.md", "skills/worklog/SKILL.md"),
    ("worklog/INSTALL.md", "docs/skill.md"),
    ("worklog/references/commands.md", "skills/worklog/references/commands.md"),
    ("worklog/scripts/schema.sql", "schema.sql"),
    ("worklog/scripts/schema_context.sql", "schema_context.sql"),
    ("worklog/scripts/schema_lifecycle.sql", "schema_lifecycle.sql"),
    ("worklog/scripts/schema_v4.sql", "schema_v4.sql"),
    ("worklog/scripts/worklog.py", "worklog.py"),
    ("worklog/scripts/worklog_store.py", "worklog_store.py"),
    ("worklog/scripts/worklog_context.py", "worklog_context.py"),
    ("worklog/scripts/worklog_summaries.py", "worklog_summaries.py"),
    ("worklog/scripts/worklog_upgrade.py", "worklog_upgrade.py"),
    ("worklog/scripts/worklog_projects.py", "worklog_projects.py"),
    ("worklog/scripts/worklog_release.py", "worklog_release.py"),
    ("worklog/scripts/release-version.json", "release-version.json"),
    ("worklog/scripts/panel_assets.py", "gui/shared/panel_assets.py"),
    ("worklog/scripts/panel_bridge.py", "gui/shared/panel_bridge.py"),
    ("worklog/scripts/panel_tools.py", "gui/shared/panel_tools.py"),
    ("worklog/scripts/project_panel.py", "gui/shared/project_panel.py"),
    ("worklog/scripts/task_actions.py", "gui/shared/task_actions.py"),
)


def build_package(output_dir, *, candidate_version=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "release-version.json").read_text(encoding="utf-8"))
    release_version = manifest["version"]
    if candidate_version is not None:
        if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", candidate_version):
            raise ValueError("本机 CLI／Skill 候选包需要 vX.Y.Z 格式的版本。")
        manifest["version"] = candidate_version
    panel = (SHARED / "panel.html").read_bytes()
    render_panel(panel, "project")
    config = {"version": CONTRACT_VERSION, "panelSha256": sha256(panel).hexdigest()}
    payload = {member: (ROOT / source).read_bytes() for member, source in FILES}
    payload[f"worklog/{PANEL_FILE.as_posix()}"] = panel
    payload[f"worklog/{CONFIG_FILE.as_posix()}"] = (json.dumps(config, indent=2) + "\n").encode("utf-8")
    if candidate_version is not None:
        payload["worklog/scripts/release-version.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        instructions = payload["worklog/INSTALL.md"].decode("utf-8").replace(release_version, candidate_version)
        instructions = instructions.replace(
            "scripts/package_skill.py --output-dir",
            f"scripts/package_skill.py --candidate-version {candidate_version} --output-dir",
        )
        payload["worklog/INSTALL.md"] = instructions.replace("当前发布版本", "本地候选版本").encode("utf-8")
    destination = output_dir / f"worklog-skill-{manifest['version']}.zip"
    with NamedTemporaryFile(prefix=".worklog-skill-", dir=output_dir, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_STORED) as archive:
            for member, data in payload.items():
                info = ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--candidate-version", help="指定本机 CLI／Skill 候选包版本（vX.Y.Z）")
    args = parser.parse_args()
    print(build_package(args.output_dir.resolve(), candidate_version=args.candidate_version))


if __name__ == "__main__":
    main()
