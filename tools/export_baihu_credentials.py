#!/usr/bin/env python3
"""只读 Windows 客户端登录态，生成可粘贴到白虎面板的变量包。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path.cwd() / "baihu-credentials.json"


def _load_module(name: str, relative_path: str) -> ModuleType:
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fingerprint(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def export_qoder() -> dict[str, Any]:
    module = _load_module("qoder_cn_checkin", "apps/qoder-cn-checkin/main/checkin.py")
    return {"QODER_CN_AUTHS": [module.panel_auth(module.load_client_auth())]}


def export_trae() -> dict[str, Any]:
    module = _load_module("trae_cn_checkin", "apps/trae-cn-checkin/main/checkin.py")
    sessions: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    invalid: list[str] = []
    for name, path, _ in module.scan_targets():
        if not path.is_file():
            continue
        try:
            storage = json.loads(path.read_text(encoding="utf-8"))
            encrypted = storage[module.AUTH_KEY]
            auth = module.decrypt_auth(encrypted)
            token = auth["token"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            invalid.append(f"{path}: {error}")
            continue
        if not isinstance(token, str) or not token:
            invalid.append(f"{path}: 登录态中没有 token")
            continue
        digest = _fingerprint(token)
        if digest in seen:
            continue
        seen.add(digest)
        sessions.append({"label": name, "storage": {module.AUTH_KEY: encrypted}})
    if not sessions:
        details = f"；{invalid[0]}" if invalid else ""
        raise ValueError(f"未找到可用的 Trae CN / TRAE SOLO CN 登录态{details}")
    result: dict[str, Any] = {"TRAE_CN_SESSIONS": sessions}
    device_id = module.load_device_id()
    if device_id:
        result["TRAE_CN_DEVICE_ID"] = device_id
    return result


def export_workbuddy() -> dict[str, Any]:
    module = _load_module("workbuddy_checkin", "apps/workbuddy-checkin/main/checkin.py")
    auth_dir = module._default_auth_dir()
    if auth_dir is None or not auth_dir.is_dir():
        raise ValueError("未找到 WorkBuddy 登录态目录")

    sessions: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for label, document in module._sessions_from_dir(auth_dir):
        auth = document.get("auth") or {}
        account = document.get("account") or {}
        if not isinstance(auth, dict) or not isinstance(account, dict):
            raise ValueError(f"WorkBuddy 登录态 {label} 的 auth/account 格式无效")
        token = module.open_wb_field(auth.get("accessToken"))
        uid = account.get("uid")
        if not isinstance(token, str) or not token or not uid:
            raise ValueError(f"WorkBuddy 登录态 {label} 缺少 accessToken 或 account.uid")
        digest = _fingerprint(token)
        if digest in seen:
            continue
        seen.add(digest)
        # 保留密文，不把解密后的 token 落盘；只导出签到必需字段。
        sessions.append(
            {
                "label": label,
                "account": {"uid": uid},
                "auth": {
                    "accessToken": auth["accessToken"],
                    "domain": auth.get("domain", ""),
                    "expiresAt": auth.get("expiresAt", 0),
                },
            }
        )
    if not sessions:
        raise ValueError(f"{auth_dir} 中没有可用的 *.info 登录态")
    return {"WORKBUDDY_SESSIONS": sessions}


EXPORTERS = {
    "qoder": export_qoder,
    "trae": export_trae,
    "workbuddy": export_workbuddy,
}


def _write_private_json(path: Path, document: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "apps",
        nargs="*",
        choices=tuple(EXPORTERS),
        help="要导出的客户端；默认扫描全部",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="敏感变量包输出路径")
    args = parser.parse_args()

    selected = args.apps or list(EXPORTERS)
    output: dict[str, Any] = {}
    errors: list[str] = []
    for name in selected:
        try:
            values = EXPORTERS[name]()
            output.update(values)
            print(f"[OK] {name}: 已读取 {len(next(iter(values.values()))) if values else 0} 项")
        except Exception as error:  # 单个未安装客户端不影响其他客户端导出。
            errors.append(f"{name}: {error}")
            print(f"[SKIP] {name}: {error}", file=sys.stderr)

    if not output:
        print("[FAIL] 没有找到可导出的登录态", file=sys.stderr)
        return 1
    _write_private_json(args.output, output)
    print(f"[OK] 已写入 {args.output.resolve()}（包含敏感凭证，请勿提交或传播）")
    print("将各顶层键对应的值复制到白虎面板同名机密变量；JSON 值请完整复制。")
    return 1 if args.apps and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
