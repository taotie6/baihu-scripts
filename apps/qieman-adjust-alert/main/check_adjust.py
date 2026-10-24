#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""且慢顾投调仓提醒（白虎面板版）。

工作日 14:40 检查且慢公开接口的“最新调仓”，当日有调仓批次时输出
NOTIFY_UPDATE=1 标记日志，由白虎任务通知结合日志变量正则提取实现
“有更新才通知”。可选配置 BHPKG_* 机密变量，改由脚本直接调用白虎
通知接口推送。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


QIEMAN_BASE_URL = "https://qieman.com"
MAX_ENV_CANDIDATES = 4
DEFAULT_PLANS = OrderedDict(
    (
        ("LONG_WIN", "长赢指数150计划"),
        ("LONG_WIN_S", "长赢指数S计划"),
    )
)
ORDER_CODE_MAP = {"022": "买入", "024": "卖出"}
DEFAULT_BAIHU_NOTIFY_URL = "http://localhost:8052/api/v1/notify/send"
MARKER_SUMMARY_LIMIT = 120


def log(message: str, *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)


def parse_plans(raw: str) -> OrderedDict[str, str]:
    """解析 CODE:名称,CODE2:名称2；未提供时使用默认计划。"""
    if not raw.strip():
        return DEFAULT_PLANS.copy()
    plans: OrderedDict[str, str] = OrderedDict()
    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        code, separator, name = entry.partition(":")
        code = code.strip()
        name = name.strip() if separator else code
        if not code:
            raise ValueError(f"QIEMAN_PLANS 第 {position} 项缺少计划代码")
        if not name:
            raise ValueError(f"QIEMAN_PLANS 第 {position} 项缺少计划名称")
        plans[code] = name
    if not plans:
        raise ValueError("QIEMAN_PLANS 没有有效计划")
    return plans


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _timestamp(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _compact_number(value: Any) -> str:
    return f"{_number(value):g}"


def _timezone() -> ZoneInfo:
    name = os.environ.get("QIEMAN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log(f"WARN: 未找到时区 {name}，回退到 Asia/Shanghai")
        return ZoneInfo("Asia/Shanghai")


def today_str() -> str:
    """当前判定日期；独立成函数便于测试替换。"""
    return datetime.now(_timezone()).strftime("%Y-%m-%d")


def format_date(value: Any) -> str:
    timestamp = _timestamp(value)
    if not timestamp:
        return "未知日期"
    return datetime.fromtimestamp(timestamp / 1000, tz=_timezone()).strftime("%Y-%m-%d")


def format_time(value: Any) -> str:
    timestamp = _timestamp(value)
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp / 1000, tz=_timezone()).strftime("%H:%M")


def _env_candidates(raw: str, *, maximum: int = MAX_ENV_CANDIDATES) -> tuple[str, ...]:
    """解析白虎可能用 `&` 合并的同名变量，新值优先。"""
    values: list[str] = []
    for value in reversed(str(raw or "").split("&")):
        value = value.strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= maximum:
            break
    return tuple(values)


class QiemanClient:
    """且慢公开页面接口的检查客户端。"""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @staticmethod
    def _sign() -> str:
        timestamp = int(time.time() * 1000)
        payload = str(math.floor(1.01 * timestamp))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32].upper()
        return f"{timestamp}{digest}"

    def _headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": QIEMAN_BASE_URL,
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "X-sign": self._sign(),
        }

    def _get(self, path: str, *, params: dict[str, str], referer: str) -> Any:
        response = self.session.get(
            QIEMAN_BASE_URL + path,
            params=params,
            headers=self._headers(referer),
            timeout=30,
        )
        response.raise_for_status()
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(f"且慢接口 {path} 返回了非 JSON 响应") from exc

    def get_latest_adjustment(self, plan_code: str) -> dict[str, Any]:
        data = self._get(
            "/pmdj/v2/long-win/plan/adjustments/latest",
            params={"prodCode": plan_code},
            referer=f"{QIEMAN_BASE_URL}/longwin/compositions/{plan_code}",
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"计划 {plan_code} 的最新调仓响应格式异常")
        return data


def build_comment(orders: list[dict[str, Any]]) -> str:
    """接口未给出摘要时，用订单明细拼一句可读摘要。"""
    parts: list[str] = []
    for order in orders:
        action = ORDER_CODE_MAP.get(order["order_code"], order["order_code"] or "调整")
        name = order["variety"] or order["fund_name"] or order["fund_code"]
        if not name:
            continue
        units = (
            abs(order["trade_unit"])
            if order["order_code"] in ORDER_CODE_MAP
            else order["trade_unit"]
        )
        parts.append(f"{action}{name} {_compact_number(units)}份")
    return "；".join(parts)


