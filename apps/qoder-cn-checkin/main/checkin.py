#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder CN 用量查询与活动领取（白虎面板版）。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# Windows 控制台默认编码可能导致中文输出异常。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


API = "https://openapi.qoder.com.cn"
DEFAULT_CLIENT_TYPE = "10"
DEFAULT_VERSION = "0.3.4"
BASE_DIR = Path(__file__).resolve().parent
AUTH_FILE = BASE_DIR / "auths.json"
LOG_FILE = BASE_DIR / "checkin.log"

ENDPOINTS: dict[str, tuple[str, dict[str, Any] | None]] = {
    "credits-summary": ("/sash/api/v1/ai-conversations/credits-summary", None),
    "seat-activity": ("/sash/api/v1/ai-conversations/seat-activity", None),
    "credits-heatmap": (
        "/sash/api/v1/ai-conversations/credits-heatmap",
        {"days": 30, "organization_id": ""},
    ),
    "campaigns": ("/sash/api/v1/me/campaigns", None),
    "plan": ("/api/v2/user/plan", None),
    "quota": ("/api/v2/quota/usage", None),
}


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def log(message: str = "", *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as exc:
        print(f"[Qoder CN] 写入日志文件失败：{exc}", file=sys.stderr, flush=True)


def _profile_dir() -> Path:
    configured = os.environ.get("QODER_CN_PROFILE", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise RuntimeError("当前环境没有 APPDATA，请配置 QODER_CN_AUTHS")
    return Path(appdata) / "com.qodercn.app.stable"


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI 只能在原 Windows 用户环境中解密，请配置 QODER_CN_AUTHS")
    kernel32 = ctypes.windll.kernel32
    inp, out = _Blob(), _Blob()
    inp.cbData = len(data)
    inp.pbData = ctypes.create_string_buffer(data)
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    ):
        raise OSError(f"CryptUnprotectData 失败，GetLastError={kernel32.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def load_client_auth() -> dict[str, Any]:
    """在原 Windows 用户环境中解密 Qoder 客户端登录仓。"""
    profile = _profile_dir()
    try:
        local_state = json.loads((profile / "Local State").read_text(encoding="utf-8"))
        encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"], validate=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取 Qoder Local State 失败：{exc}") from exc
    if not encrypted_key.startswith(b"DPAPI"):
        raise ValueError("Local State 的 encrypted_key 前缀不是 DPAPI")
    key = _dpapi_unprotect(encrypted_key[5:])

    try:
        blob = (profile / "auth.v1.dat").read_bytes()
    except OSError as exc:
        raise ValueError(f"读取 auth.v1.dat 失败：{exc}") from exc
    if not blob.startswith(b"v10") or len(blob) < 3 + 12 + 16:
        raise ValueError("auth.v1.dat 不是有效的 v10 信封")
    try:
        auth = json.loads(AESGCM(key).decrypt(blob[3:15], blob[15:], None))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("auth.v1.dat 解密失败，请重新登录 Qoder CN") from exc
    if not isinstance(auth, dict):
        raise ValueError("客户端登录仓解密结果格式无效")
    return auth


def panel_auth(auth: dict[str, Any]) -> dict[str, Any]:
    """仅保留白虎调用 API 所需字段，绝不导出 refreshToken。"""
    token = auth.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("客户端登录仓中没有 token")
    result: dict[str, Any] = {"token": token}
    for key in ("expiresAt", "user"):
        if auth.get(key) is not None:
            result[key] = auth[key]
    return result


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取凭证文件 {path} 失败：{exc}") from exc


def _normalise_auths(data: Any) -> list[tuple[str, dict[str, Any]]]:
    entries = data if isinstance(data, list) else [data]
    accounts: list[tuple[str, dict[str, Any]]] = []
    seen: set[bytes] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"凭证第 {index} 项必须是 JSON 对象")
        label = str(entry.get("label") or "")
        auth = entry if "token" in entry else entry.get("auth")
        if not isinstance(auth, dict):
            raise ValueError(f"凭证第 {index} 项不是登录态对象或 auth 包装对象")
        token = auth.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError(f"凭证第 {index} 项没有 token")
        fingerprint = hashlib.sha256(token.encode("utf-8")).digest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        user = auth.get("user") or {}
        if not label and isinstance(user, dict):
            label = str(user.get("name") or "")
        accounts.append((label or f"账号{index}", auth))
    return accounts


