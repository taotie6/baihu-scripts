#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder 国际版用量查询与活动领取（白虎面板版）。"""

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


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


API = "https://openapi.qoder.sh"
DEFAULT_CLIENT_TYPE = "10"
DEFAULT_VERSION = "0.4.2"
PROFILE_CANDIDATES = (
    "com.qoder.app.stable",
    "com.qoder.app",
    "com.qoder.stable",
    "Qoder",
)
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
        print(f"[Qoder Global] 写入日志文件失败：{exc}", file=sys.stderr, flush=True)


def _redact(text: str, *secrets: str, limit: int | None = None) -> str:
    safe = text
    for secret in secrets:
        if secret and len(secret) >= 8:
            safe = safe.replace(secret, "[REDACTED]")
            safe = safe.replace(f"Bearer {secret}", "Bearer [REDACTED]")
    return safe if limit is None else safe[:limit]


def _session_token(session: requests.Session) -> str:
    authorization = str(session.headers.get("Authorization") or "")
    return authorization.removeprefix("Bearer ")


def _profile_dir() -> Path:
    configured = os.environ.get("QODER_GLOBAL_PROFILE", "").strip()
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if not (path / "Local State").is_file():
            raise FileNotFoundError(f"QODER_GLOBAL_PROFILE 中未找到 Local State：{path}")
        return path
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise RuntimeError("当前环境没有 APPDATA，请配置 QODER_GLOBAL_PAT 或 QODER_GLOBAL_AUTHS")
    for name in PROFILE_CANDIDATES:
        path = Path(appdata) / name
        if (path / "Local State").is_file():
            return path
    raise FileNotFoundError(f"未找到 Qoder 国际版凭证目录，尝试过：{', '.join(PROFILE_CANDIDATES)}")


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI 只能在原 Windows 用户环境中解密，请改用 PAT 或导入凭证")
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
    """在原 Windows 用户环境中解密国际版客户端登录仓。"""
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
    if not blob.startswith(b"v10") or len(blob) < 31:
        raise ValueError("auth.v1.dat 不是有效的 v10 信封")
    try:
        auth = json.loads(AESGCM(key).decrypt(blob[3:15], blob[15:], None))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("auth.v1.dat 解密失败，请重新登录 Qoder") from exc
    if not isinstance(auth, dict):
        raise ValueError("客户端登录仓解密结果格式无效")
    return auth


def _token_request(path: str, payload: dict[str, str], secret: str) -> dict[str, Any]:
    response = requests.post(
        f"{API}{path}",
        json=payload,
        headers={"Accept": "application/json", "User-Agent": "Qoder"},
        timeout=20,
    )
    if response.status_code != 200:
        body = _redact(response.text, secret, limit=300)
        raise RuntimeError(f"{path.rsplit('/', 1)[-1]} 失败 HTTP {response.status_code}: {body}")
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(f"{path.rsplit('/', 1)[-1]} 返回了非 JSON 响应") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"{path.rsplit('/', 1)[-1]} 返回格式异常")
    return result


def exchange_pat(pat: str) -> dict[str, Any]:
    """用 PAT 换取短时 job token。"""
    return _token_request("/api/v1/jobToken/exchange", {"personal_token": pat}, pat)


def refresh_job_token(refresh_token: str) -> dict[str, Any]:
    """用 refresh token 换取新的 job token。"""
    return _token_request(
        "/api/v1/jobToken/refresh", {"refreshToken": refresh_token}, refresh_token
    )


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取凭证文件 {path} 失败：{exc}") from exc


def _normalise_credentials(data: Any) -> list[tuple[str, dict[str, Any]]]:
    entries = data if isinstance(data, list) else [data]
    credentials: list[tuple[str, dict[str, Any]]] = []
    seen: set[bytes] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"凭证第 {index} 项必须是 JSON 对象")
        label = str(entry.get("label") or "")
        credential = entry
        if not any(key in entry for key in ("pat", "token", "accessToken")):
            credential = entry.get("auth")
        if not isinstance(credential, dict):
            raise ValueError(f"凭证第 {index} 项不是 PAT、job token 或 auth 包装对象")
        source_secret = credential.get("pat") or credential.get("token") or credential.get("accessToken")
        if not isinstance(source_secret, str) or not source_secret:
            raise ValueError(f"凭证第 {index} 项缺少 pat、token 或 accessToken")
        fingerprint = hashlib.sha256(source_secret.encode("utf-8")).digest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        user = credential.get("user") or {}
        if not label and isinstance(user, dict):
            label = str(user.get("name") or "")
        credentials.append((label or f"账号{index}", credential))
    return credentials


