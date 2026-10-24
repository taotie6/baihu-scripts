#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""且慢顾投计划同步（白虎面板版）。

默认抓取并整理计划持仓与调仓记录；配置完整的 FEISHU_* 机密变量后，
额外同步到飞书多维表格。执行结果由白虎任务通知统一推送。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
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
TEXT_FIELD = 1
NUMBER_FIELD = 2
SELECT_FIELD = 3
DATE_FIELD = 5
# 以下字段定义与原多维表格（全天候系统）逐列核对过：类型、单选选项、
# 数字与日期格式均保持一致，仅在新建表/字段时使用，已有字段永不改动。
HOLDING_FIELDS = [
    {"name": "基金代码", "type": TEXT_FIELD, "desc": "基金或投资标的代码，作为持仓同步的唯一键。"},
    {"name": "基金名称", "type": TEXT_FIELD, "desc": "基金或投资标的名称。"},
    {
        "name": "资产类别",
        "type": SELECT_FIELD,
        "desc": "且慢计划中的资产分类。",
        "property": {
            "options": [
                {"name": "A股", "color": 0},
                {"name": "海外成熟市场股票", "color": 1},
                {"name": "海外新兴市场股票", "color": 2},
                {"name": "境内债券", "color": 3},
                {"name": "海外债券", "color": 4},
                {"name": "原油", "color": 5},
                {"name": "黄金", "color": 6},
            ]
        },
    },
    {
        "name": "目标占比",
        "type": NUMBER_FIELD,
        "desc": "该标的在计划中的目标权重，原始值为 0–1 小数。",
        "property": {"formatter": "0.00%"},
    },
    {
        "name": "计划份数",
        "type": NUMBER_FIELD,
        "desc": "长赢计划分配给该标的计划份数。",
        "property": {"formatter": "0"},
    },
    {
        "name": "单位净值",
        "type": NUMBER_FIELD,
        "desc": "且慢返回的最新单位净值。",
        "property": {"formatter": "0.0000"},
    },
    {
        "name": "净值日期",
        "type": DATE_FIELD,
        "desc": "单位净值对应的日期。",
        "ui_type": "DateTime",
        "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"},
    },
    {
        "name": "主理人持有单价",
        "type": NUMBER_FIELD,
        "desc": "且慢公开计划中的主理人持有单价。",
        "property": {"formatter": "0.0000"},
    },
]
HISTORY_FIELDS = [
    {
        "name": "调仓ID",
        "type": NUMBER_FIELD,
        "desc": "且慢调仓批次 ID，与基金代码组成历史记录唯一键。",
        "property": {"formatter": "1,000"},
    },
    {"name": "基金代码", "type": TEXT_FIELD, "desc": "本条调仓明细对应的基金或投资标的代码。"},
    {"name": "基金名称", "type": TEXT_FIELD, "desc": "本条调仓明细对应的基金名称。"},
    {
        "name": "操作类型",
        "type": SELECT_FIELD,
        "desc": "买入、卖出或且慢返回的其他操作类型。",
        "property": {
            "options": [
                {"name": "买入", "color": 33},
                {"name": "卖出", "color": 26},
            ]
        },
    },
    {
        # 单选选项由同步数据自动累积，这里不预置。
        "name": "品种",
        "type": SELECT_FIELD,
        "desc": "且慢调仓记录中的品种名称。",
    },
    {
        "name": "成交净值",
        "type": NUMBER_FIELD,
        "desc": "本次买卖对应的成交净值。",
        "property": {"formatter": "0.0000"},
    },
    {
        "name": "净值日期",
        "type": DATE_FIELD,
        "desc": "成交净值对应的日期。",
        "ui_type": "DateTime",
        "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"},
    },
    {
        "name": "交易份数",
        "type": NUMBER_FIELD,
        "desc": "本次调仓的交易份数；正数为买入，负数为卖出。",
        "property": {"formatter": "1,000"},
    },
    {
        "name": "交易日期",
        "type": DATE_FIELD,
        "desc": "本次调仓实际发生的日期。",
        "ui_type": "DateTime",
        "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"},
    },
]


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


def positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), maximum)


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


def _timezone() -> ZoneInfo:
    name = os.environ.get("QIEMAN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log(f"WARN: 未找到时区 {name}，回退到 Asia/Shanghai")
        return ZoneInfo("Asia/Shanghai")


def format_date(value: Any) -> str:
    timestamp = _timestamp(value)
    if not timestamp:
        return "未知日期"
    return datetime.fromtimestamp(timestamp / 1000, tz=_timezone()).strftime("%Y-%m-%d")


def mask_token(value: str) -> str:
    """隐藏飞书 Token 主体，仅保留少量首尾字符供排错。"""
    token = str(value or "")
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


class QiemanClient:
    """且慢公开页面接口的同步客户端。"""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @staticmethod
    def _sign() -> str:
        import hashlib

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

    def get_plan_composition(self, plan_code: str) -> dict[str, Any]:
        data = self._get(
            "/pmdj/v2/long-win/plan",
            params={"prodCode": plan_code},
            referer=f"{QIEMAN_BASE_URL}/longwin/compositions/{plan_code}",
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"计划 {plan_code} 的组合响应格式异常")
        return data

    def get_plan_history(self, plan_code: str) -> list[dict[str, Any]]:
        data = self._get(
            "/pmdj/v2/long-win/plan/history",
            params={"prodCode": plan_code},
            referer=f"{QIEMAN_BASE_URL}/longwin/compositions/{plan_code}",
        )
        if not isinstance(data, list):
            raise RuntimeError(f"计划 {plan_code} 的调仓响应格式异常")
        return [item for item in data if isinstance(item, dict)]


def organise_plan(
    code: str,
    name: str,
    composition: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    history_limit: int,
) -> dict[str, Any]:
    """把接口响应整理成通知和可选飞书同步共用的稳定结构。"""
    groups: list[dict[str, Any]] = []
    all_funds: list[dict[str, Any]] = []
    raw_groups = composition.get("composition") or []
    if not isinstance(raw_groups, list):
        raw_groups = []

    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        class_name = str(raw_group.get("className") or "未分类")
        funds: list[dict[str, Any]] = []
        raw_funds = raw_group.get("compList") or []
        if not isinstance(raw_funds, list):
            raw_funds = []
        for raw_item in raw_funds:
            if not isinstance(raw_item, dict):
                continue
            raw_fund = raw_item.get("fund") or {}
            if not isinstance(raw_fund, dict):
                raw_fund = {}
            fund = {
                "fund_code": str(raw_fund.get("fundCode") or ""),
                "fund_name": str(raw_fund.get("fundName") or ""),
                "class_name": class_name,
                "target_percent": _number(raw_item.get("percent")),
                "plan_unit": _number(raw_item.get("planUnit")),
                "nav": _number(raw_item.get("nav")),
                "nav_date": _timestamp(raw_item.get("navDate")),
                "unit_value": (
                    None
                    if raw_item.get("unitValue") is None
                    else _number(raw_item.get("unitValue"))
                ),
            }
            if fund["fund_code"]:
                funds.append(fund)
                all_funds.append(fund)
        groups.append(
            {
                "class_name": class_name,
                "target_percent": _number(raw_group.get("percent")),
                "funds": funds,
            }
        )

    normalised_history: list[dict[str, Any]] = []
    for raw_item in history:
        raw_fund = raw_item.get("fund") or {}
        if not isinstance(raw_fund, dict):
            raw_fund = {}
        item = {
            "adjustment_id": int(_number(raw_item.get("adjustmentId"))),
            "fund_code": str(raw_fund.get("fundCode") or ""),
            "fund_name": str(raw_fund.get("fundName") or ""),
            "order_code": str(raw_item.get("orderCode") or ""),
            "variety": str(raw_item.get("variety") or ""),
            "nav": _number(raw_item.get("nav")),
            "nav_date": _timestamp(raw_item.get("navDate")),
            "trade_unit": _number(raw_item.get("tradeUnit")),
            "adjust_txn_date": _timestamp(raw_item.get("adjustTxnDate")),
        }
        if item["adjustment_id"] and item["fund_code"]:
            normalised_history.append(item)
    normalised_history.sort(
        key=lambda item: (item["adjust_txn_date"], item["adjustment_id"]), reverse=True
    )

    adjustments: list[dict[str, Any]] = []
    grouped: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    for item in normalised_history:
        grouped.setdefault(item["adjustment_id"], []).append(item)
    for adjustment_id, items in list(grouped.items())[:history_limit]:
        adjustments.append(
            {
                "adjustment_id": adjustment_id,
                "trade_date": max(item["adjust_txn_date"] for item in items),
                "items": items,
            }
        )

    active_funds = [
        fund
        for fund in all_funds
        if fund["target_percent"] > 0 or fund["plan_unit"] > 0
    ]
    active_funds.sort(key=lambda item: item["target_percent"], reverse=True)
    return {
        "code": code,
        "name": name,
        "invested_unit": _number(composition.get("investedUnit")),
        "active_fund_count": len(active_funds),
        "groups": groups,
        "top_funds": active_funds[:5],
        "all_funds": all_funds,
        "history": normalised_history,
        "recent_adjustments": adjustments,
    }


def _compact_number(value: Any) -> str:
    number = _number(value)
    return f"{number:g}"


def render_plan(report: dict[str, Any]) -> str:
    lines = [f"【{report['name']}】{report['code']}"]
    lines.append(
        f"持仓 {report['active_fund_count']} 只 · 已投入 {_compact_number(report['invested_unit'])} 份"
    )
    allocations = [
        f"{group['class_name']} {group['target_percent'] * 100:.2f}%"
        for group in report["groups"]
        if group["target_percent"] > 0
    ]
    if allocations:
        lines.append("配置：" + " / ".join(allocations))
    if report["top_funds"]:
        top = "、".join(
            f"{fund['fund_name']} {fund['target_percent'] * 100:.2f}%"
            for fund in report["top_funds"]
        )
        lines.append("前五持仓：" + top)

    adjustments = report["recent_adjustments"]
    if adjustments:
        lines.append("最近调仓：")
    for adjustment in adjustments:
        items = adjustment["items"]
        actions: list[str] = []
        for item in items[:4]:
            action = ORDER_CODE_MAP.get(item["order_code"], item["order_code"] or "调整")
            fund_name = item["variety"] or item["fund_name"] or item["fund_code"]
            units = (
                abs(item["trade_unit"])
                if item["order_code"] in ORDER_CODE_MAP
                else item["trade_unit"]
            )
            actions.append(
                f"{action}{fund_name} {_compact_number(units)}份"
                f"@{_compact_number(item['nav'])}"
            )
        if len(items) > 4:
            actions.append(f"另 {len(items) - 4} 项")
        lines.append(
            f"- {format_date(adjustment['trade_date'])} #{adjustment['adjustment_id']}："
            + "；".join(actions)
        )
    return "\n".join(lines)


def render_task_summary(
    reports: list[dict[str, Any]], feishu_status: str, failures: list[str] | None = None
) -> str:
    content = "\n\n".join(render_plan(report) for report in reports)
    if feishu_status:
        content += f"\n\n飞书：{feishu_status}"
    if failures:
        content += "\n\n异常：" + "；".join(failures)
    return content


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


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    app_token: str
    token_type: str
    app_id_candidates: tuple[str, ...] = ()
    app_secret_candidates: tuple[str, ...] = ()
    app_token_candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "app_id_candidates", self.app_id_candidates or (self.app_id,)
        )
        object.__setattr__(
            self,
            "app_secret_candidates",
            self.app_secret_candidates or (self.app_secret,),
        )
        object.__setattr__(
            self,
            "app_token_candidates",
            self.app_token_candidates or (self.app_token,),
        )

    def redact(self, message: Any) -> str:
        result = str(message or "")
        sensitive = (
            self.app_id_candidates
            + self.app_secret_candidates
            + self.app_token_candidates
        )
        for value in sorted(set(sensitive), key=len, reverse=True):
            if value:
                result = result.replace(value, "[REDACTED]")
        return result

    @classmethod
    def from_env(cls) -> FeishuConfig | None:
        values = {
            "FEISHU_APP_ID": _env_candidates(os.environ.get("FEISHU_APP_ID", "")),
            "FEISHU_APP_SECRET": _env_candidates(
                os.environ.get("FEISHU_APP_SECRET", "")
            ),
            "FEISHU_TABLE_TOKEN": _env_candidates(
                os.environ.get("FEISHU_TABLE_TOKEN", "")
            ),
        }
        configured = [key for key, value in values.items() if value]
        if not configured:
            return None
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("飞书为可选功能；启用时还需配置 " + "、".join(missing))
        token_types = _env_candidates(os.environ.get("FEISHU_TOKEN_TYPE", "bitable"))
        token_type = (token_types[0] if token_types else "bitable").lower()
        if token_type not in {"bitable", "wiki"}:
            raise ValueError("FEISHU_TOKEN_TYPE 只能是 bitable 或 wiki")
        return cls(
            app_id=values["FEISHU_APP_ID"][0],
            app_secret=values["FEISHU_APP_SECRET"][0],
            app_token=values["FEISHU_TABLE_TOKEN"][0],
            token_type=token_type,
            app_id_candidates=values["FEISHU_APP_ID"],
            app_secret_candidates=values["FEISHU_APP_SECRET"],
            app_token_candidates=values["FEISHU_TABLE_TOKEN"],
        )


