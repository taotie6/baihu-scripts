#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蹬蹬堂订阅共享池每日签到（白虎面板版）。"""

from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests


APP_VERSION = "1.0.4"
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


def _normalise_accounts_text(text: str) -> str:
    """移除机密变量中常见的 JSON 外层包装和复制粘贴字符。"""
    invisible = "\ufeff\u200b\u200e\u200f\u2060"
    value = text.strip().strip(invisible).strip()
    prefix = "IPCHECK_POOL_ACCOUNTS="

    for _ in range(3):
        before = value
        value = value.strip().strip(invisible).strip()
        # 部分白虎版本会把 textarea 内容按 HTML 实体保存，例如 &quot;、&#34;。
        value = html.unescape(value).strip().strip(invisible).strip()
        if value.startswith(prefix):
            value = value[len(prefix) :].strip().strip(invisible).strip()

        if value.startswith("```") and value.endswith("```"):
            lines = value.splitlines()
            if len(lines) >= 3:
                value = "\n".join(lines[1:-1]).strip().strip(invisible).strip()

        # 白虎编辑器可能把整段 JSON 再包成 JSON 字符串，例如
        # "[{\"account\":\"user\"}]"；json.loads 会正确还原中文和转义引号。
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, str) and decoded.strip():
                value = decoded.strip()
            elif value[1:].lstrip().startswith(("[", "{", "［", "｛")):
                # HTML 实体只编码了外层引号时，内部的 JSON 引号可能已解码，
                # 此时无法作为 JSON 字符串解析，但可以安全地去掉外层引号。
                value = value[1:-1].strip()

        if len(value) >= 2 and value[0] == value[-1] == "'":
            inner = value[1:-1].strip()
            if inner.startswith(("[", "{", "［", "｛")):
                value = inner

        outer_pairs = {"［": ("[", "］", "]"), "｛": ("{", "｝", "}")}
        if value and value[0] in outer_pairs:
            opening, fullwidth_closing, closing = outer_pairs[value[0]]
            if value.endswith(fullwidth_closing):
                value = opening + value[1:-1] + closing

        if value == before:
            break
    return value


def _restore_escaped_layout(text: str) -> str:
    """还原 JSON 字符串外被面板转义成字面量的换行和缩进。"""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    replacements = {"n": "\n", "r": "\r", "t": "\t"}
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in ("\n", "\r"):
                # 复制多行 JSON 时常见的行尾续行符：},\n 或 },\r\n。
                index += 2
                if following == "\r" and index < len(text) and text[index] == "\n":
                    index += 1
                continue
            if following in replacements:
                output.append(replacements[following])
                index += 2
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _json_candidates(text: str) -> list[str]:
    """返回原文及从任意 [/{ 起点到最后一个 ]/} 的可解析片段。"""
    candidates = [text]
    # 本应用只接受账号数组；不要误把内部账号对象当成完整配置。
    pairs = {"[": "]"}
    for start, opening in enumerate(text[:4096]):
        closing = pairs.get(opening)
        if closing is None:
            continue
        end = text.rfind(closing)
        if end > start:
            candidate = text[start : end + 1]
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _parse_accounts_json(raw_accounts: str) -> Any:
    normalised = _normalise_accounts_text(raw_accounts)
    restored = _restore_escaped_layout(normalised)
    candidates: list[str] = []
    for base in (normalised, restored):
        for candidate in _json_candidates(base):
            if candidate not in candidates:
                candidates.append(candidate)

    error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            error = exc

    assert error is not None
    target = candidates[-1]
    if not target:
        hint = "变量内容为空"
    elif target[0] not in "[{":
        hint = f"开头字符不是 [ 或 {{（U+{ord(target[0]):04X}）"
    else:
        hint = "请检查指定位置附近的逗号、引号或括号"
    raise ValueError(
        "IPCHECK_POOL_ACCOUNTS 不是合法 JSON："
        f"{error.msg}（第 {error.lineno} 行，第 {error.colno} 列）。"
        + hint
        + f"（当前脚本 v{APP_VERSION}，已支持 HTML 实体、包装前缀和转义换行）"
    ) from error


def load_accounts() -> list[Account]:
    """加载多账号 JSON 或单账号变量；多账号配置优先。"""
    raw_accounts = os.environ.get("IPCHECK_POOL_ACCOUNTS", "").strip()
    if raw_accounts:
        document = _parse_accounts_json(raw_accounts)
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
    log(f"[蹬蹬堂订阅池] 开始每日签到（脚本 v{APP_VERSION}）")
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