def organise_adjustment(
    raw: dict[str, Any], plan_name: str, end_at: int = 0
) -> dict[str, Any] | None:
    """整理单个调仓批次；end_at 是响应顶层的跟车截止时间。"""
    adjustment_id = int(_number(raw.get("adjustmentId")))
    if not adjustment_id:
        return None
    orders: list[dict[str, Any]] = []
    for raw_order in raw.get("orders") or []:
        if not isinstance(raw_order, dict):
            continue
        raw_fund = raw_order.get("fund") or {}
        if not isinstance(raw_fund, dict):
            raw_fund = {}
        order = {
            "order_code": str(raw_order.get("orderCode") or ""),
            "variety": str(raw_order.get("variety") or ""),
            "fund_name": str(raw_fund.get("fundName") or ""),
            "fund_code": str(raw_fund.get("fundCode") or ""),
            "trade_unit": _number(raw_order.get("tradeUnit")),
            "post_plan_unit": (
                None
                if raw_order.get("postPlanUnit") is None
                else _number(raw_order.get("postPlanUnit"))
            ),
            "nav": _number(raw_order.get("nav")),
        }
        if order["variety"] or order["fund_name"] or order["fund_code"]:
            orders.append(order)
    comment = str(raw.get("comment") or "").strip()
    if not comment or comment == "--":
        comment = build_comment(orders) or f"调仓 #{adjustment_id}"
    return {
        "adjustment_id": adjustment_id,
        "comment": comment,
        "url": str(raw.get("url") or "").strip(),
        "create_time": _timestamp(raw.get("adjustCreateTime")),
        "txn_date": _timestamp(raw.get("adjustTxnDate")),
        "end_at": _timestamp(end_at),
        "orders": orders,
        "plans": [plan_name],
    }


def collect_adjustments(
    plan_responses: list[tuple[str, dict[str, Any]]], target_date: str | None
) -> list[dict[str, Any]]:
    """汇总各计划的最新调仓并按批次去重；target_date 为空时不过滤日期。"""
    merged: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for plan_name, response in plan_responses:
        raw_list = response.get("latestAdjustments")
        if not isinstance(raw_list, list):
            raise RuntimeError(f"计划 {plan_name} 的 latestAdjustments 格式异常")
        end_at = _timestamp(response.get("adjustTxnDateEndAt"))
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            item = organise_adjustment(raw, plan_name, end_at)
            if not item:
                continue
            existing = merged.get(item["adjustment_id"])
            if existing is not None:
                if plan_name not in existing["plans"]:
                    existing["plans"].append(plan_name)
            else:
                merged[item["adjustment_id"]] = item
    adjustments = list(merged.values())
    if target_date is not None:
        adjustments = [
            item for item in adjustments if format_date(item["txn_date"]) == target_date
        ]
    adjustments.sort(key=lambda item: (item["txn_date"], item["adjustment_id"]))
    return adjustments


def deadline_text(adjustment: dict[str, Any], now_ms: int | None = None) -> str:
    """跟车截止描述：窗口内提示截止时间，过期提示已截止。"""
    if not adjustment["end_at"]:
        return ""
    current = now_ms if now_ms is not None else int(time.time() * 1000)
    if current >= adjustment["end_at"]:
        return "跟车已截止"
    return f"跟车截止 {format_time(adjustment['end_at'])}"


def render_orders(adjustment: dict[str, Any]) -> str:
    parts: list[str] = []
    for order in adjustment["orders"]:
        action = ORDER_CODE_MAP.get(order["order_code"], order["order_code"] or "调整")
        name = order["variety"] or order["fund_name"] or order["fund_code"]
        if not name:
            continue
        units = (
            abs(order["trade_unit"])
            if order["order_code"] in ORDER_CODE_MAP
            else order["trade_unit"]
        )
        detail = f"{action}{name} {_compact_number(units)}份"
        if order["post_plan_unit"] is not None:
            detail += f"（买后 {_compact_number(order['post_plan_unit'])} 份）"
        if order["nav"] > 0:
            detail += f"@{_compact_number(order['nav'])}"
        parts.append(detail)
    return "；".join(parts)


def render_alert(
    adjustments: list[dict[str, Any]], target_date: str, *, now_ms: int | None = None
) -> str:
    if not adjustments:
        return f"今日（{target_date}）无调仓更新。"
    lines = [f"检测到今日（{target_date}）调仓："]
    for adjustment in adjustments:
        deadline = deadline_text(adjustment, now_ms)
        suffix = f"；{deadline}" if deadline else ""
        lines.append(
            f"- #{adjustment['adjustment_id']} {adjustment['comment']}"
            f"（涉及计划：{'、'.join(adjustment['plans'])}{suffix}）"
        )
        orders = render_orders(adjustment)
        if orders:
            lines.append(f"  {orders}")
        if adjustment["url"]:
            lines.append(f"  公告：{adjustment['url']}")
    return "\n".join(lines)


def compact_summary(
    adjustments: list[dict[str, Any]], *, now_ms: int | None = None
) -> str:
    parts: list[str] = []
    for adjustment in adjustments:
        deadline = deadline_text(adjustment, now_ms)
        short_deadline = (
            deadline.replace("跟车截止 ", "截止").replace("跟车已截止", "已截止")
            if deadline
            else ""
        )
        part = f"#{adjustment['adjustment_id']} {adjustment['comment']}"
        if short_deadline:
            part += f"（{short_deadline}）"
        parts.append(part)
    summary = "；".join(parts)
    if len(summary) > MARKER_SUMMARY_LIMIT:
        summary = summary[: MARKER_SUMMARY_LIMIT - 1].rstrip() + "…"
    return summary