def load_auths(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    """凭证优先级：命令行文件 > 白虎机密变量 > 环境文件 > 同目录文件 > DPAPI。"""
    if args.auth_file:
        return _normalise_auths(_read_json_file(Path(args.auth_file).expanduser()))

    raw = os.environ.get("QODER_CN_AUTHS", "").strip()
    if raw:
        try:
            return _normalise_auths(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"QODER_CN_AUTHS 不是合法 JSON：{exc.msg}") from exc

    configured_file = os.environ.get("QODER_CN_AUTH_FILE", "").strip()
    if configured_file:
        return _normalise_auths(_read_json_file(Path(configured_file).expanduser()))
    if AUTH_FILE.is_file():
        return _normalise_auths(_read_json_file(AUTH_FILE))
    return _normalise_auths(load_client_auth())


def export_client_auth(path: Path) -> None:
    """显式导出 Windows 客户端凭证，供复制到白虎机密变量。"""
    auth = panel_auth(load_client_auth())
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(auth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    log(f"凭证已导出到 {path}；请将内容保存为白虎机密变量并妥善删除本地副本")


def make_session(token: str) -> requests.Session:
    """创建带 Qoder 公共请求头的 Session。"""
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Qoder",
            "Authorization": f"Bearer {token}",
            "Cosy-ClientType": os.environ.get("QODER_CN_CLIENT_TYPE", DEFAULT_CLIENT_TYPE),
            "Cosy-Version": os.environ.get("QODER_CN_VERSION", DEFAULT_VERSION),
        }
    )
    return session


def _redact_response_text(session: requests.Session, text: str, limit: int = 400) -> str:
    safe = text
    authorization = str(session.headers.get("Authorization") or "")
    token = authorization.removeprefix("Bearer ")
    if authorization:
        safe = safe.replace(authorization, "[REDACTED]")
    if len(token) >= 8:
        safe = safe.replace(token, "[REDACTED]")
    return safe[:limit]


