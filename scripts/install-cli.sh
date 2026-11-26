#!/bin/bash
set -euo pipefail

if ! command -v python3 >/dev/null; then
    printf '%s\n' '安装需要 Python 3.9 或更新版本。' >&2
    exit 1
fi

python3 -B - <<'PYTHON'
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

if sys.version_info < (3, 9):
    sys.exit("安装需要 Python 3.9 或更新版本。")

version = "v0.6.0"
archive_name = f"worklog-skill-{version}.zip"
archive_sha256 = "2f1d8328c1ef3d58c5218f099fc0d0ca5cb17900da0c1544d8cd5cfdd2eebd47"
archive_url = f"https://github.com/floohubAI/worklog/releases/download/{version}/{archive_name}"
user_home = Path.home()
skills_dir = user_home / ".agents/skills"
skill = skills_dir / "worklog"
codex_dir = Path(os.environ.get("CODEX_HOME") or user_home / ".codex").expanduser()
legacy_skill = codex_dir / "skills/worklog"

try:
    skills_dir.mkdir(parents=True, exist_ok=True)
    with (skills_dir / ".worklog-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for installed in (skill, legacy_skill):
            backup = installed.parent / ".worklog-install-previous"
            if os.path.lexists(backup):
                sys.exit(f"发现中断的安装备份：{backup}。请先恢复原安装。")
            if os.path.lexists(installed):
                print(f"已保留现有 CLI／Skill：{installed}")
                sys.exit(0)

        with TemporaryDirectory(prefix=".worklog-install-", dir=skills_dir) as temporary:
            staging = Path(temporary)
            archive = staging / archive_name
            subprocess.run(
                ["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https",
                 "--output", str(archive), archive_url],
                check=True,
            )
            if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_sha256:
                sys.exit("CLI／Skill 安装包的 SHA-256 校验失败。安装已停止。")

            with ZipFile(archive) as package:
                package.extractall(staging)
            (staging / "worklog").rename(skill)
except (OSError, subprocess.CalledProcessError, BadZipFile) as error:
    sys.exit(f"workLog CLI／Skill 安装失败：{error}")

print(f"workLog CLI／Skill {version} 已安装到 {skill}。")
print("请新建聊天并加载 worklog Skill。")
PYTHON
