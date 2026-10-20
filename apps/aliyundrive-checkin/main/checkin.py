#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云盘每日签到（白虎面板版）。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


AUTH_URL = "https://auth.aliyundrive.com/v2/account/token"
SIGN_URL = "https://member.aliyundrive.com/v1/activity/sign_in_list"
REWARD_URL = "https://member.aliyundrive.com/v1/activity/sign_in_reward"
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "checkin.log"


def log(message: str, *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as exc:
        print(f"[阿里云盘] 写入日志文件失败：{exc}", file=sys.stderr, flush=True)


def _redact(text: str, *secrets: str, limit: int = 300) -> str:
    safe = text
    for secret in secrets:
        if secret and len(secret) >= 8:
            safe = safe.replace(secret, "[REDACTED]")
    return safe[:limit]


def _response_json(response: requests.Response, secret: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        body = _redact(response.text, secret)
        raise RuntimeError(f"HTTP {response.status_code} 返回了非 JSON 响应：{body}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {response.status_code} 返回格式异常")
    return payload


def update_token(refresh_token: str) -> tuple[str, str]:
    """使用 refresh token 换取 access token，并返回服务端的新 refresh token。"""
    response = requests.post(
        AUTH_URL,
        json={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    payload = _response_json(response, refresh_token)
    if not response.ok:
        message = payload.get("message") or payload.get("code") or response.reason
        raise RuntimeError(
            f"刷新登录态失败(HTTP {response.status_code})：{_redact(str(message), refresh_token)}"
        )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        message = payload.get("message") or payload.get("code") or "响应中无 access_token"
        raise RuntimeError(f"刷新登录态失败：{_redact(str(message), refresh_token)}")
    new_refresh_token = payload.get("refresh_token")
    return access_token, str(new_refresh_token or refresh_token)


def _post_member_api(
    url: str,
    access_token: str,
    payload: dict[str, Any],
) -> tuple[requests.Response, dict[str, Any]]:
    headers = {"Authorization": access_token, "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    return response, _response_json(response, access_token)


def _today_log(result: dict[str, Any]) -> dict[str, Any]:
    logs = result.get("signInLogs") or []
    logs = [item for item in logs if isinstance(item, dict)] if isinstance(logs, list) else []
    if not logs:
        return {}

    sign_days = result.get("signInCount")
    for item in logs:
        day = item.get("day") if item.get("day") is not None else item.get("signInDay")
        if str(day) == str(sign_days):
            return item
    for index, item in enumerate(logs):
        if item.get("status") == "miss" and index > 0:
            return logs[index - 1]
    completed = [item for item in logs if item.get("status") != "miss"]
    return completed[-1] if completed else logs[-1]


def _reward_text(today: dict[str, Any], reward_payload: dict[str, Any]) -> str:
    if today.get("isReward") is False:
        return "今日没有奖励"
    reward = reward_payload.get("result") or today.get("reward") or {}
    if isinstance(reward, dict):
        name = str(reward.get("name") or reward.get("rewardName") or "")
        description = str(reward.get("description") or reward.get("rewardDescription") or "")
        text = f"{name}{description}".strip()
        if text:
            return f"获得奖励：{text}"
    return "奖励领取成功"


def sign(name: str, access_token: str) -> bool:
    """完成签到并领取当天奖励。"""
    response, payload = _post_member_api(SIGN_URL, access_token, {})
    if not response.ok:
        message = payload.get("message") or payload.get("code") or response.reason
        log(f"[{name}] 签到失败(HTTP {response.status_code})：{_redact(str(message), access_token)}")
        return False
    if payload.get("success") is False:
        message = payload.get("message") or payload.get("code") or "未知错误"
        log(f"[{name}] 签到失败：{_redact(str(message), access_token)}")
        return False
    result = payload.get("result")
    if not isinstance(result, dict):
        log(f"[{name}] 签到响应缺少 result")
        return False

    sign_days = result.get("signInCount")
    if sign_days is None:
        log(f"[{name}] 签到响应缺少 signInCount")
        return False
    today = _today_log(result)
    reward_response, reward_payload = _post_member_api(
        REWARD_URL, access_token, {"signInDay": sign_days}
    )
    message = str(reward_payload.get("message") or "")
    already_claimed = "已领取" in message or "already" in message.casefold()
    if (not reward_response.ok and not already_claimed) or (
        reward_payload.get("success") is False and not already_claimed
    ):
        detail = message or str(reward_payload.get("code") or reward_response.reason)
        log(
            f"[{name}] 签到成功（累计 {sign_days} 天），但奖励领取失败："
            f"{_redact(detail, access_token)}"
        )
        return False

    reward_text = "奖励已领取" if already_claimed else _reward_text(today, reward_payload)
    log(f"[{name}] 签到成功，累计 {sign_days} 天，{reward_text}")
    return True


def _normalise_accounts(data: Any) -> list[tuple[str, str]]:
    if isinstance(data, dict) and "ALIYUN" in data and not any(
        key in data for key in ("refresh_token", "refreshToken")
    ):
        data = data["ALIYUN"]
    entries = data if isinstance(data, list) else [data]
    accounts: list[tuple[str, str]] = []
    seen: set[bytes] = set()
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, str):
            label, refresh_token = f"账号{index}", entry
        elif isinstance(entry, dict):
            label = str(entry.get("label") or entry.get("name") or f"账号{index}")
            refresh_token = entry.get("refresh_token") or entry.get("refreshToken")
        else:
            raise ValueError(f"账号配置第 {index} 项必须是字符串或 JSON 对象")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise ValueError(f"账号配置第 {index} 项缺少 refresh_token")
        refresh_token = refresh_token.strip()
        fingerprint = hashlib.sha256(refresh_token.encode("utf-8")).digest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        accounts.append((label, refresh_token))
    return accounts


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取账号文件 {path} 失败：{exc}") from exc


def _normalise_accounts_text(text: str) -> str:
    """移除复制粘贴或机密存储常见的 JSON 外层包装。"""
    invisible = "\ufeff\u200b\u200e\u200f\u2060"
    value = text.strip().strip(invisible).strip()

    prefix = "ALIYUNDRIVE_ACCOUNTS="
    if value.startswith(prefix):
        value = value[len(prefix) :].strip().strip(invisible).strip()

    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            value = "\n".join(lines[1:-1]).strip().strip(invisible).strip()

    if len(value) >= 2 and value[0] == value[-1] == "'":
        inner = value[1:-1].strip()
        if inner.startswith(("[", "{", "［", "｛")):
            value = inner

    outer_pairs = {"［": ("[", "］", "]"), "｛": ("{", "｝", "}")}
    if value and value[0] in outer_pairs:
        opening, fullwidth_closing, closing = outer_pairs[value[0]]
        if value.endswith(fullwidth_closing):
            value = opening + value[1:-1] + closing
    return value


def _restore_escaped_layout(text: str) -> str:
    """还原 JSON 字符串外被面板转义的换行和缩进。"""
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
        if char == "\\" and index + 1 < len(text) and text[index + 1] in replacements:
            output.append(replacements[text[index + 1]])
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _parse_accounts_json(raw_accounts: str) -> Any:
    normalised = _normalise_accounts_text(raw_accounts)
    try:
        return json.loads(normalised)
    except json.JSONDecodeError as first_error:
        restored = _restore_escaped_layout(normalised)
        if restored != normalised:
            try:
                return json.loads(restored)
            except json.JSONDecodeError as restored_error:
                error = restored_error
        else:
            error = first_error
        if not restored:
            hint = "变量内容为空"
        elif restored[0] not in "[{":
            hint = f"开头字符不是 [ 或 {{（U+{ord(restored[0]):04X}）"
        else:
            hint = "请检查指定位置附近的逗号、引号或括号"
        raise ValueError(
            "ALIYUNDRIVE_ACCOUNTS 不是合法 JSON："
            f"{error.msg}（第 {error.lineno} 行，第 {error.colno} 列）。"
            + hint
        ) from error


def load_accounts(args: argparse.Namespace) -> list[tuple[str, str]]:
    """按命令行文件、多账号变量、单账号变量、挂载文件顺序加载。"""
    if args.accounts_file:
        return _normalise_accounts(_load_json_file(Path(args.accounts_file).expanduser()))

    raw_accounts = os.environ.get("ALIYUNDRIVE_ACCOUNTS", "").strip()
    if raw_accounts:
        return _normalise_accounts(_parse_accounts_json(raw_accounts))

    refresh_token = os.environ.get("ALIYUNDRIVE_REFRESH_TOKEN", "").strip()
    if refresh_token:
        return _normalise_accounts(refresh_token)

    configured_file = os.environ.get("ALIYUNDRIVE_ACCOUNTS_FILE", "").strip()
    if configured_file:
        return _normalise_accounts(_load_json_file(Path(configured_file).expanduser()))
    return []


def checkin_account(name: str, refresh_token: str) -> bool:
    try:
        access_token, _ = update_token(refresh_token)
        return sign(name, access_token)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        safe_error = _redact(str(exc), refresh_token)
        log(f"[{name}] 执行失败：{safe_error}", error=True)
        return False


def _notify(success_count: int, total: int) -> None:
    try:
        import baihu  # type: ignore[import-not-found]

        notify = getattr(baihu, "notify", None)
        if callable(notify):
            result = notify("阿里云盘每日签到", f"成功 {success_count}/{total} 个账号")
            if inspect.isawaitable(result):
                asyncio.run(result)
    except Exception as exc:  # 通知失败不能覆盖签到结果。
        log(f"WARN: 白虎通知发送失败：{exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阿里云盘每日签到")
    parser.add_argument("--accounts-file", help="账号 JSON 文件；兼容原 config.json 的 ALIYUN 字段")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("=" * 50)
    log("[阿里云盘] 开始每日签到")
    log("=" * 50)
    try:
        accounts = load_accounts(args)
    except ValueError as exc:
        log(f"ERROR: 加载账号失败：{exc}", error=True)
        return 1
    if not accounts:
        log("ERROR: 没有可用账号，请配置 ALIYUNDRIVE_REFRESH_TOKEN 或 ALIYUNDRIVE_ACCOUNTS", error=True)
        return 1

    results = [checkin_account(name, refresh_token) for name, refresh_token in accounts]
    success_count = sum(results)
    log(f"[阿里云盘] 执行完成：成功 {success_count}/{len(results)}")
    _notify(success_count, len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