def get(
    session: requests.Session, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = session.get(API + path, params=params, timeout=20)
    if response.status_code != 200:
        return {
            "_error": response.status_code,
            "_body": _redact_response_text(session, response.text),
        }
    try:
        body = response.json()
    except requests.exceptions.JSONDecodeError:
        return {
            "_error": "响应不是 JSON",
            "_body": _redact_response_text(session, response.text),
        }
    if not isinstance(body, dict):
        return {"_error": "响应格式异常", "_body": str(type(body).__name__)}
    return body


def claim_one(session: requests.Session, campaign_id: str) -> dict[str, Any]:
    """领取单个活动权益；服务端支持幂等重放。"""
    response = session.post(f"{API}/sash/api/v1/me/campaigns/{campaign_id}/claim", timeout=20)
    try:
        parsed = response.json()
    except requests.exceptions.JSONDecodeError:
        parsed = {"_raw": _redact_response_text(session, response.text, 300)}
    body = parsed if isinstance(parsed, dict) else {"_raw": str(parsed)[:300]}
    body["_http"] = response.status_code
    return body


def _number(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "?"


def print_quota(session: requests.Session, prefix: str = "剩余额度") -> bool:
    """拉取 quota/usage 并打印套餐及签到发放额度。"""
    quota = get(session, ENDPOINTS["quota"][0])
    if "_error" in quota:
        log(f"{prefix}  : 请求失败 {quota['_error']}")
        return False
    parts: list[str] = []
    for label, key in (("套餐", "userQuota"), ("签到/加油包", "addOnQuota")):
        value = quota.get(key) or {}
        if isinstance(value, dict) and value.get("total"):
            parts.append(f"{label} 剩余 {_number(value.get('remaining'))}/{_number(value.get('total'))}")
    exceeded = "（已超额）" if quota.get("isQuotaExceeded") else ""
    log(f"{prefix}  : {'，'.join(parts) if parts else '无可用额度'}{exceeded}")
    return True


def do_claim(session: requests.Session, dry_run: bool) -> int:
    document = get(session, ENDPOINTS["campaigns"][0])
    if "_error" in document:
        log(f"活动列表请求失败：{document['_error']} {document.get('_body', '')[:120]}")
        return 1
    campaigns = document.get("campaigns") or []
    if not isinstance(campaigns, list) or not campaigns:
        log("当前没有可展示的活动")
        return 0
    for campaign in campaigns:
        if not isinstance(campaign, dict):
            continue
        benefit = campaign.get("benefit") or {}
        if not isinstance(benefit, dict):
            benefit = {}
        log(
            f"{campaign.get('campaignId')}  {campaign.get('campaignKey')}  "
            f"{str(campaign.get('actionType', '')):13} {str(campaign.get('claimStatus', '')):9} "
            f"{benefit.get('kind')}x{benefit.get('amount')}"
        )

    targets = [
        campaign
        for campaign in campaigns
        if isinstance(campaign, dict)
        and campaign.get("claimStatus") == "CLAIMABLE"
    ]
    if not targets:
        log("没有 CLAIMABLE 的活动，无需签到")
        return 0
    if dry_run:
        keys = [target.get("campaignKey") for target in targets]
        log(f"[dry-run] 将领取 {len(targets)} 条：{keys}")
        return 0

    claimed = False
    failures = 0
    for campaign in targets:
        result = claim_one(session, str(campaign["campaignId"]))
        benefit = result.get("benefit") or {}
        if not isinstance(benefit, dict):
            benefit = {}
        log(
            f"签到 {campaign.get('campaignKey')} -> HTTP {result.get('_http')} "
            f"status={result.get('status')} replayed={result.get('replayed')} "
            f"权益={benefit.get('kind')}x{benefit.get('amount')} 发放单={result.get('grantId')}"
        )
        success = result.get("_http") == 200 and (
            result.get("status") == "CLAIMED" or result.get("replayed") is True
        )
        claimed = claimed or success
        failures += int(not success)
    if claimed:
        log("签到已到账，查询最新额度")
        print_quota(session, prefix="剩余额度(签到后)")
    return 1 if failures else 0


def show_usage(session: requests.Session, auth: dict[str, Any], args: argparse.Namespace) -> int:
    """面板数据汇总并自动领取活动权益。"""
    user = auth.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    summary = get(session, ENDPOINTS["credits-summary"][0])
    streak = get(session, ENDPOINTS["seat-activity"][0])
    heat = get(
        session,
        ENDPOINTS["credits-heatmap"][0],
        {"days": args.days, "organization_id": ""},
    )

    log(f"账号      : {user.get('name')} ({user.get('id')})")
    log(f"token 到期: {auth.get('expiresAt')}")
    failed = not print_quota(session)
    if "_error" not in summary:
        log(f"Credits   : 累计 {summary.get('totalCredits')} / 峰值 {summary.get('peakCredits')}")
    else:
        log(f"Credits   : 请求失败 {summary['_error']} {summary.get('_body', '')[:120]}")
        failed = True
    if "_error" not in streak:
        log(
            f"活跃天数  : 累计 {streak.get('cumulativeActiveDays')} 天 / 当前连续 "
            f"{streak.get('currentConsecutiveDays')} 天 / 最长 {streak.get('maxConsecutiveDays')} 天"
            f"（最后活跃 {streak.get('lastActiveDate')}）"
        )
    else:
        log(f"活跃天数  : 请求失败 {streak['_error']}")
        failed = True
    if "_error" not in heat:
        items = heat.get("items") or []
        items = items if isinstance(items, list) else []
        used = [item for item in items if isinstance(item, dict) and item.get("value")]
        first_date = items[0].get("date", "?") if items and isinstance(items[0], dict) else "?"
        last_date = items[-1].get("date", "?") if items and isinstance(items[-1], dict) else "?"
        log(f"热力图    : {first_date} ~ {last_date}，总和 {heat.get('total')}，有用量的天数 {len(used)}")
        for item in used[-5:]:
            log(f"            {item.get('date')}  {item.get('value')}")
    else:
        log(f"热力图    : 请求失败 {heat['_error']}")
        failed = True

    claim_result = do_claim(session, args.dry_run)
    return 1 if failed or claim_result else 0


def run_account(auth: dict[str, Any], args: argparse.Namespace) -> int:
    session = make_session(str(auth["token"]))
    if args.what == "claim":
        return do_claim(session, args.dry_run)
    if args.what in ENDPOINTS:
        path, params = ENDPOINTS[args.what]
        if args.what == "credits-heatmap":
            params = {**(params or {}), "days": args.days}
        output = get(session, path, params)
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        log(_redact_response_text(session, rendered, max(400, len(rendered))))
        return 1 if "_error" in output else 0
    return show_usage(session, auth, args)


def _notify(success_count: int, total: int) -> None:
    try:
        import baihu  # type: ignore[import-not-found]

        notify = getattr(baihu, "notify", None)
        if callable(notify):
            result = notify("Qoder CN 用量与签到", f"成功 {success_count}/{total} 个账号")
            if inspect.isawaitable(result):
                asyncio.run(result)
    except Exception as exc:  # 通知失败不能覆盖任务结果。
        log(f"WARN: 白虎通知发送失败：{exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="读取 Qoder CN 用量并领取活动权益")
    choices = ["usage", "claim", "export-auth", *ENDPOINTS]
    parser.add_argument("what", nargs="?", choices=choices, default="usage")
    parser.add_argument(
        "--days",
        type=int,
        default=os.environ.get("QODER_CN_HEATMAP_DAYS", "30"),
        help="credits-heatmap 的天数，默认 30",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出将领取的活动")
    parser.add_argument("--auth-file", help="凭证 JSON 文件（对象或对象列表）")
    parser.add_argument("--export-file", help="export-auth 的输出路径，默认同目录 auths.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.what == "export-auth":
        try:
            export_client_auth(Path(args.export_file) if args.export_file else AUTH_FILE)
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            log(f"ERROR: 导出凭证失败：{exc}", error=True)
            return 1

    try:
        accounts = load_auths(args)
    except (OSError, RuntimeError, ValueError) as exc:
        log(f"ERROR: 加载凭证失败：{exc}", error=True)
        return 1
    if not accounts:
        log("ERROR: 没有可用的 Qoder CN 凭证", error=True)
        return 1

    results: list[bool] = []
    for index, (label, auth) in enumerate(accounts, start=1):
        log(f"===== 账号 {index}: {label} =====")
        try:
            results.append(run_account(auth, args) == 0)
        except Exception as exc:  # 单账号失败不能中断后续账号。
            log(f"账号 {label} 处理失败：{exc}", error=True)
            results.append(False)
    success_count = sum(results)
    log(f"[Qoder CN] 执行完成：成功 {success_count}/{len(results)}")
    _notify(success_count, len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
