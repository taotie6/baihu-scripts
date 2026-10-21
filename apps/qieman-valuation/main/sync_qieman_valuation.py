#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""且慢每日估值（白虎面板版）。

默认获取指数估值与债券收益，整理后通过白虎原生通知推送；完整配置
FEISHU_* 环境变量后，额外同步到飞书多维表格。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


QIEMAN_BASE_URL = "https://qieman.com"
DEFAULT_BAIHU_NOTIFY_URL = "http://localhost:8052/api/v1/notify/send"
TEXT_FIELD = 1
NUMBER_FIELD = 2
SELECT_FIELD = 3
GROUP_MAP = {"LOW": "低估", "MID": "适中", "HIGH": "高估"}
CATEGORY_MAP = {
    "BROAD_BASE": "宽基",
    "INDUSTRY": "行业",
    "STRATEGY": "策略",
    "THEME": "主题",
    "GLOBAL": "全球",
    "INTEREST_BOND": "利率债",
    "CREDIT_BOND": "信用债",
    "CONVERTIBLE_BOND": "可转债",
}

INDEX_FIELDS = [
    {"name": "指数代码", "type": TEXT_FIELD, "desc": "指数的官方代码（例如 399006.SZ）。"},
    {"name": "指数名称", "type": TEXT_FIELD, "desc": "指数的中文名称。"},
    {"name": "当前PE", "type": NUMBER_FIELD, "desc": "市盈率，通常用于衡量指数估值，数值越低代表价格相对盈利越低。"},
    {"name": "PE百分位", "type": NUMBER_FIELD, "desc": "当前 PE 在历史统计区间所处的百分位。"},
    {"name": "PE最低", "type": NUMBER_FIELD, "desc": "指数 PE 在统计区间的历史最低值。"},
    {"name": "PE最高", "type": NUMBER_FIELD, "desc": "指数 PE 在统计区间的历史最高值。"},
    {"name": "当前PB", "type": NUMBER_FIELD, "desc": "市净率，通常适用于周期性行业估值。"},
    {"name": "PB百分位", "type": NUMBER_FIELD, "desc": "当前 PB 在历史统计区间所处的百分位。"},
    {"name": "PB最低", "type": NUMBER_FIELD, "desc": "指数 PB 在统计区间的历史最低值。"},
    {"name": "PB最高", "type": NUMBER_FIELD, "desc": "指数 PB 在统计区间的历史最高值。"},
    {"name": "ROE", "type": NUMBER_FIELD, "desc": "净资产收益率，用于观察长期盈利能力。"},
    {"name": "估值状态", "type": SELECT_FIELD, "desc": "低估、适中或高估。"},
    {"name": "指数类型", "type": SELECT_FIELD, "desc": "宽基、行业、策略、主题或全球等分类。"},
    {"name": "更新时间", "type": TEXT_FIELD, "desc": "且慢估值数据的更新日期。"},
]
BOND_FIELDS = [
    {"name": "债券代码", "type": TEXT_FIELD, "desc": "债券指数的官方代码（例如 CBA08203.CS）。"},
    {"name": "债券名称", "type": TEXT_FIELD, "desc": "债券指数名称；名称中的期限可作为久期参考。"},
    {"name": "到期收益率", "type": NUMBER_FIELD, "desc": "Yield to Maturity；数值越高通常代表债券价格越低。"},
    {"name": "百分位", "type": NUMBER_FIELD, "desc": "当前到期收益率在历史统计区间所处的百分位。"},
    {"name": "估值状态", "type": SELECT_FIELD, "desc": "低估表示收益率相对较高，高估表示收益率相对较低。"},
    {"name": "债券分类", "type": SELECT_FIELD, "desc": "利率债、信用债或可转债等分类。"},
    {"name": "更新时间", "type": TEXT_FIELD, "desc": "且慢估值数据的更新日期。"},
]


def log(message: str, *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)


def positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), maximum)


def optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timezone() -> ZoneInfo:
    name = os.environ.get("QIEMAN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log(f"WARN: 未找到时区 {name}，回退到 Asia/Shanghai")
        return ZoneInfo("Asia/Shanghai")


def format_update_date(timestamp_ms: Any) -> tuple[str, str]:
    try:
        timestamp = int(timestamp_ms or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if not timestamp:
        return "未知日期", ""
    value = datetime.fromtimestamp(timestamp / 1000, tz=_timezone())
    return value.strftime("%Y-%m-%d"), f"{value.year}/{value.month}/{value.day} 20:00"


def _category(item: dict[str, Any]) -> str:
    values = item.get("category") or []
    key = str(values[0]) if isinstance(values, list) and values else ""
    return CATEGORY_MAP.get(key, key or "未分类")


class QiemanValuationClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @staticmethod
    def _sign() -> str:
        timestamp = int(time.time() * 1000)
        payload = str(math.floor(1.01 * timestamp))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32].upper()
        return f"{timestamp}{digest}"

    def fetch(self) -> dict[str, Any]:
        response = self.session.get(
            f"{QIEMAN_BASE_URL}/pmdj/v2/idx-eval/latest",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": QIEMAN_BASE_URL,
                "Referer": f"{QIEMAN_BASE_URL}/idx-eval",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "X-sign": self._sign(),
            },
            timeout=30,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError("且慢估值接口返回了非 JSON 响应") from exc
        if not isinstance(data, dict):
            raise RuntimeError("且慢估值接口响应格式异常")
        return data


def organise_valuation(data: dict[str, Any]) -> dict[str, Any]:
    update_date, update_time = format_update_date(data.get("date"))
    raw_indexes = data.get("idxEvalList") or []
    raw_bonds = data.get("bondIdxEvalList") or []
    if not isinstance(raw_indexes, list) or not isinstance(raw_bonds, list):
        raise ValueError("且慢估值列表格式异常")

    indexes: list[dict[str, Any]] = []
    for raw in raw_indexes:
        if not isinstance(raw, dict) or not raw.get("indexCode"):
            continue
        group_code = str(raw.get("group") or "")
        indexes.append(
            {
                "index_code": str(raw["indexCode"]),
                "index_name": str(raw.get("indexName") or ""),
                "pe": optional_number(raw.get("pe")),
                "pe_percentile": optional_number(raw.get("pePercentile")),
                "pe_low": optional_number(raw.get("peLow")),
                "pe_high": optional_number(raw.get("peHigh")),
                "pb": optional_number(raw.get("pb")),
                "pb_percentile": optional_number(raw.get("pbPercentile")),
                "pb_low": optional_number(raw.get("pbLow")),
                "pb_high": optional_number(raw.get("pbHigh")),
                "roe": optional_number(raw.get("roe")),
                "group_code": group_code,
                "group": GROUP_MAP.get(group_code, group_code or "未知"),
                "category": _category(raw),
            }
        )

    bonds: list[dict[str, Any]] = []
    for raw in raw_bonds:
        if not isinstance(raw, dict) or not raw.get("indexCode"):
            continue
        group_code = str(raw.get("group") or "")
        bonds.append(
            {
                "index_code": str(raw["indexCode"]),
                "index_name": str(raw.get("indexName") or ""),
                "profit_rate": optional_number(raw.get("profitRate")),
                "profit_rate_percentile": optional_number(
                    raw.get("profitRatePercentile")
                ),
                "group_code": group_code,
                "group": GROUP_MAP.get(group_code, group_code or "未知"),
                "category": _category(raw),
            }
        )

    if not indexes and not bonds:
        raise ValueError("且慢估值接口没有返回有效数据")
    return {
        "update_date": update_date,
        "update_time": update_time,
        "indexes": indexes,
        "bonds": bonds,
    }


def _percentile_sort_value(item: dict[str, Any], *, high: bool) -> float:
    values = [
        value
        for value in (item.get("pe_percentile"), item.get("pb_percentile"))
        if value is not None
    ]
    if not values:
        return -1 if high else 2
    return max(values) if high else min(values)


def _number_text(value: Any, digits: int = 2) -> str:
    number = optional_number(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _percent_text(value: Any) -> str:
    number = optional_number(value)
    return "—" if number is None else f"{number * 100:.2f}%"


def render_notification(report: dict[str, Any], limit: int, feishu_status: str) -> str:
    indexes = report["indexes"]
    bonds = report["bonds"]
    counts = Counter(item["group"] for item in indexes)
    lines = [f"且慢每日估值 · {report['update_date']}"]
    lines.append(
        f"指数 {len(indexes)} 个：低估 {counts['低估']} / 适中 {counts['适中']} / 高估 {counts['高估']}"
    )

    low = sorted(
        (item for item in indexes if item["group_code"] == "LOW"),
        key=lambda item: _percentile_sort_value(item, high=False),
    )[:limit]
    high = sorted(
        (item for item in indexes if item["group_code"] == "HIGH"),
        key=lambda item: _percentile_sort_value(item, high=True),
        reverse=True,
    )[:limit]
    if low:
        lines.append("\n低估前列：")
        for item in low:
            lines.append(
                f"- {item['index_name']}：PE {_number_text(item['pe'])}"
                f"（{_percent_text(item['pe_percentile'])}） / PB {_number_text(item['pb'])}"
                f"（{_percent_text(item['pb_percentile'])}）"
            )
    if high:
        lines.append("\n高估前列：")
        for item in high:
            lines.append(
                f"- {item['index_name']}：PE {_number_text(item['pe'])}"
                f"（{_percent_text(item['pe_percentile'])}） / PB {_number_text(item['pb'])}"
                f"（{_percent_text(item['pb_percentile'])}）"
            )

    if bonds:
        lines.append(f"\n债券收益 {len(bonds)} 个：")
        for item in sorted(
            bonds,
            key=lambda value: value.get("profit_rate")
            if value.get("profit_rate") is not None
            else -1,
            reverse=True,
        ):
            lines.append(
                f"- {item['index_name']}：YTM {_percent_text(item['profit_rate'])}，"
                f"历史百分位 {_percent_text(item['profit_rate_percentile'])}，{item['group']}"
            )
    if feishu_status:
        lines.append(f"\n飞书：{feishu_status}")

    content = "\n".join(lines)
    max_length = positive_int(
        os.environ.get("QIEMAN_NOTIFY_MAX_LENGTH"), default=3500, maximum=10000
    )
    if len(content) > max_length:
        content = content[: max(1, max_length - 8)].rstrip() + "\n…内容已截断"
    return content


def notify_baihu(content: str) -> bool:
    """通过白虎脚本调用接口主动推送通知。"""
    token = os.environ.get("BHPKG_NOTIFY_TOKEN", "").strip()
    channel = os.environ.get("BHPKG_NOTIFY_CHANNEL", "").strip()
    missing = [
        key
        for key, value in (
            ("BHPKG_NOTIFY_TOKEN", token),
            ("BHPKG_NOTIFY_CHANNEL", channel),
        )
        if not value
    ]
    if missing:
        log(
            "未配置白虎主动推送参数 "
            + "、".join(missing)
            + "，跳过通知；请在“消息推送 → 脚本调用说明/渠道列表”中获取。"
            "任务的“通知配置”绑定不会自动注入这些变量"
        )
        return False

    url = (
        os.environ.get("BHPKG_NOTIFY_URL", "").strip()
        or DEFAULT_BAIHU_NOTIFY_URL
    )
    try:
        response = requests.post(
            url,
            headers={"notify-token": token},
            json={
                "channel_id": channel,
                "title": "且慢每日估值",
                "content": content,
                "options": {"format": "markdown"},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            raise RuntimeError(data.get("msg") or f"返回代码 {data.get('code')}")
        log("白虎通知已发送")
        return True
    except Exception as exc:
        log(f"WARN: 白虎通知发送失败：{exc}", error=True)
        return False


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    app_token: str
    token_type: str

    @classmethod
    def from_env(cls) -> FeishuConfig | None:
        values = {
            "FEISHU_APP_ID": os.environ.get("FEISHU_APP_ID", "").strip(),
            "FEISHU_APP_SECRET": os.environ.get("FEISHU_APP_SECRET", "").strip(),
            "FEISHU_TABLE_TOKEN": os.environ.get("FEISHU_TABLE_TOKEN", "").strip(),
        }
        configured = [key for key, value in values.items() if value]
        if not configured:
            return None
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("飞书为可选功能；启用时还需配置 " + "、".join(missing))
        token_type = os.environ.get("FEISHU_TOKEN_TYPE", "bitable").strip().lower()
        if token_type not in {"bitable", "wiki"}:
            raise ValueError("FEISHU_TOKEN_TYPE 只能是 bitable 或 wiki")
        return cls(
            app_id=values["FEISHU_APP_ID"],
            app_secret=values["FEISHU_APP_SECRET"],
            app_token=values["FEISHU_TABLE_TOKEN"],
            token_type=token_type,
        )


class FeishuBitableClient:
    BASE_URL = "https://open.feishu.cn/open-apis"
    RETRYABLE_CODES = {1254002, 1254290, 1254291, 1254607, 1255040}

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self.app_token = config.app_token
        self.access_token = ""
        if config.token_type == "wiki":
            self.app_token = self._resolve_wiki_token(config.app_token)

    def _tenant_token(self) -> str:
        response = requests.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal/",
            json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            timeout=30,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError("获取飞书访问令牌时返回了非 JSON 响应") from exc
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(
                "获取飞书 tenant_access_token 失败："
                f"{data.get('msg') or data.get('code')}。"
                "请确认 FEISHU_APP_ID 与 FEISHU_APP_SECRET 来自同一个飞书自建应用；"
                "此步骤发生在 Wiki Token 解析之前，与 FEISHU_TABLE_TOKEN 无关"
            )
        return str(data["tenant_access_token"])

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
                raise RuntimeError(
                    f"飞书接口返回非 JSON 响应（HTTP {response.status_code}）"
                ) from exc
            code = result.get("code")
            if response.ok and code == 0:
                return result
            if response.status_code == 401:
                self.access_token = ""
            if (code in self.RETRYABLE_CODES or response.status_code >= 500) and attempt < 5:
                time.sleep(attempt * 2)
                continue
            message = result.get("msg") or result.get("message") or response.reason
            raise RuntimeError(
                f"飞书接口失败（HTTP {response.status_code}, code={code}）：{message}"
            )
        raise RuntimeError("飞书接口重试次数已用尽")

    def _resolve_wiki_token(self, wiki_token: str) -> str:
        result = self.request(
            "GET",
            "/wiki/v2/spaces/get_node",
            params={"token": wiki_token, "obj_type": "wiki"},
        )
        node = result.get("data", {}).get("node", {})
        if node.get("obj_type") != "bitable" or not node.get("obj_token"):
            raise RuntimeError("FEISHU_TABLE_TOKEN 对应的 Wiki 节点不是多维表格")
        return str(node["obj_token"])

    def list_tables(self) -> list[dict[str, Any]]:
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

    def create_table(self, name: str) -> str:
        result = self.request(
            "POST",
            f"/bitable/v1/apps/{self.app_token}/tables",
            data={"table": {"name": name}},
        )
        table_id = result.get("data", {}).get("table_id")
        if not table_id:
            raise RuntimeError(f"飞书创建数据表「{name}」后未返回 table_id")
        return str(table_id)

    def ensure_table(self, name: str, tables: dict[str, str]) -> str:
        if name not in tables:
            log(f"飞书：创建数据表「{name}」")
            tables[name] = self.create_table(name)
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

    def ensure_fields(self, table_id: str, expected: list[dict[str, Any]]) -> None:
        existing = self.list_fields(table_id)
        for field in expected:
            name = str(field["name"])
            payload = {
                "field_name": name,
                "type": int(field["type"]),
                "description": {"disable_sync": False, "text": str(field["desc"])},
            }
            current = existing.get(name)
            if current is None:
                self.request(
                    "POST",
                    f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/fields",
                    data=payload,
                )
                continue
            current_description = current.get("description") or {}
            current_text = (
                current_description.get("text", "")
                if isinstance(current_description, dict)
                else str(current_description)
            )
            if current_text != field["desc"]:
                field_id = current.get("field_id")
                if not field_id:
                    raise RuntimeError(f"飞书字段 {name} 缺少 field_id")
                self.request(
                    "PUT",
                    f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/fields/{field_id}",
                    data=payload,
                )

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

    def upsert_rows(
        self,
        table_id: str,
        rows: list[dict[str, Any]],
        unique_field: str,
    ) -> tuple[int, int]:
        existing = {
            str(fields.get(unique_field)): str(record.get("record_id"))
            for record in self.list_records(table_id)
            if record.get("record_id")
            and isinstance((fields := record.get("fields")), dict)
            and fields.get(unique_field)
        }
        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        for row in rows:
            key = str(row.get(unique_field) or "")
            if not key:
                continue
            record_id = existing.get(key)
            if record_id:
                updates.append({"record_id": record_id, "fields": row})
            else:
                creates.append({"fields": row})
        self.batch_create(table_id, creates)
        self.batch_update(table_id, updates)
        return len(creates), len(updates)


def index_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "指数代码": item["index_code"],
            "指数名称": item["index_name"],
            "当前PE": item["pe"],
            "PE百分位": item["pe_percentile"],
            "PE最低": item["pe_low"],
            "PE最高": item["pe_high"],
            "当前PB": item["pb"],
            "PB百分位": item["pb_percentile"],
            "PB最低": item["pb_low"],
            "PB最高": item["pb_high"],
            "ROE": item["roe"],
            "估值状态": item["group"],
            "指数类型": item["category"],
            "更新时间": report["update_time"],
        }
        for item in report["indexes"]
    ]


def bond_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "债券代码": item["index_code"],
            "债券名称": item["index_name"],
            "到期收益率": item["profit_rate"],
            "百分位": item["profit_rate_percentile"],
            "估值状态": item["group"],
            "债券分类": item["category"],
            "更新时间": report["update_time"],
        }
        for item in report["bonds"]
    ]


