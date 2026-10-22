#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 每日积分领取（白虎面板版）。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import inspect
import json
import os
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from Crypto.Cipher import AES


API_BASE = "https://copilot.tencent.com/v2/billing/meter"
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "checkin.log"

# WorkBuddy 5.6.0 起登录态敏感字段使用 AES-256-GCM 加密。密钥由客户端
# 构建期内嵌的 atRestSecretKey 派生，同一客户端版本通用。
_AT_REST_SECRET = "Sik9U5aXhCdwTVEwsEySDOmDoB9r9ntFxHF1fst9LQI="
_WB_KEY = hashlib.sha256(_AT_REST_SECRET.encode("utf-8")).digest()
_WB_KEY_ID = hashlib.sha256(_WB_KEY).hexdigest()[:16]

_FRAMING_NUM = {"file": 1, "field": 2, "record": 3, "stream": 4}
_FRAMING_TAG = {"file": b"WBEF1", "field": b"WBEV1", "record": b"WBER1", "stream": b"WBES1"}


def _wb_aad(framing: str, key_id: str, suite: int) -> bytes:
    """复刻 buildAuthenticatedContextAad（sym-v1）的字节布局。"""
    return b"".join(
        [
            b"WB-AAD\x00",
            b"\x01",
            struct.pack(">I", len(_FRAMING_TAG[framing])) + _FRAMING_TAG[framing],
            struct.pack(">I", 6) + b"sym-v1",
            struct.pack(">I", suite),
            struct.pack(">I", len(key_id)) + key_id.encode(),
            bytes([_FRAMING_NUM[framing]]),
            b"\x00",
            b"\x00",
        ]
    )


def _decrypt_prefixed_field(value: str) -> str:
    """解密 5.6.0+ 使用的 ``$wbEncrypted:<base64>`` 字符串信封。

    已观察到的字符串信封是 ``nonce(12B) + ciphertext + tag(16B)``。部分构建
    会把相同三段包装成 JSON，因此同时兼容两种编码，但只接受当前构建密钥。
    """
    encoded = value.removeprefix("$wbEncrypted:")
    try:
        packed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ValueError("加密登录态不是合法 Base64") from error

    key_id = _WB_KEY_ID
    try:
        envelope = json.loads(packed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        envelope = None

    if isinstance(envelope, dict):
        try:
            key_id = str(envelope.get("keyId") or _WB_KEY_ID)
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            tag_value = envelope.get("tag", envelope.get("authTag"))
            auth_tag = base64.b64decode(tag_value, validate=True)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("加密登录态 JSON 信封格式无效") from error
    else:
        if len(packed) < 12 + 16 + 1:
            raise ValueError("加密登录态二进制信封长度不足")
        nonce, ciphertext, auth_tag = packed[:12], packed[12:-16], packed[-16:]

    if key_id != _WB_KEY_ID:
        raise ValueError(f"密钥不匹配（envelope keyId={key_id}），WorkBuddy 版本可能已更新")
    cipher = AES.new(_WB_KEY, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(b"WB-AAD\x00" + b"\x01" + key_id.encode("ascii") + nonce)
    try:
        return cipher.decrypt_and_verify(ciphertext, auth_tag).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("登录态解密认证失败，请重新登录 WorkBuddy") from error


def open_wb_field(value: Any) -> Any:
    """解密 $wbEncrypted 字段封装；明文值原样返回。"""
    if isinstance(value, str) and value.startswith("$wbEncrypted:"):
        return _decrypt_prefixed_field(value)
    if not isinstance(value, dict) or value.get("$wbEncrypted") != 1:
        return value
    if value.get("scheme") not in (None, "sym-v1"):
        raise ValueError(f"不支持的加密方案 {value.get('scheme')}，请重新登录 WorkBuddy")

    try:
        envelope = json.loads(base64.b64decode(value["envelope"], validate=True))
        key_id = envelope["keyId"]
        suite = int(envelope["suite"])
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        auth_tag = base64.b64decode(envelope["authTag"], validate=True)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("加密登录态格式无效") from error

    if key_id != _WB_KEY_ID:
        raise ValueError(f"密钥不匹配（envelope keyId={key_id}），WorkBuddy 版本可能已更新")

    cipher = AES.new(_WB_KEY, AES.MODE_GCM, nonce=nonce)
    cipher.update(_wb_aad("field", key_id, suite))
    try:
        plain = cipher.decrypt_and_verify(ciphertext, auth_tag)
    except ValueError as error:
        raise ValueError("登录态解密认证失败，请重新登录 WorkBuddy") from error
    return plain.decode("utf-8")


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as error:
        print(f"[WorkBuddy] 写入日志文件失败：{error}", file=sys.stderr, flush=True)


def _default_auth_dir() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    return Path(local_app_data) / "CodeBuddyExtension" / "Data" / "Public" / "auth"


def _sessions_from_env() -> list[tuple[str, dict[str, Any]]]:
    raw = os.environ.get("WORKBUDDY_SESSIONS", "").strip()
    if not raw:
        return []
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"WORKBUDDY_SESSIONS 不是合法 JSON：{error.msg}") from error

    entries = document if isinstance(document, list) else [document]
    sessions: list[tuple[str, dict[str, Any]]] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"WORKBUDDY_SESSIONS 第 {index} 项必须是 JSON 对象")
        label = str(entry.get("label") or f"账号{index}")
        sessions.append((label, entry))
    return sessions


