#!/usr/bin/env python3
"""将预构建 DSH 插件打包成可离线安装的 tgz。"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


PLUGIN = Path(__file__).resolve().parent
ROOT = PLUGIN.parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_skill import build_package


def build_bundle(output_dir, *, candidate_cli_version=None):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".worklog-dsh-", dir=output_dir) as temporary:
        staging = Path(temporary)
        bundle = staging / "package"
        bundle.mkdir()
        for name in ("package.json", "cordis.patch.yml", "host.mjs", "client.js", "README.md"):
            shutil.copyfile(PLUGIN / name, bundle / name)
        skill_zip = build_package(staging, candidate_version=candidate_cli_version)
        with ZipFile(skill_zip) as archive:
            required = {
                "worklog/SKILL.md", "worklog/gui/panel.html", "worklog/gui-resource.json",
                "worklog/scripts/panel_assets.py", "worklog/scripts/panel_bridge.py",
            }
            if required.difference(archive.namelist()):
                raise ValueError("Skill 安装包缺少共享页面或操作桥。请从完整代码构建。")
            archive.extractall(bundle / "skills")
        result = subprocess.run(
            ["npm", "pack", str(bundle), "--ignore-scripts", "--offline",
             "--cache", str(staging / "npm-cache"), "--pack-destination", str(staging), "--json"],
            check=True, capture_output=True, text=True,
        )
        name = json.loads(result.stdout)[0]["filename"]
        package = output_dir / name
        (staging / name).replace(package)
        return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-cli-version")
    args = parser.parse_args()
    print(build_bundle(args.output_dir, candidate_cli_version=args.candidate_cli_version))


if __name__ == "__main__":
    main()
