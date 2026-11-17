#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蹬蹬堂订阅共享池每日签到（白虎面板版）。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests


BASE_URL = "https://ai01.teleapi.top"
LOGIN_PATH = "/ipcheck/api/pool/local_login"
CHECKIN_PATH = "/ipcheck/api/pool/checkin"
POOL_PAGE_PATH = "/ipcheck/pool"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "checkin.log"


@dataclass(frozen=True)
class Account:
    label: str
    account: str
    password: str


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def log(message: str, *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as exc:
        print(f"[蹬蹬堂订阅池] 写入日志文件失败：{exc}", file=sys.stderr, flush=True)


def _redact(text: str, *secrets: str, limit: int = 300) -> str:
    safe = str(text)
    for secret in secrets:
        if secret and len(secret) >= 3:
            safe = safe.replace(secret, "[REDACTED]")
    return safe[:limit]


def _response_payload(response: requests.Response, *secrets: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError) as exc:
        body = _redact(response.text, *secrets)
        raise RuntimeError(f"HTTP {response.status_code} 返回了非 JSON 响应：{body}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {response.status_code} 返回格式异常")
    return payload


def _is_cloudflare_challenge(response: requests.Response) -> bool:
    marker = response.headers.get("cf-mitigated", "").lower()
    if marker == "challenge":
        return True
    body = (response.text or "").lower()
    return "just a moment" in body or "cf-chl-" in body or "challenge-platform" in body


def _api_message(payload: dict[str, Any], response: requests.Response) -> str:
    message = payload.get("error") or payload.get("message") or payload.get("msg")
    return str(message or response.reason or f"HTTP {response.status_code}")


def _request_json(
    session: requests.Session,
    url: str,
    body: dict[str, Any],
    *secrets: str,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}{POOL_PAGE_PATH}",
        "User-Agent": USER_AGENT,
    }
    response = session.post(url, json=body, headers=headers, timeout=30)
    if _is_cloudflare_challenge(response):
        raise RuntimeError(
            f"Cloudflare 人机验证拦截(HTTP {response.status_code})，"
            "请检查白虎出口 IP 或稍后重试"
        )

    payload = _response_payload(response, *secrets)
    if not response.ok:
        raise ApiError(
            _redact(_api_message(payload, response), *secrets),
            response.status_code,
            payload,
        )
    error = payload.get("error")
    if error:
        raise ApiError(_redact(str(error), *secrets), response.status_code, payload)
    return payload


def _is_already_checked_in(message: str, payload: dict[str, Any] | None = None) -> bool:
    data = payload or {}
    for key in ("already_checked_in", "alreadyCheckedIn", "already"):
        if data.get(key) is True:
            return True
    if data.get("checked_in") is True or data.get("checkedIn") is True:
        return True

    text = message.strip().lower()
    markers = (
        "今日已签到",
        "今天已签到",
        "已经签到",
        "签到过了",
        "重复签到",
        "already checked in",
        "already_checked_in",
    )
    return any(marker in text for marker in markers)


def _format_success(payload: dict[str, Any], already: bool = False) -> str:
    if already or _is_already_checked_in("", payload):
        return "今日已签到"

    parts: list[str] = []
    points = payload.get("points")
    balance = payload.get("balance")
    streak = payload.get("streak")
    if points is not None:
        parts.append(f"+{points} 虾仁")
    if balance is not None:
        parts.append(f"余额 {balance} 虾仁")
    if streak is not None:
        parts.append(f"连续签到 {streak} 天")
    return "签到成功" + (f"，{'，'.join(parts)}" if parts else "")


def load_accounts() -> list[Account]:
    """加载多账号 JSON 或单账号变量；多账号配置优先。"""
    raw_accounts = os.environ.get("IPCHECK_POOL_ACCOUNTS", "").strip()
    if raw_accounts:
        try:
            document = json.loads(raw_accounts)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "IPCHECK_POOL_ACCOUNTS 不是合法 JSON："
                f"{exc.msg}（第 {exc.lineno} 行，第 {exc.colno} 列）"
            ) from exc
        if not isinstance(document, list):
            raise ValueError("IPCHECK_POOL_ACCOUNTS 必须是 JSON 数组")

        accounts: list[Account] = []
        seen: set[str] = set()
        for index, item in enumerate(document, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"IPCHECK_POOL_ACCOUNTS 第 {index} 项必须是对象")
            account = str(item.get("account") or item.get("username") or "").strip()
            password = str(item.get("password") or item.get("pass") or "")
            if not account or not password:
                raise ValueError(f"IPCHECK_POOL_ACCOUNTS 第 {index} 项缺少 account 或 password")
            fingerprint = account.lower()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            label = str(item.get("label") or f"账号{len(accounts) + 1}").strip()
            accounts.append(Account(label=label, account=account, password=password))
        return accounts

    account = os.environ.get("IPCHECK_POOL_ACCOUNT", "").strip()
    password = os.environ.get("IPCHECK_POOL_PASSWORD", "")
    if account and password:
        return [Account(label="账号1", account=account, password=password)]
    if account or password:
        raise ValueError("IPCHECK_POOL_ACCOUNT 和 IPCHECK_POOL_PASSWORD 必须同时填写")
    return []


def checkin_account(
    account: Account,
    *,
    session_factory: Callable[[], requests.Session] = requests.Session,
) -> bool:
    """登录并签到；成功或今日已签到均返回 True。"""
    session = session_factory()
    try:
        _request_json(
            session,
            f"{BASE_URL}{LOGIN_PATH}",
            {"account": account.account, "password": account.password},
            account.account,
            account.password,
        )
        log(f"[{account.label}] 账号密码登录成功")

        try:
            payload = _request_json(
                session,
                f"{BASE_URL}{CHECKIN_PATH}",
                {},
                account.account,
                account.password,
            )
        except ApiError as exc:
            if _is_already_checked_in(str(exc), exc.payload):
                log(f"[{account.label}] 今日已签到")
                return True
            raise

        already = _is_already_checked_in("", payload)
        log(f"[{account.label}] {_format_success(payload, already=already)}")
        return True
    except (requests.RequestException, ApiError, RuntimeError) as exc:
        safe_error = _redact(str(exc), account.account, account.password)
        log(f"[{account.label}] 执行失败：{safe_error}", error=True)
        return False


def main() -> int:
    log("=" * 50)
    log("[蹬蹬堂订阅池] 开始每日签到")
    log("=" * 50)
    try:
        accounts = load_accounts()
    except ValueError as exc:
        log(f"ERROR: 加载账号失败：{exc}", error=True)
        return 1
    if not accounts:
        log(
            "ERROR: 没有可用账号，请配置 IPCHECK_POOL_ACCOUNT/IPCHECK_POOL_PASSWORD "
            "或 IPCHECK_POOL_ACCOUNTS",
            error=True,
        )
        return 1

    results = [checkin_account(account) for account in accounts]
    success_count = sum(results)
    log(f"[蹬蹬堂订阅池] 执行完成：成功 {success_count}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