def render_marker(
    adjustments: list[dict[str, Any]], *, now_ms: int | None = None
) -> str:
    """输出给白虎通知正则提取的稳定标记行。"""
    if not adjustments:
        return "NOTIFY_UPDATE=0 今日无调仓更新"
    return f"NOTIFY_UPDATE=1 {compact_summary(adjustments, now_ms=now_ms)}"


def _truncate(content: str, limit: int = 3500) -> str:
    if len(content) > limit:
        content = content[: max(1, limit - 8)].rstrip() + "\n…内容已截断"
    return content


def _first_candidate(raw: str, default: str = "") -> str:
    values = _env_candidates(raw)
    return values[0] if values else default


class BaihuPushConfig:
    """可选的白虎脚本直推配置；未配置或仅部分配置时返回 None。"""

    def __init__(self, token: str, channel: str, url: str) -> None:
        self.token = token
        self.channel = channel
        self.url = url

    @classmethod
    def from_env(cls) -> "BaihuPushConfig | None":
        token = _first_candidate(os.environ.get("BHPKG_NOTIFY_TOKEN", ""))
        channel = _first_candidate(os.environ.get("BHPKG_NOTIFY_CHANNEL", ""))
        if not token and not channel:
            return None
        if not token or not channel:
            log(
                "WARN: 白虎直推需要同时配置 BHPKG_NOTIFY_TOKEN 与 "
                "BHPKG_NOTIFY_CHANNEL，当前不完整，已跳过直推"
            )
            return None
        url = _first_candidate(
            os.environ.get("BHPKG_NOTIFY_URL", ""), DEFAULT_BAIHU_NOTIFY_URL
        )
        return cls(token, channel, url)


def push_baihu(config: BaihuPushConfig, title: str, content: str) -> bool:
    """通过白虎脚本调用接口推送一条 markdown 通知；失败只告警不中断。"""
    try:
        response = requests.post(
            config.url,
            headers={"notify-token": config.token},
            json={
                "channel_id": config.channel,
                "title": title,
                "content": _truncate(content),
                "options": {"format": "markdown"},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            raise RuntimeError(data.get("msg") or f"返回代码 {data.get('code')}")
        return True
    except Exception as exc:
        log(f"WARN: 白虎直推发送失败：{exc}", error=True)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查且慢顾投计划当日是否有调仓更新")
    parser.add_argument(
        "--plans",
        default=os.environ.get("QIEMAN_PLANS", ""),
        help="计划列表，格式 CODE:名称,CODE2:名称2",
    )
    parser.add_argument(
        "--date",
        default="",
        help="调试用：覆盖判定日期，格式 YYYY-MM-DD，默认今天（Asia/Shanghai）",
    )
    parser.add_argument(
        "--force-notify",
        action="store_true",
        help="调试用：忽略日期，按最新调仓强制走有更新分支，用于验证通知链路",
    )
    parser.add_argument("--json", action="store_true", help="额外输出整理后的完整 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plans = parse_plans(args.plans)
    except ValueError as exc:
        log(f"ERROR: {exc}", error=True)
        return 2

    target_date = args.date.strip()
    if target_date:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            log("ERROR: --date 需要 YYYY-MM-DD 格式", error=True)
            return 2
        log(f"调试模式：判定日期覆盖为 {target_date}")
    else:
        target_date = today_str()
    if args.force_notify:
        log("WARN: --force-notify 仅用于调试通知链路，忽略日期过滤")

    client = QiemanClient()
    plan_responses: list[tuple[str, dict[str, Any]]] = []
    failures: list[str] = []
    for code, name in plans.items():
        log(f"获取且慢计划：{name}（{code}）")
        try:
            plan_responses.append((name, client.get_latest_adjustment(code)))
        except Exception as exc:
            failures.append(f"{name}：{exc}")
            log(f"ERROR: {name} 获取最新调仓失败：{exc}", error=True)
    if not plan_responses:
        log("ERROR: 所有计划均获取失败", error=True)
        return 1

    try:
        adjustments = collect_adjustments(
            plan_responses, None if args.force_notify else target_date
        )
    except RuntimeError as exc:
        log(f"ERROR: {exc}", error=True)
        return 1

    alert = render_alert(adjustments, target_date)
    log("\n" + alert)
    log(render_marker(adjustments))
    if args.json:
        print(json.dumps(adjustments, ensure_ascii=False, indent=2))

    if adjustments:
        push_config = BaihuPushConfig.from_env()
        if push_config is not None:
            sent = push_baihu(push_config, f"且慢调仓提醒 {target_date}", alert)
            log("白虎直推：已发送" if sent else "白虎直推：发送失败，详见上方告警")

    if failures:
        log("执行存在失败：" + "；".join(failures), error=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