def load_credentials(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    """按命令行、白虎机密变量、挂载文件、本地客户端顺序加载凭证。"""
    if args.pat:
        return _normalise_credentials({"pat": args.pat})
    if args.auth_file:
        return _normalise_credentials(_read_json_file(Path(args.auth_file).expanduser()))

    raw = os.environ.get("QODER_GLOBAL_AUTHS", "").strip()
    if raw:
        try:
            return _normalise_credentials(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"QODER_GLOBAL_AUTHS 不是合法 JSON：{exc.msg}") from exc

    pat = os.environ.get("QODER_GLOBAL_PAT", "").strip()
    if pat:
        return _normalise_credentials({"pat": pat})

    configured_file = os.environ.get("QODER_GLOBAL_AUTH_FILE", "").strip()
    if configured_file:
        return _normalise_credentials(_read_json_file(Path(configured_file).expanduser()))
    if AUTH_FILE.is_file():
        return _normalise_credentials(_read_json_file(AUTH_FILE))
    return _normalise_credentials(load_client_auth())


def export_client_auth(path: Path) -> None:
    """显式导出 Windows 客户端凭证，供迁移到白虎机密变量。"""
    auth = load_client_auth()
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
    log(f"凭证已导出到 {path}；请保存到白虎机密变量并妥善删除本地副本")


def make_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Qoder",
            "Authorization": f"Bearer {token}",
            "Cosy-ClientType": os.environ.get("QODER_GLOBAL_CLIENT_TYPE", DEFAULT_CLIENT_TYPE),
            "Cosy-Version": os.environ.get("QODER_GLOBAL_VERSION", DEFAULT_VERSION),
        }
    )
    return session