class FeishuBitableClient:
    BASE_URL = "https://open.feishu.cn/open-apis"
    RETRYABLE_CODES = {1254002, 1254290, 1254291, 1254607, 1255040}

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self.app_token = config.app_token
        self.access_token = ""
        self._app_token_validated = False
        if config.token_type == "wiki":
            last_error = ""
            for position, token in enumerate(config.app_token_candidates, start=1):
                log(
                    f"飞书：正在解析 Wiki Token 候选 "
                    f"{position}/{len(config.app_token_candidates)}（{mask_token(token)}）"
                )
                try:
                    self.app_token = self._resolve_wiki_token(token)
                    self._app_token_validated = True
                    log(
                        "飞书：Wiki Token 已解析为多维表格 "
                        + mask_token(self.app_token)
                    )
                    break
                except Exception as exc:
                    last_error = config.redact(exc)
            else:
                raise RuntimeError(
                    f"飞书 Wiki Token 候选均解析失败：{last_error}"
                )

    def _tenant_token(self) -> str:
        pairs = [
            (app_id, app_secret)
            for app_id in self.config.app_id_candidates
            for app_secret in self.config.app_secret_candidates
        ][: MAX_ENV_CANDIDATES**2]
        last_error = "未返回访问令牌"
        for app_id, app_secret in pairs:
            response = requests.post(
                f"{self.BASE_URL}/auth/v3/tenant_access_token/internal/",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=30,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                last_error = "飞书返回了非 JSON 响应"
                continue
            if data.get("code") == 0 and data.get("tenant_access_token"):
                return str(data["tenant_access_token"])
            last_error = self.config.redact(data.get("msg") or data.get("code"))
        raise RuntimeError(
            "获取飞书 tenant_access_token 失败："
            f"{last_error}（已尝试 {len(pairs)} 组候选凭据）。"
            "请确认 FEISHU_APP_ID 与 FEISHU_APP_SECRET 来自同一个飞书自建应用；"
            "此步骤发生在 Wiki Token 解析之前，与 FEISHU_TABLE_TOKEN 无关"
        )

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(1, 6):
            if not self.access_token:
                self.access_token = self._tenant_token()
            response = requests.request(
                method,
                self.BASE_URL + endpoint,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                params=params,
                json=data,
                timeout=30,
            )
            try:
                result = response.json()
            except requests.exceptions.JSONDecodeError as exc:
                raise RuntimeError(f"飞书接口返回非 JSON 响应（HTTP {response.status_code}）") from exc
            code = result.get("code")
            if response.ok and code == 0:
                return result
            if response.status_code == 401:
                self.access_token = ""
            if (code in self.RETRYABLE_CODES or response.status_code >= 500) and attempt < 5:
                time.sleep(attempt * 2)
                continue
            message = self.config.redact(
                result.get("msg") or result.get("message") or response.reason
            )
            raise RuntimeError(f"飞书接口失败（HTTP {response.status_code}, code={code}）：{message}")
        raise RuntimeError("飞书接口重试次数已用尽")

    def _resolve_wiki_token(self, wiki_token: str) -> str:
        result = self.request(
            "GET",
            "/wiki/v2/spaces/get_node",
            params={"token": wiki_token, "obj_type": "wiki"},
        )
        node = result.get("data", {}).get("node", {})
        log(
            "飞书：Wiki 节点类型 "
            f"{node.get('obj_type') or '未知'}，对象 {mask_token(str(node.get('obj_token') or ''))}"
        )
        if node.get("obj_type") != "bitable" or not node.get("obj_token"):
            raise RuntimeError("FEISHU_TABLE_TOKEN 对应的 Wiki 节点不是多维表格")
        return str(node["obj_token"])

    def list_tables(self) -> list[dict[str, Any]]:
        if self.config.token_type == "bitable" and not self._app_token_validated:
            last_error = ""
            for position, token in enumerate(self.config.app_token_candidates, start=1):
                self.app_token = token
                try:
                    items = self._list_tables()
                    self._app_token_validated = True
                    if position > 1:
                        log(f"飞书：已切换到可用的 Table Token 候选 {position}")
                    return items
                except Exception as exc:
                    last_error = self.config.redact(exc)
            raise RuntimeError(
                f"飞书 Table Token 候选均不可用：{last_error}"
            )
        return self._list_tables()

    def _list_tables(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "GET", f"/bitable/v1/apps/{self.app_token}/tables", params=params
            )
            data = result.get("data", {})
            items.extend(item for item in data.get("items", []) if isinstance(item, dict))
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more"):
                return items

    @staticmethod
    def _field_payload(field: dict[str, Any]) -> dict[str, Any]:
        """建表/建字段时的完整字段定义；已有字段不会被更新，因此只在这里出现。"""
        payload: dict[str, Any] = {
            "field_name": str(field["name"]),
            "type": int(field["type"]),
            "description": {"disable_sync": False, "text": str(field["desc"])},
        }
        if field.get("ui_type"):
            payload["ui_type"] = str(field["ui_type"])
        if field.get("property") is not None:
            payload["property"] = field["property"]
        return payload

    def create_table(self, name: str, fields: list[dict[str, Any]]) -> str:
        result = self.request(
            "POST",
            f"/bitable/v1/apps/{self.app_token}/tables",
            data={
                "table": {
                    "name": name,
                    "fields": [self._field_payload(field) for field in fields],
                }
            },
        )
        table_id = result.get("data", {}).get("table_id")
        if not table_id:
            raise RuntimeError(f"飞书创建数据表「{name}」后未返回 table_id")
        return str(table_id)

    def ensure_table(
        self, name: str, fields: list[dict[str, Any]], tables: dict[str, str]
    ) -> str:
        if name in tables:
            log(f"飞书：找到数据表「{name}」（{tables[name]}）")
            return tables[name]
        log(f"飞书：创建数据表「{name}」，一次带出 {len(fields)} 个字段")
        tables[name] = self.create_table(name, fields)
        log(f"飞书：已创建数据表「{name}」（{tables[name]}）")
        return tables[name]

    def list_fields(self, table_id: str) -> dict[str, dict[str, Any]]:
        result = self.request(
            "GET", f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/fields"
        )
        return {
            str(item["field_name"]): item
            for item in result.get("data", {}).get("items", [])
            if isinstance(item, dict) and item.get("field_name")
        }

    def ensure_fields(self, table_id: str, expected: list[dict[str, Any]]) -> int:
        """只补建缺失字段；已有字段的说明、类型和显示属性一律不读不改。"""
        existing = self.list_fields(table_id)
        added = 0
        for field in expected:
            if str(field["name"]) in existing:
                continue
            self.request(
                "POST",
                f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/fields",
                data=self._field_payload(field),
            )
            added += 1
        return added

    def list_records(self, table_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "GET",
                f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records",
                params=params,
            )
            data = result.get("data", {})
            items.extend(item for item in data.get("items", []) if isinstance(item, dict))
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more"):
                return items

    @staticmethod
    def _chunks(items: list[dict[str, Any]], size: int = 500) -> Iterable[list[dict[str, Any]]]:
        for start in range(0, len(items), size):
            yield items[start : start + size]

    def batch_create(self, table_id: str, records: list[dict[str, Any]]) -> int:
        for chunk in self._chunks(records):
            self.request(
                "POST",
                f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records/batch_create",
                data={"records": chunk},
            )
        return len(records)

    def batch_update(self, table_id: str, records: list[dict[str, Any]]) -> int:
        for chunk in self._chunks(records):
            self.request(
                "POST",
                f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records/batch_update",
                data={"records": chunk},
            )
        return len(records)

    def sync_report(self, report: dict[str, Any], tables: dict[str, str]) -> str:
        log(f"飞书：开始同步计划「{report['name']}」（{report['code']}）")
        log(f"飞书：[1/2] 同步持仓组合 → 「{report['name']}」")
        holding_table = self.ensure_table(report["name"], HOLDING_FIELDS, tables)
        holding_added_fields = self.ensure_fields(holding_table, HOLDING_FIELDS)
        log(f"飞书：持仓表字段已就绪（补建 {holding_added_fields}，已有字段未改动）")
        existing_holdings = {
            str(record.get("fields", {}).get("基金代码")): str(record.get("record_id"))
            for record in self.list_records(holding_table)
            if record.get("record_id") and record.get("fields", {}).get("基金代码")
        }
        log(
            f"飞书：持仓目标 {len(report['all_funds'])} 条，"
            f"已有 {len(existing_holdings)} 条"
        )
        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        for fund in report["all_funds"]:
            fields = {
                "基金代码": fund["fund_code"],
                "基金名称": fund["fund_name"],
                "资产类别": fund["class_name"],
                "目标占比": fund["target_percent"],
                "计划份数": fund["plan_unit"],
                "单位净值": fund["nav"],
                "净值日期": fund["nav_date"] or None,
                "主理人持有单价": fund["unit_value"],
            }
            record_id = existing_holdings.get(fund["fund_code"])
            if record_id:
                updates.append({"record_id": record_id, "fields": fields})
            else:
                creates.append({"fields": fields})
        self.batch_create(holding_table, creates)
        self.batch_update(holding_table, updates)
        log(f"飞书：持仓同步完成，新增 {len(creates)}，更新 {len(updates)}")

        history_name = f"{report['name']}-买卖记录"
        log(f"飞书：[2/2] 增量同步买卖历史 → 「{history_name}」")
        history_table = self.ensure_table(history_name, HISTORY_FIELDS, tables)
        history_added_fields = self.ensure_fields(history_table, HISTORY_FIELDS)
        log(f"飞书：历史表字段已就绪（补建 {history_added_fields}，已有字段未改动）")
        existing_history = {
            (int(_number(fields.get("调仓ID"))), str(fields.get("基金代码")))
            for record in self.list_records(history_table)
            if isinstance((fields := record.get("fields")), dict)
            and fields.get("调仓ID") is not None
            and fields.get("基金代码")
        }
        log(
            f"飞书：历史表已有 {len(existing_history)} 条，"
            f"且慢返回 {len(report['history'])} 条"
        )
        history_creates: list[dict[str, Any]] = []
        skipped_history = 0
        for item in report["history"]:
            key = (item["adjustment_id"], item["fund_code"])
            if key in existing_history:
                skipped_history += 1
                continue
            history_creates.append(
                {
                    "fields": {
                        "调仓ID": item["adjustment_id"],
                        "基金代码": item["fund_code"],
                        "基金名称": item["fund_name"],
                        "操作类型": ORDER_CODE_MAP.get(
                            item["order_code"], item["order_code"]
                        ),
                        "品种": item["variety"],
                        "成交净值": item["nav"],
                        "净值日期": item["nav_date"] or None,
                        "交易份数": item["trade_unit"],
                        "交易日期": item["adjust_txn_date"] or None,
                    }
                }
            )
            existing_history.add(key)
        self.batch_create(history_table, history_creates)
        log(
            f"飞书：历史同步完成，新增 {len(history_creates)}，"
            f"跳过 {skipped_history}（已有）"
        )
        return (
            f"{report['name']} 持仓新增 {len(creates)}、更新 {len(updates)}，"
            f"历史新增 {len(history_creates)}、跳过 {skipped_history}"
        )


def sync_feishu(reports: list[dict[str, Any]], disabled: bool) -> str:
    if disabled:
        return "已通过命令行禁用"
    config = FeishuConfig.from_env()
    if config is None:
        return "未配置，已跳过"
    client = FeishuBitableClient(config)
    log("飞书：正在获取多维表格中的数据表列表")
    tables = {
        str(item.get("name")): str(item.get("table_id"))
        for item in client.list_tables()
        if item.get("name") and item.get("table_id")
    }
    log(f"飞书：共找到 {len(tables)} 个数据表：{list(tables)}")
    results = [client.sync_report(report, tables) for report in reports]
    return "；".join(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取且慢顾投计划并可选同步飞书")
    parser.add_argument(
        "--plans",
        default=os.environ.get("QIEMAN_PLANS", ""),
        help="计划列表，格式 CODE:名称,CODE2:名称2",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=positive_int(
            os.environ.get("QIEMAN_HISTORY_LIMIT"), default=3, maximum=20
        ),
        help="任务日志摘要中显示的最近调仓批次数，默认 3",
    )
    parser.add_argument("--no-feishu", action="store_true", help="本次不写飞书")
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="已废弃：通知由白虎任务通知配置统一处理",
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

    client = QiemanClient()
    reports: list[dict[str, Any]] = []
    failures: list[str] = []
    for code, name in plans.items():
        log(f"获取且慢计划：{name}（{code}）")
        try:
            composition = client.get_plan_composition(code)
            history = client.get_plan_history(code)
            report = organise_plan(
                code,
                name,
                composition,
                history,
                history_limit=positive_int(args.history_limit, default=3, maximum=20),
            )
            if not report["all_funds"]:
                raise RuntimeError("未获得任何组合明细")
            reports.append(report)
            log(
                f"整理完成：{report['active_fund_count']} 只持仓，"
                f"{len(report['history'])} 条历史记录"
            )
        except Exception as exc:
            failures.append(f"{name}：{exc}")
            log(f"ERROR: {name} 获取或整理失败：{exc}", error=True)

    if not reports:
        return 1

    feishu_status = ""
    try:
        feishu_status = sync_feishu(reports, args.no_feishu)
        log(f"飞书：{feishu_status}")
    except Exception as exc:
        feishu_status = f"同步失败：{exc}"
        failures.append(feishu_status)
        log(f"ERROR: {feishu_status}", error=True)

    summary = render_task_summary(reports, feishu_status, failures)
    log("\n" + summary)
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    if failures:
        log("执行存在失败：" + "；".join(failures), error=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
