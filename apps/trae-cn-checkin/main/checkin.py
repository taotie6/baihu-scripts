#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trae CN / TRAE SOLO CN 每日签到（白虎面板版）。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


AUTH_KEY = "iCubeAuthInfo://icube.cloudide"
API_BASE = "https://api.trae.cn/trae/api/v2/ug/checkin_credits"
REQ_SOURCE = 1

HDR_LEN = 6
KEY_LEN = 32
HMAC_LEN = 64
URE = bytes(
    [
        82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251,
        124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203,
        84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78,
        8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109, 139, 209, 37,
    ]
)
DRE = bytes(
    [
        31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95,
        96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239,
        160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97,
        23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33, 12, 125,
    ]
)

APP_DIR_NAMES = ("Trae CN", "TRAE SOLO CN")
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "checkin.log"


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as error:
        print(f"[Trae CN] 写入日志文件失败：{error}", file=sys.stderr, flush=True)


def decrypt_auth(b64_text: str) -> dict[str, Any]:
    """解密 iCubeAuthInfo 加密串，返回含 token/userRegion 的对象。"""
    try:
        encrypted = base64.b64decode(b64_text, validate=True)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ValueError("登录态不是合法 Base64") from error

    minimum_length = HDR_LEN + KEY_LEN + AES.block_size
    if len(encrypted) < minimum_length:
        raise ValueError("登录态长度不足")
    ciphertext = encrypted[HDR_LEN + KEY_LEN :]
    if len(ciphertext) % AES.block_size:
        raise ValueError("登录态密文长度无效")

    key = encrypted[HDR_LEN : HDR_LEN + KEY_LEN]
    sha = hashlib.sha512(key).digest()
    xor = bytes(left ^ right for left, right in zip(URE, DRE))
    derived = hashlib.sha512(sha + xor).digest()
    aes_key, iv = derived[:16], derived[16:32]
    try:
        plain = unpad(AES.new(aes_key, AES.MODE_CBC, iv).decrypt(ciphertext), AES.block_size)
        payload = json.loads(plain[HMAC_LEN:].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("登录态解密失败，请重新登录 Trae CN") from error
    if not isinstance(payload, dict):
        raise ValueError("登录态解密结果格式无效")
    return payload


def _expand_path(raw: str) -> Path:
    replacements = {
        "%APPDATA%": os.environ.get("APPDATA", ""),
        "%USERPROFILE%": os.environ.get("USERPROFILE", ""),
    }
    expanded = raw
    for marker, value in replacements.items():
        if value:
            expanded = expanded.replace(marker, value)
    return Path(os.path.expandvars(expanded)).expanduser()


def _configured_paths() -> list[str]:
    raw = os.environ.get("TRAE_CN_STORAGE_PATHS", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("TRAE_CN_STORAGE_PATHS 必须是路径字符串或 JSON 字符串数组")
    return value


def scan_targets() -> list[tuple[str, Path, Path]]:
    """扫描 (名称, storage.json 路径, 应用数据目录)，按路径去重。"""
    candidates: list[tuple[str, Path, Path]] = []
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        for name in APP_DIR_NAMES:
            app_dir = Path(appdata) / name
            candidates.append((name, app_dir / "User" / "globalStorage" / "storage.json", app_dir))

    try:
        configured_paths = _configured_paths()
    except ValueError as error:
        log(f"ERROR: {error}")
        configured_paths = []
    for raw in configured_paths:
        path = _expand_path(raw)
        if path.suffix.lower() == ".json":
            app_dir = path.parents[2] if len(path.parents) > 2 else path.parent
            candidates.append((app_dir.name or path.stem, path, app_dir))
        else:
            candidates.append((path.name, path / "User" / "globalStorage" / "storage.json", path))

    targets: list[tuple[str, Path, Path]] = []
    seen: set[str] = set()
    for name, storage_file, app_dir in candidates:
        fingerprint = str(storage_file).casefold()
        if fingerprint not in seen:
            seen.add(fingerprint)
            targets.append((name, storage_file, app_dir))
    return targets


def _storages_from_env() -> list[tuple[str, dict[str, Any]]]:
    raw = os.environ.get("TRAE_CN_SESSIONS", "").strip()
    if not raw:
        return []
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"TRAE_CN_SESSIONS 不是合法 JSON：{error.msg}") from error

    entries = document if isinstance(document, list) else [document]
    storages: list[tuple[str, dict[str, Any]]] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"TRAE_CN_SESSIONS 第 {index} 项必须是 JSON 对象")
        label = str(entry.get("label") or f"账号{index}")
        # 完整 storage.json 优先，避免其恰好含有名为 storage 的普通配置项。
        storage = entry if AUTH_KEY in entry else entry.get("storage")
        if not isinstance(storage, dict):
            raise ValueError(f"TRAE_CN_SESSIONS 第 {index} 项不是完整 storage.json 或包装对象")
        storages.append((label, storage))
    return storages