def get(
    session: requests.Session, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = session.get(API + path, params=params, timeout=20)
    token = _session_token(session)
    if response.status_code != 200:
        return {
            "_error": response.status_code,
            "_body": _redact(response.text, token, limit=400),
        }
    try:
        body = response.json()
    except requests.exceptions.JSONDecodeError:
        return {
            "_error": "响应不是 JSON",
            "_body": _redact(response.text, token, limit=400),
        }
    if not isinstance(body, dict):
        return {"_error": "响应格式异常", "_body": type(body).__name__}
    return body


def claim_one(session: requests.Session, campaign_id: str) -> dict[str, Any]:
    response = session.post(f"{API}/sash/api/v1/me/campaigns/{campaign_id}/claim", timeout=20)
    token = _session_token(session)
    try:
        parsed = response.json()
    except requests.exceptions.JSONDecodeError:
        parsed = {"_raw": _redact(response.text, token, limit=300)}
    body = parsed if isinstance(parsed, dict) else {"_raw": str(parsed)[:300]}
    body["_http"] = response.status_code
    return body


def _number(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "?"


def print_quota(
    session: requests.Session,
    prefix: str = "剩余额度",
    cached: dict[str, Any] | None = None,
) -> bool:
    quota = cached if cached is not None else get(session, ENDPOINTS["quota"][0])
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
        benefit = benefit if isinstance(benefit, dict) else {}
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
        and campaign.get("actionType") == "CLAIM_BENEFIT"
    ]
    if not targets:
        log("没有 CLAIMABLE 的活动，无需签到")
        return 0
    if dry_run:
        log(f"[dry-run] 将领取 {len(targets)} 条：{[item.get('campaignKey') for item in targets]}")
        return 0

    claimed = False
    failures = 0
    for campaign in targets:
        result = claim_one(session, str(campaign["campaignId"]))
        benefit = result.get("benefit") or {}
        benefit = benefit if isinstance(benefit, dict) else {}
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


def show_usage(
    session: requests.Session,
    auth: dict[str, Any],
    args: argparse.Namespace,
    initial_quota: dict[str, Any],
) -> int:
    user = auth.get("user") or {}
    user = user if isinstance(user, dict) else {}
    summary = get(session, ENDPOINTS["credits-summary"][0])
    streak = get(session, ENDPOINTS["seat-activity"][0])
    heat = get(
        session,
        ENDPOINTS["credits-heatmap"][0],
        {"days": args.days, "organization_id": ""},
    )

    log(f"账号      : {user.get('name')} ({user.get('id')})")
    log(f"token 到期: {auth.get('expiresAt') or auth.get('expiresIn')}")
    failed = not print_quota(session, cached=initial_quota)
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


def _job_token(auth: dict[str, Any]) -> str:
    value = auth.get("accessToken") or auth.get("token")
    return str(value or "")


def run_account(credential: dict[str, Any], args: argparse.Namespace) -> int:
    runtime_auth = dict(credential)
    pat = str(credential.get("pat") or "")
    if pat:
        exchanged = exchange_pat(pat)
        existing_user = credential.get("user")
        runtime_auth = {**credential, **exchanged}
        if existing_user and not runtime_auth.get("user"):
            runtime_auth["user"] = existing_user

    token = _job_token(runtime_auth)
    refresh_token = str(runtime_auth.get("refreshToken") or "")
    if not token:
        raise ValueError("exchange/凭证中没有 accessToken 或 token")

    session = make_session(token)
    probe = get(session, ENDPOINTS["quota"][0])
    if probe.get("_error") in (401, 403) and refresh_token:
        log("job token 已失效，尝试使用 refresh token 刷新")
        refreshed = refresh_job_token(refresh_token)
        token = _job_token(refreshed)
        if not token:
            raise ValueError("刷新响应中没有 accessToken 或 token")
        runtime_auth.update(refreshed)
        session = make_session(token)
        probe = get(session, ENDPOINTS["quota"][0])
    if "_error" in probe:
        log(f"job token 校验失败：{probe['_error']} {probe.get('_body', '')[:120]}")
        return 1

    if not runtime_auth.get("user"):
        info = get(session, "/api/v1/userinfo")
        if "_error" not in info:
            runtime_auth["user"] = info

    if args.what == "claim":
        return do_claim(session, args.dry_run)
    if args.what in ENDPOINTS:
        if args.what == "quota":
            output = probe
        else:
            path, params = ENDPOINTS[args.what]
            if args.what == "credits-heatmap":
                params = {**(params or {}), "days": args.days}
            output = get(session, path, params)
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        log(_redact(rendered, pat, token))
        return 1 if "_error" in output else 0
    return show_usage(session, runtime_auth, args, probe)


def _notify(success_count: int, total: int) -> None:
    try:
        import baihu  # type: ignore[import-not-found]

        notify = getattr(baihu, "notify", None)
        if callable(notify):
            result = notify("Qoder 国际版用量与签到", f"成功 {success_count}/{total} 个账号")
            if inspect.isawaitable(result):
                asyncio.run(result)
    except Exception as exc:  # 通知失败不能覆盖任务结果。
        log(f"WARN: 白虎通知发送失败：{exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="读取 Qoder 国际版用量并领取活动权益")
    choices = ["usage", "claim", "export-auth", *ENDPOINTS]
    parser.add_argument("what", nargs="?", choices=choices, default="usage")
    parser.add_argument(
        "--days",
        type=int,
        default=os.environ.get("QODER_GLOBAL_HEATMAP_DAYS", "30"),
        help="credits-heatmap 的天数，默认 30",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出将领取的活动")
    parser.add_argument("--auth-file", help="凭证 JSON 文件（支持 PAT 或 job token）")
    parser.add_argument("--pat", help="Personal Access Token；命令行可能泄露，优先使用环境变量")
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
        credentials = load_credentials(args)
    except (OSError, RuntimeError, ValueError) as exc:
        log(f"ERROR: 加载凭证失败：{exc}", error=True)
        return 1
    if not credentials:
        log("ERROR: 没有可用的 Qoder 国际版凭证", error=True)
        return 1

    results: list[bool] = []
    for index, (label, credential) in enumerate(credentials, start=1):
        log(f"===== 账号 {index}: {label} =====")
        try:
            results.append(run_account(credential, args) == 0)
        except Exception as exc:  # 单账号失败不能中断后续账号。
            safe_error = _redact(
                str(exc),
                str(credential.get("pat") or ""),
                str(credential.get("token") or credential.get("accessToken") or ""),
                str(credential.get("refreshToken") or ""),
            )
            log(f"账号 {label} 处理失败：{safe_error}", error=True)
            results.append(False)
    success_count = sum(results)
    log(f"[Qoder Global] 执行完成：成功 {success_count}/{len(results)}")
    _notify(success_count, len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
