#!/bin/bash
set -euo pipefail

# The release build fills these values from the completed ZIP.
release_tag='@RELEASE_TAG@'
archive_name='@ARCHIVE_NAME@'
archive_sha256='@ARCHIVE_SHA256@'

if [[ "$archive_sha256" == @* ]]; then
    printf '%s\n' '请执行 GitHub Release 附件中的 install-codex.sh。' >&2
    exit 1
fi
if [[ "$(uname -s)" != Darwin ]]; then
    printf '%s\n' 'workLog Codex 侧栏安装脚本支持 macOS。' >&2
    exit 1
fi
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    printf '%s\n' '安装需要 Python 3.9 或更新版本。' >&2
    exit 1
fi

worklog_tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/worklog-codex-install.XXXXXXXX")"
trap 'rm -rf -- "$worklog_tmpdir"' EXIT

archive="$worklog_tmpdir/$archive_name"
release_url="https://github.com/floohubAI/worklog/releases/download/$release_tag"
curl -fsSL --proto '=https' --proto-redir '=https' --output "$archive" "$release_url/$archive_name"

downloaded_sha256="$(shasum -a 256 "$archive")"
if [[ "${downloaded_sha256%% *}" != "$archive_sha256" ]]; then
    printf '%s\n' 'workLog 完整包 SHA-256 校验失败。安装已停止。' >&2
    exit 1
fi

unzip -q "$archive" -d "$worklog_tmpdir"
python3 -B "$worklog_tmpdir/${archive_name%-full.zip}/install.py" "$@"