def sync_feishu(report: dict[str, Any], disabled: bool) -> str:
    if disabled:
        return "已通过命令行禁用"
    config = FeishuConfig.from_env()
    if config is None:
        return "未配置，已跳过"
    client = FeishuBitableClient(config)
    tables = {
        str(item.get("name")): str(item.get("table_id"))
        for item in client.list_tables()
        if item.get("name") and item.get("table_id")
    }

    index_table = client.ensure_table("每日指数估值", tables)
    client.ensure_fields(index_table, INDEX_FIELDS)
    index_added, index_updated = client.upsert_rows(
        index_table, index_rows(report), "指数代码"
    )

    bond_table = client.ensure_table("每日债券收益", tables)
    client.ensure_fields(bond_table, BOND_FIELDS)
    bond_added, bond_updated = client.upsert_rows(
        bond_table, bond_rows(report), "债券代码"
    )
    return (
        f"指数新增 {index_added}、更新 {index_updated}；"
        f"债券新增 {bond_added}、更新 {bond_updated}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取且慢每日估值并通过白虎推送")
    parser.add_argument(
        "--limit",
        type=int,
        default=positive_int(
            os.environ.get("QIEMAN_VALUATION_LIMIT"), default=5, maximum=20
        ),
        help="通知中低估/高估各显示多少个指数，默认 5",
    )
    parser.add_argument("--no-feishu", action="store_true", help="本次不写飞书")
    parser.add_argument("--no-notify", action="store_true", help="本次不发白虎通知")
    parser.add_argument("--json", action="store_true", help="额外输出整理后的完整 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("获取且慢每日估值数据")
    try:
        report = organise_valuation(QiemanValuationClient().fetch())
    except Exception as exc:
        message = f"获取或整理且慢估值失败：{exc}"
        log(f"ERROR: {message}", error=True)
        if not args.no_notify:
            notify_baihu(message)
        return 1

    log(
        f"整理完成：{len(report['indexes'])} 条指数估值，"
        f"{len(report['bonds'])} 条债券收益，日期 {report['update_date']}"
    )
    failed = False
    try:
        feishu_status = sync_feishu(report, args.no_feishu)
        log(f"飞书：{feishu_status}")
    except Exception as exc:
        failed = True
        feishu_status = f"同步失败：{exc}"
        log(f"ERROR: 飞书{feishu_status}", error=True)

    content = render_notification(
        report,
        positive_int(args.limit, default=5, maximum=20),
        feishu_status,
    )
    log("\n" + content)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.no_notify:
        notify_baihu(content)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
