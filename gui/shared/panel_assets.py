#!/usr/bin/env python3
"""读取和验证随 workLog Skill 安装的共享页面。"""

import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re


CONTRACT_VERSION = 1
PANEL_FILE = Path("gui/panel.html")
CONFIG_FILE = Path("gui-resource.json")
UNAVAILABLE = "shared_resources_unavailable"
INCOMPATIBLE = "shared_resources_incompatible"


class SharedResourceError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code

    def response(self):
        return {"code": self.code, "message": str(self)}


def user_skill_path():
    return Path.home() / ".agents/skills/worklog"


class PanelRoot(HTMLParser):
    attributes = None

    def handle_starttag(self, tag, attributes):
        if tag == "html" and self.attributes is None:
            self.attributes = attributes


def render_panel(data, surface):
    if surface not in {"project", "dashboard"}:
        raise SharedResourceError("invalid_input", "未知的 workLog 面板视图。")
    try:
        html = data.decode("utf-8")
    except UnicodeError as error:
        raise SharedResourceError(UNAVAILABLE, "workLog 页面编码损坏。请更新 workLog Skill。") from error
    root = re.match(r"""\s*<!doctype html>\s*<html(?=\s|>)(?:[^>"']|"[^"]*"|'[^']*')*>""", html, re.IGNORECASE)
    parser = PanelRoot()
    if root:
        parser.feed(root[0])
    values = [value for name, value in (parser.attributes or []) if name == "data-surface"]
    markers = list(re.finditer(r"\sdata-surface(?=\s|=|>)", root[0], re.IGNORECASE)) if root else []
    marker = 'data-surface="project"'
    start = markers[0].start() + 1 if len(markers) == 1 else 0
    end = start + len(marker)
    if values != ["project"] or not start or not html.startswith(marker, start) or html[end] not in " \t\r\n\f>":
        raise SharedResourceError(UNAVAILABLE, "workLog 页面缺少有效的视图标记。请更新 workLog Skill。")
    return html[:start] + f'data-surface="{surface}"' + html[end:]


def load_panel_assets(skill, surface):
    try:
        skill = skill.parent.resolve() / skill.name
        panel, config_path = skill / PANEL_FILE, skill / CONFIG_FILE
        if any(path.resolve(strict=True) != path or not path.is_file() for path in (panel, config_path)):
            raise ValueError("共享资源路径无效")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or type(config.get("version")) is not int:
            raise ValueError("共享资源契约版本无效")
        if config["version"] != CONTRACT_VERSION:
            raise SharedResourceError(INCOMPATIBLE, "workLog 共享资源版本不兼容。请更新 workLog Skill。")
        data = panel.read_bytes()
        if config.get("panelSha256") != sha256(data).hexdigest():
            raise ValueError("共享页面摘要不符")
    except (OSError, ValueError, RuntimeError) as error:
        raise SharedResourceError(UNAVAILABLE, "workLog 共享资源缺失或损坏。请更新 workLog Skill。") from error
    return {"html": render_panel(data, surface), "config": config}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--surface", required=True)
    args = parser.parse_args()
    try:
        result = load_panel_assets(args.skill, args.surface)
    except SharedResourceError as error:
        print(json.dumps(error.response()))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