def _storages_from_files() -> list[tuple[str, dict[str, Any]]]:
    storages: list[tuple[str, dict[str, Any]]] = []
    for name, path, _ in scan_targets():
        if not path.exists():
            log(f"跳过 [{name}]：未找到 {path}")
            continue
        try:
            storage = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            log(f"ERROR: 解析 [{name}] storage.json 失败：{error}")
            continue
        if not isinstance(storage, dict):
            log(f"ERROR: 解析 [{name}] storage.json 失败：根节点必须是 JSON 对象")
            continue
        storages.append((name, storage))
    return storages


def load_accounts() -> list[tuple[str, dict[str, Any]]]:
    """从白虎机密变量或 storage.json 读取账号，按 token 去重。"""
    candidates: list[tuple[str, dict[str, Any]]] = []
    try:
        candidates.extend(_storages_from_env())
    except ValueError as error:
        log(f"ERROR: {error}")
    candidates.extend(_storages_from_files())

    accounts: list[tuple[str, dict[str, Any]]] = []
    seen: set[bytes] = set()
    for name, storage in candidates:
        try:
            encrypted = storage.get(AUTH_KEY)
            if not isinstance(encrypted, str) or not encrypted:
                raise ValueError("storage.json 中无 iCube 登录态")
            auth = decrypt_auth(encrypted)
            token = auth.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError("登录态中无 token，请重新登录")
            fingerprint = hashlib.sha256(token.encode("utf-8")).digest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            accounts.append((name, auth))
        except ValueError as error:
            log(f"ERROR: 解析 [{name}] 失败：{error}")
    return accounts


def load_device_id() -> str:
    """设备 ID：白虎机密变量 → ahanet 配置 → 客户端日志。"""
    configured = os.environ.get("TRAE_CN_DEVICE_ID", "").strip()
    if configured:
        if not configured.isdigit():
            log("WARN: TRAE_CN_DEVICE_ID 通常应为纯数字，请检查配置")
        return configured

    app_dirs = [app_dir for _, _, app_dir in scan_targets()]
    for app_dir in app_dirs:
        path = app_dir / "ahanet" / "tt_net_config.config"
        try:
            match = re.search(r"device_id[^\w]*(\d+)", path.read_text(encoding="utf-8", errors="ignore"))
            if match:
                return match.group(1)
        except OSError:
            continue

    for app_dir in app_dirs:
        logs_dir = app_dir / "logs"
        if not logs_dir.is_dir():
            continue
        try:
            main_logs = sorted(
                logs_dir.glob("*/main.log"), key=lambda path: path.stat().st_mtime, reverse=True
            )
        except OSError:
            continue
        for path in main_logs[:5]:
            try:
                match = re.search(
                    r"\[ICDRS\].*?did: (\d+)", path.read_text(encoding="utf-8", errors="ignore")
                )
                if match:
                    return match.group(1)
            except OSError:
                continue
    return ""


