#!/usr/bin/env python3
"""Build a deterministic Baihu AppStore v1 index from apps/*/app.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml


UTC8 = timezone(timedelta(hours=8))
REQUIRED_KEYS = ("spec_version", "id", "name", "version", "author", "category", "last_commit")
INDEX_KEYS = (
    "spec_version",
    "id",
    "name",
    "version",
    "author",
    "category",
    "last_commit",
    "description",
    "icon",
    "homepage",
    "build_opts",
    "schedule",
    "schedule_opts",
    "template",
    "languages",
)


def parse_timestamp(value: Any, source: Path) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{source}: last_commit 不能为空")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{source}: last_commit 不是合法 ISO 8601 时间: {text}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_git_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url


def load_manifest(path: Path, owner: str, repo: str, branch: str) -> tuple[dict[str, Any], datetime]:
    raw = path.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: Manifest 根节点必须是对象")

    missing = [key for key in REQUIRED_KEYS if document.get(key) in (None, "")]
    if missing:
        raise ValueError(f"{path}: 缺少必填字段: {', '.join(missing)}")
    if document["spec_version"] != "v1":
        raise ValueError(f"{path}: 仅支持 spec_version v1")
    if document["id"] != path.parent.name:
        raise ValueError(f"{path}: id 必须与应用目录名一致")

    sources = document.get("sources") or []
    tasks = document.get("tasks") or (document.get("sync_rules") or {}).get("tasks") or []
    env_schema = document.get("env_schema") or []
    scenarios = document.get("scenarios") or []
    for field, value in (
        ("sources", sources),
        ("tasks", tasks),
        ("env_schema", env_schema),
        ("scenarios", scenarios),
    ):
        if not isinstance(value, list):
            raise ValueError(f"{path}: {field} 必须是数组")

    own_repo_url = f"https://github.com/{owner}/{repo}"
    expected_source_path = f"apps/{document['id']}/main"
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"{path}: sources[{index}] 必须是对象")
        if (
            source.get("source_type") == "git"
            and normalize_git_url(source.get("source_url")) == own_repo_url
            and source.get("path") != expected_source_path
        ):
            raise ValueError(
                f"{path}: 本仓库 Git source 必须设置 path: {expected_source_path}，"
                "避免每个应用完整检出整个仓库"
            )

    updated = parse_timestamp(document["last_commit"], path)
    metadata = {key: document[key] for key in INDEX_KEYS if document.get(key) is not None}
    metadata.update(
        {
            "tasks": tasks,
            "tasks_count": len(tasks),
            "env_schema": env_schema,
            "scenarios": scenarios,
            "scenarios_count": len(scenarios),
            "app_dir": path.parent.name,
            "manifest_path": path.relative_to(path.parents[2]).as_posix(),
            "manifest_raw": raw,
            "manifest_url": (
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
                f"apps/{path.parent.name}/{path.name}"
            ),
            "updated_at": updated.astimezone(UTC8).strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    return metadata, updated


def build_index(root: Path, owner: str, repo: str, branch: str) -> dict[str, Any]:
    apps_root = root / "apps"
    manifests = sorted(apps_root.glob("*/app.yaml"))
    if not manifests:
        raise ValueError(f"{apps_root}: 未找到任何 app.yaml")

    apps: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    ids: set[str] = set()
    for manifest in manifests:
        metadata, updated = load_manifest(manifest, owner, repo, branch)
        if metadata["id"] in ids:
            raise ValueError(f"重复应用 ID: {metadata['id']}")
        ids.add(metadata["id"])
        apps.append(metadata)
        timestamps.append(updated)

    apps.sort(key=lambda item: item["id"])
    newest = max(timestamps)
    newest_utc = newest.astimezone(timezone.utc)
    newest_utc8 = newest.astimezone(UTC8)
    return {
        "version": "v1",
        "branch": branch,
        "generated_at": newest_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_utc8": newest_utc8.strftime("%Y-%m-%d %H:%M:%S"),
        "build_time": newest_utc8.strftime("%Y-%m-%d %H:%M:%S"),
        "total_apps": len(apps),
        "apps": apps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps-dir", default="apps", help="应用目录，相对于仓库根目录")
    parser.add_argument("--owner", default="taotie6", help="GitHub 仓库所有者")
    parser.add_argument("--repo", default="baihu-scripts", help="GitHub 仓库名")
    parser.add_argument("--branch", default="main", help="生成 Manifest URL 使用的分支")
    parser.add_argument("--output", default="apps.json", help="输出文件，相对于仓库根目录")
    parser.add_argument("--check", action="store_true", help="仅检查现有索引是否为最新")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    if args.apps_dir != "apps":
        custom = Path(args.apps_dir).resolve()
        if custom != root / "apps":
            raise ValueError("当前构建器要求应用目录为仓库根目录下的 apps/")
    output = root / args.output

    index = build_index(root, args.owner, args.repo, args.branch)
    rendered = json.dumps(index, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(f"[FAIL] {output.name} 不是最新，请运行 python build-all.py", file=sys.stderr)
            return 1
        print(f"[OK] {output.name} 已是最新，共 {index['total_apps']} 个应用")
        return 0

    output.write_text(rendered, encoding="utf-8")
    print(f"[OK] 已生成 {output.name}，共 {index['total_apps']} 个应用")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1)
