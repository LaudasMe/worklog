#!/usr/bin/env python3
"""Build the offline workLog Skill release ZIP from tracked source files."""

import argparse
import json
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZipInfo, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]
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
)


def build_package(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    version = json.loads((ROOT / "release-version.json").read_text(encoding="utf-8"))["version"]
    destination = output_dir / f"worklog-skill-{version}.zip"
    with NamedTemporaryFile(prefix=".worklog-skill-", dir=output_dir, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_STORED) as archive:
            for member, source in FILES:
                info = ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, (ROOT / source).read_bytes())
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build_package(args.output_dir.resolve()))


if __name__ == "__main__":
    main()
