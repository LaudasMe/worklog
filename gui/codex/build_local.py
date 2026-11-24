#!/usr/bin/env python3
"""从 workLog 源码构建可独立安装的 Codex GUI 完整包。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import re
import stat
import sys

EXAMPLE = Path(__file__).resolve().parent
REPO = EXAMPLE.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from package_skill import build_package

PLUGIN_FILES = (
    ".codex-plugin/plugin.json", "codex_skill_version.py", "panel.html",
    "project_panel.py", "release-version.json", "server.py", "task_actions.py",
)
RUNTIME_FILES = (
    "worklog_projects.py", "worklog_release.py",
    "worklog_store.py", "worklog_summaries.py",
)


def build_full_package(output_dir, *, candidate_version=None):
    plugin = EXAMPLE / "worklog-panel"
    version = json.loads((plugin / "release-version.json").read_text(encoding="utf-8"))
    plugin_manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if f"gui-codex-v{plugin_manifest['version']}" != version["version"]:
        raise ValueError("插件版本与 Codex GUI 发布版本不一致。")
    if candidate_version is not None:
        if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", candidate_version):
            raise ValueError("本机候选包需要 X.Y.Z 格式的插件版本。")
        plugin_manifest["version"] = candidate_version
        version["version"] = f"gui-codex-v{candidate_version}"
    archive_root = f"worklog-codex-gui-v{plugin_manifest['version']}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{archive_root}-full.zip"
    with TemporaryDirectory(prefix=".worklog-codex-build-", dir=output_dir) as temporary:
        package = build_package(Path(temporary))
        if package.name != f"worklog-skill-{version['bundled_cli_version']}.zip":
            raise ValueError("随包 CLI／Skill 版本与 Codex GUI 发布清单不一致。")
        sources = {
            ".agents/plugins/marketplace.json": EXAMPLE / ".agents/plugins/marketplace.json",
            "INSTALL.md": EXAMPLE / "INSTALL.md",
            "install.py": EXAMPLE / "install.py",
            package.name: package,
        }
        sources.update({f"worklog-panel/{name}": plugin / name for name in PLUGIN_FILES})
        sources.update({f"worklog-panel/runtime/{name}": REPO / name for name in RUNTIME_FILES})
        payload = {name: source.read_bytes() for name, source in sources.items()}
        if candidate_version is not None:
            plugin_manifest["description"] = "在 Codex 中查看本机 workLog 总览和当前项目。"
            plugin_manifest["interface"]["shortDescription"] = "在 Dashboard 和项目侧栏查看和管理任务。"
            for name, manifest in (
                (".codex-plugin/plugin.json", plugin_manifest), ("release-version.json", version),
            ):
                payload[f"worklog-panel/{name}"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
            docs = payload["INSTALL.md"].decode()
            docs = docs.replace("worklog-codex-gui-v0.1.0", archive_root).replace("gui-codex-v0.1.0", version["version"])
            payload["INSTALL.md"] = ("# 本机 Dashboard 候选包\n\n"
                "此包用于本机验收。安装后从 Codex 主侧栏应用列表打开或固定 workLog Dashboard。"
                "聊天右侧可以继续使用 workLog 项目侧栏。\n\n" + docs).encode()
        payload["sha256.json"] = (
            json.dumps({name: sha256(data).hexdigest() for name, data in payload.items()},
                       indent=2, sort_keys=True) + "\n"
        ).encode()
        staged_zip = Path(temporary) / output.name
        with ZipFile(staged_zip, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                member = ZipInfo(f"{archive_root}/{name}")
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | 0o644) << 16
                member.compress_type = ZIP_DEFLATED
                archive.writestr(member, data)
        staged_zip.replace(output)
    return output


def build_release_assets(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".worklog-codex-release-", dir=output_dir) as temporary:
        staging = Path(temporary)
        archive = build_full_package(staging)
        version = archive.name.removeprefix("worklog-codex-gui-").removesuffix("-full.zip")
        digest = sha256(archive.read_bytes()).hexdigest()

        launcher = staging / "install-codex.sh"
        template = (EXAMPLE / launcher.name).read_text(encoding="utf-8")
        launcher.write_text(
            template.replace("@RELEASE_TAG@", f"gui-codex-{version}")
                    .replace("@ARCHIVE_NAME@", archive.name)
                    .replace("@ARCHIVE_SHA256@", digest),
            encoding="utf-8",
        )
        launcher.chmod(0o755)

        checksums = staging / "SHA256SUMS"
        launcher_digest = sha256(launcher.read_bytes()).hexdigest()
        checksums.write_text(
            f"{digest}  {archive.name}\n{launcher_digest}  {launcher.name}\n",
            encoding="utf-8",
        )
        return tuple(asset.replace(output_dir / asset.name) for asset in (archive, launcher, checksums))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO / "dist")
    variant = parser.add_mutually_exclusive_group()
    variant.add_argument("--candidate-version", help="指定本机候选包的插件版本（X.Y.Z）")
    variant.add_argument("--release-assets", action="store_true", help="同时生成下载脚本和 SHA-256 校验文件。")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if args.release_assets:
        for asset in build_release_assets(output_dir):
            print(asset)
    else:
        print(build_full_package(output_dir, candidate_version=args.candidate_version))


if __name__ == "__main__":
    main()