def _sessions_from_dir(auth_dir: Path | None) -> list[tuple[str, dict[str, Any]]]:
    if auth_dir is None:
        return []
    if not auth_dir.is_dir():
        raise ValueError(f"未找到登录态目录 {auth_dir}")

    sessions: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(auth_dir.glob("*.info")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            log(f"ERROR: 解析 [{path.stem}] 失败：{error}")
            continue
        if not isinstance(document, dict):
            log(f"ERROR: 解析 [{path.stem}] 失败：登录态根节点必须是 JSON 对象")
            continue
        sessions.append((path.stem, document))
    return sessions


def load_accounts() -> list[tuple[str, dict[str, Any]]]:
    """从白虎机密变量或挂载目录读取登录态，解密并按 token 去重。"""
    candidates: list[tuple[str, dict[str, Any]]] = []
    try:
        candidates.extend(_sessions_from_env())
    except ValueError as error:
        log(f"ERROR: {error}")

    configured_dir = os.environ.get("WORKBUDDY_AUTH_DIR", "").strip()
    auth_dir = Path(configured_dir).expanduser() if configured_dir else _default_auth_dir()
    if auth_dir is not None:
        try:
            candidates.extend(_sessions_from_dir(auth_dir))
        except ValueError as error:
            # 已配置变量时，Windows 默认目录不存在不应阻止白虎运行。
            if configured_dir or not candidates:
                log(f"ERROR: {error}")

    accounts: list[tuple[str, dict[str, Any]]] = []
    seen: set[bytes] = set()
    for name, session in candidates:
        try:
            auth = session.get("auth") or {}
            account = session.get("account") or {}
            if not isinstance(auth, dict) or not isinstance(account, dict):
                raise ValueError("auth/account 字段格式无效")
            token = open_wb_field(auth.get("accessToken"))
            if not isinstance(token, str) or not token:
                raise ValueError("登录态中无 accessToken，请重新登录")
            if not account.get("uid"):
                raise ValueError("登录态中无 account.uid，请重新登录")
            token_fingerprint = hashlib.sha256(token.encode("utf-8")).digest()
            if token_fingerprint in seen:
                continue
            seen.add(token_fingerprint)
            # 只替换内存副本，不修改原始文件或白虎变量。
            normalized = dict(session)
            normalized["auth"] = dict(auth)
            normalized["auth"]["accessToken"] = token
            accounts.append((name, normalized))
        except (UnicodeDecodeError, ValueError) as error:
            log(f"ERROR: 解析 [{name}] 失败：{error}")
    return accounts


def api(path: str, session: dict[str, Any]) -> dict[str, Any]:
    auth = session["auth"]
    headers = {
        "Authorization": f"Bearer {auth['accessToken']}",
        "X-User-Id": str(session["account"]["uid"]),
        "X-Domain": str(auth.get("domain", "")),
        "Content-Type": "application/json",
    }
    response = requests.post(f"{API_BASE}/{path}", headers=headers, json={}, timeout=30)
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise RuntimeError(f"HTTP {response.status_code} 返回了非 JSON 响应") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {response.status_code} 返回格式异常")
    if not response.ok and "code" not in payload:
        raise RuntimeError(f"HTTP {response.status_code} 请求失败")
    return payload


def _is_expired(session: dict[str, Any]) -> bool:
    expires_at = (session.get("auth") or {}).get("expiresAt")
    try:
        return not expires_at or float(expires_at) / 1000 < time.time()
    except (TypeError, ValueError):
        return True


def checkin_api(name: str, session: dict[str, Any]) -> bool:
    """通过 HTTP API 签到，成功或今日已签到返回 True。"""
    if _is_expired(session):
        log(f"[{name}] accessToken 已过期，需启动 WorkBuddy 刷新登录态后重新导入")
        return False

    try:
        status = api("checkin-status", session)
    except (requests.RequestException, RuntimeError) as error:
        log(f"[{name}] 查询签到状态失败：{error}")
        return False

    data = status.get("data") or {}
    if status.get("code") != 0:
        log(f"[{name}] 查询签到状态异常：{status.get('msg') or '未知错误'}")
        return False
    if isinstance(data, dict) and data.get("today_checked_in"):
        log(f"[{name}] (API) 今日已签到")
        return True

    try:
        claim = api("daily-checkin", session)
    except (requests.RequestException, RuntimeError) as error:
        log(f"[{name}] 领取积分失败：{error}")
        return False

    if claim.get("code") == 0:
        claim_data = claim.get("data") or {}
        credit = claim_data.get("credit", "?") if isinstance(claim_data, dict) else "?"
        log(f"[{name}] (API) 签到成功，获得积分：{credit}")
        return True
    if claim.get("code") == 10001:
        log(f"[{name}] (API) 今日已签到")
        return True
    log(f"[{name}] (API) 签到失败：{claim.get('msg') or '未知错误'}")
    return False


def _notify(success_count: int, total: int) -> None:
    """白虎运行时存在通知模块时发送汇总；本地运行时静默跳过。"""
    try:
        import baihu  # type: ignore[import-not-found]

        notify = getattr(baihu, "notify", None)
        if callable(notify):
            result = notify("WorkBuddy 每日积分", f"成功 {success_count}/{total} 个账号")
            if inspect.isawaitable(result):
                import asyncio

                asyncio.run(result)
    except Exception as error:  # 通知失败不能覆盖签到结果。
        log(f"WARN: 白虎通知发送失败：{error}")


def main() -> int:
    log("=" * 50)
    log("[WorkBuddy] 开始每日积分领取")
    log("=" * 50)
    accounts = load_accounts()
    if not accounts:
        log("ERROR: 没有可用登录态，请配置 WORKBUDDY_SESSIONS 或挂载登录态目录")
        return 1

    results = [checkin_api(name, session) for name, session in accounts]
    success_count = sum(results)
    log(f"[WorkBuddy] 执行完成：成功 {success_count}/{len(results)}")
    _notify(success_count, len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
