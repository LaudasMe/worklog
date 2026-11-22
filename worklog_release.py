"""Read package version metadata and compare official GitHub releases."""

from http.client import HTTPException
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY = "LaudasMe/worklog"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
VERSION_MANIFEST = "release-version.json"
VERSION_PREFIXES = {"cli": "v", "codex": "gui-codex-v"}
VERSION_NUMBER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def parse_version(version, component="cli"):
    if not isinstance(version, str) or len(version) > 128:
        return None
    match = re.fullmatch(VERSION_PREFIXES[component] + VERSION_NUMBER, version)
    return tuple(map(int, match.groups())) if match else None


def read_release_version(manifest_path, component="cli"):
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if parse_version(version, component) is not None else None


def github_releases():
    page = 1
    while True:
        request = Request(
            f"{RELEASES_API}?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "workLog"},
        )
        with urlopen(request, timeout=8) as response:
            releases = json.load(response)
            links = response.headers.get("Link", "")
        if not isinstance(releases, list) or not all(isinstance(item, dict) for item in releases):
            raise ValueError("Invalid GitHub Releases response")
        yield from releases
        if not re.search(r';\s*rel="next"', links):
            return
        page += 1


def latest_release(component):
    best_version = None
    best_tag = None
    for release in github_releases():
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        tag = release.get("tag_name")
        version = parse_version(tag, component)
        if version is not None and (best_version is None or version > best_version):
            best_version = version
            best_tag = tag
    return best_tag


def check_update(component, installed_version):
    installed = parse_version(installed_version, component)
    result = {
        "installed_version": installed_version if installed is not None else None,
        "latest_version": None,
        "status": "unknown",
        "release_url": None,
        "error": None,
    }
    try:
        latest = latest_release(component)
    except HTTPError as error:
        result["error"] = f"GitHub Releases 请求失败（HTTP {error.code}）。"
    except URLError as error:
        result["error"] = f"无法连接 GitHub Releases（{error.reason}）。"
    except (OSError, HTTPException) as error:
        result["error"] = f"读取 GitHub Releases 失败（{error}）。"
    except (ValueError, RecursionError):
        result["error"] = "GitHub Releases 返回了无法读取的数据。"
    else:
        if latest is None:
            result["error"] = "GitHub Releases 上暂无该组件的正式版本。"
        else:
            result["latest_version"] = latest
            result["release_url"] = f"{RELEASES_PAGE}/tag/{latest}"
            if installed is not None:
                remote = parse_version(latest, component)
                if installed < remote:
                    result["status"] = "update_available"
                elif installed == remote:
                    result["status"] = "up_to_date"
                else:
                    result["status"] = "ahead"
    return result