def api(url: str, token: str, region: str, device_id: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Cloud-IDE-JWT {token}",
        "Content-Type": "application/json",
        "x-device-id": device_id,
    }
    if region:
        headers["X-User-Region"] = region
    response = requests.post(url, headers=headers, json={"req_source": REQ_SOURCE}, timeout=30)
    response.raise_for_status()
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise RuntimeError(f"HTTP {response.status_code} 返回了非 JSON 响应") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {response.status_code} 返回格式异常")
    return payload


def unwrap(response: dict[str, Any]) -> dict[str, Any]:
    """兼容直接返回和 {code: 0, data: {...}} 两种格式。"""
    data = response.get("data")
    if "checked_in" not in response and isinstance(data, dict) and "checked_in" in data:
        return data
    return response


def _user_region(auth: dict[str, Any]) -> str:
    value = auth.get("userRegion")
    if isinstance(value, dict):
        return str(value.get("region") or "")
    return str(value or "")


def checkin_api(name: str, auth: dict[str, Any], device_id: str) -> bool:
    """通过 HTTP API 签到，成功或今日已签到返回 True。"""
    token = str(auth["token"])
    region = _user_region(auth)
    try:
        status = unwrap(api(f"{API_BASE}/status", token, region, device_id))
    except requests.HTTPError as error:
        code = error.response.status_code if error.response is not None else "?"
        log(f"[{name}] 查询签到状态失败(HTTP {code})，登录态可能过期")
        return False
    except (requests.RequestException, RuntimeError) as error:
        log(f"[{name}] 查询签到状态失败：{error}")
        return False

    if status.get("checked_in"):
        log(f"[{name}] (API) 今日已签到，当前积分：{status.get('credits', '?')}")
        return True
    if not status.get("enable"):
        summary = json.dumps(status, ensure_ascii=False)[:200]
        log(f"[{name}] 签到不可用：{summary}")
        return False

    try:
        claim = api(f"{API_BASE}/claim", token, region, device_id)
    except requests.HTTPError as error:
        code = error.response.status_code if error.response is not None else "?"
        log(f"[{name}] 领取积分失败(HTTP {code})")
        return False
    except (requests.RequestException, RuntimeError) as error:
        log(f"[{name}] 领取积分失败：{error}")
        return False

    if claim.get("code") == 0:
        try:
            after = unwrap(api(f"{API_BASE}/status", token, region, device_id))
            log(f"[{name}] (API) 签到成功，当前积分：{after.get('credits', '?')}")
        except (requests.RequestException, RuntimeError):
            log(f"[{name}] (API) 签到成功")
        return True
    message = claim.get("message") or json.dumps(claim, ensure_ascii=False)[:200]
    log(f"[{name}] (API) 签到失败：{message}")
    return False


def _notify(success_count: int, total: int) -> None:
    """白虎运行时存在通知模块时发送汇总；本地运行时静默跳过。"""
    try:
        import baihu  # type: ignore[import-not-found]

        notify = getattr(baihu, "notify", None)
        if callable(notify):
            result = notify("Trae CN 每日签到", f"成功 {success_count}/{total} 个账号")
            if inspect.isawaitable(result):
                asyncio.run(result)
    except Exception as error:  # 通知失败不能覆盖签到结果。
        log(f"WARN: 白虎通知发送失败：{error}")


def main() -> int:
    log("=" * 50)
    log("[Trae CN] 开始每日签到")
    log("=" * 50)
    accounts = load_accounts()
    if not accounts:
        log("ERROR: 没有可用登录态，请配置 TRAE_CN_SESSIONS 或挂载 storage.json")
        return 1

    device_id = load_device_id()
    if not device_id:
        log("WARN: 未找到设备 ID；将使用空值请求，若失败请配置 TRAE_CN_DEVICE_ID")
    results = [checkin_api(name, auth, device_id) for name, auth in accounts]
    success_count = sum(results)
    log(f"[Trae CN] 执行完成：成功 {success_count}/{len(results)}")
    _notify(success_count, len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
