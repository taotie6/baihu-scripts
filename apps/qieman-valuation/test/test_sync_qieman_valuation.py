#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "main" / "sync_qieman_valuation.py"
)
SPEC = importlib.util.spec_from_file_location("qieman_valuation", MODULE_PATH)
assert SPEC and SPEC.loader
VALUATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALUATION
SPEC.loader.exec_module(VALUATION)


class OrganiseValuationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = {
            "date": 1760000000000,
            "idxEvalList": [
                {
                    "indexCode": "000001.CSI",
                    "indexName": "低估指数",
                    "pe": 8,
                    "pePercentile": 0.01,
                    "peLow": 7,
                    "peHigh": 20,
                    "pb": 1,
                    "pbPercentile": 0.02,
                    "pbLow": 0.8,
                    "pbHigh": 3,
                    "roe": 0.12,
                    "group": "LOW",
                    "category": ["BROAD_BASE"],
                },
                {
                    "indexCode": "000002.CSI",
                    "indexName": "高估指数",
                    "pe": 30,
                    "pePercentile": 0.99,
                    "pb": 5,
                    "pbPercentile": 0.98,
                    "group": "HIGH",
                    "category": ["INDUSTRY"],
                },
            ],
            "bondIdxEvalList": [
                {
                    "indexCode": "BOND.CS",
                    "indexName": "测试债券",
                    "profitRate": 0.025,
                    "profitRatePercentile": 0.75,
                    "group": "LOW",
                    "category": ["INTEREST_BOND"],
                }
            ],
        }

    def test_organises_api_fields(self) -> None:
        report = VALUATION.organise_valuation(self.data)
        self.assertEqual(len(report["indexes"]), 2)
        self.assertEqual(report["indexes"][0]["category"], "宽基")
        self.assertEqual(report["indexes"][0]["group"], "低估")
        self.assertEqual(report["bonds"][0]["profit_rate"], 0.025)

    def test_renders_summary_and_percentages(self) -> None:
        content = VALUATION.render_notification(
            VALUATION.organise_valuation(self.data), 5, "未配置，已跳过"
        )
        self.assertIn("指数 2 个：低估 1 / 适中 0 / 高估 1", content)
        self.assertIn("低估指数：PE 8（1.00%）", content)
        self.assertIn("测试债券：YTM 2.50%，历史百分位 75.00%，低估", content)
        self.assertIn("飞书：未配置，已跳过", content)

    def test_builds_original_feishu_field_shape(self) -> None:
        report = VALUATION.organise_valuation(self.data)
        row = VALUATION.index_rows(report)[0]
        self.assertEqual(row["指数代码"], "000001.CSI")
        self.assertEqual(row["当前PE"], 8)
        self.assertEqual(row["估值状态"], "低估")


class FeishuConfigTest(unittest.TestCase):
    def test_feishu_is_optional(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(VALUATION.FeishuConfig.from_env())

    def test_partial_feishu_config_is_rejected(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app"}, clear=True):
            with self.assertRaisesRegex(ValueError, "启用时还需配置"):
                VALUATION.FeishuConfig.from_env()

    def test_complete_feishu_config_is_loaded(self) -> None:
        values = {
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_TABLE_TOKEN": "token",
        }
        with patch.dict(os.environ, values, clear=True):
            config = VALUATION.FeishuConfig.from_env()
        assert config
        self.assertEqual(config.app_token, "token")
        self.assertEqual(config.token_type, "bitable")


class BaihuNotificationTest(unittest.TestCase):
    def test_posts_notification_to_baihu_api(self) -> None:
        values = {
            "BHPKG_NOTIFY_TOKEN": "notify-secret",
            "BHPKG_NOTIFY_CHANNEL": "channel-1",
        }
        with patch.dict(os.environ, values, clear=True), patch.object(
            VALUATION.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"code": 200, "msg": "success"}
            self.assertTrue(VALUATION.notify_baihu("测试内容"))

        post.assert_called_once_with(
            VALUATION.DEFAULT_BAIHU_NOTIFY_URL,
            headers={"notify-token": "notify-secret"},
            json={
                "channel_id": "channel-1",
                "title": "且慢每日估值",
                "content": "测试内容",
                "options": {"format": "markdown"},
            },
            timeout=30,
        )

    def test_skips_notification_when_script_credentials_are_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            VALUATION.requests, "post"
        ) as post:
            self.assertFalse(VALUATION.notify_baihu("测试内容"))
        post.assert_not_called()


class FeishuClientErrorTest(unittest.TestCase):
    def test_invalid_app_secret_error_explains_wiki_token_is_not_involved(self) -> None:
        config = VALUATION.FeishuConfig(
            "app", "bad-secret", "wikcn-token", "bitable"
        )
        client = VALUATION.FeishuBitableClient(config)
        with patch.object(VALUATION.requests, "post") as post:
            post.return_value.json.return_value = {
                "code": 10003,
                "msg": "app secret invalid",
            }
            with self.assertRaisesRegex(RuntimeError, "同一个飞书自建应用") as context:
                client._tenant_token()
        self.assertIn("FEISHU_TABLE_TOKEN 无关", str(context.exception))


class FeishuSyncTest(unittest.TestCase):
    def test_ensure_fields_reports_created_and_updated_descriptions(self) -> None:
        config = VALUATION.FeishuConfig("app", "secret", "base-token", "bitable")
        client = VALUATION.FeishuBitableClient(config)
        expected = [
            {"name": "指数代码", "type": 1, "desc": "新说明"},
            {"name": "指数名称", "type": 1, "desc": "名称说明"},
        ]
        existing = {
            "指数代码": {
                "field_id": "fld-code",
                "field_name": "指数代码",
                "description": {"text": "旧说明"},
            }
        }
        with patch.object(client, "list_fields", return_value=existing), patch.object(
            client, "request"
        ) as request:
            result = client.ensure_fields("tbl-index", expected)

        self.assertEqual(result, (1, 1))
        self.assertEqual(request.call_count, 2)

    def test_sync_feishu_reports_each_table_result(self) -> None:
        report = {
            "update_time": "2026/10/21 20:00",
            "indexes": [
                {
                    "index_code": "000001.CSI",
                    "index_name": "测试指数",
                    "pe": 8,
                    "pe_percentile": 0.1,
                    "pe_low": 7,
                    "pe_high": 20,
                    "pb": 1,
                    "pb_percentile": 0.2,
                    "pb_low": 0.8,
                    "pb_high": 3,
                    "roe": 0.12,
                    "group": "低估",
                    "category": "宽基",
                }
            ],
            "bonds": [
                {
                    "index_code": "BOND.CS",
                    "index_name": "测试债券",
                    "profit_rate": 0.025,
                    "profit_rate_percentile": 0.75,
                    "group": "低估",
                    "category": "利率债",
                }
            ],
        }
        fake_client = MagicMock()
        fake_client.list_tables.return_value = [
            {"name": "每日指数估值", "table_id": "tbl-index"},
            {"name": "每日债券收益", "table_id": "tbl-bond"},
        ]
        fake_client.ensure_table.side_effect = ["tbl-index", "tbl-bond"]
        fake_client.ensure_fields.side_effect = [(0, 1), (1, 0)]
        fake_client.upsert_rows.side_effect = [(0, 32), (0, 5)]
        environment = {
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_TABLE_TOKEN": "base-token",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            VALUATION, "FeishuBitableClient", return_value=fake_client
        ):
            result = VALUATION.sync_feishu(report, disabled=False)

        self.assertEqual(result, "指数新增 0、更新 32；债券新增 0、更新 5")
        self.assertEqual(fake_client.ensure_fields.call_count, 2)
        self.assertEqual(fake_client.upsert_rows.call_count, 2)

    def test_mask_token_never_returns_the_complete_token(self) -> None:
        token = "wikcn-sensitive-full-token"
        masked = VALUATION.mask_token(token)
        self.assertNotEqual(masked, token)
        self.assertEqual(masked, "wikc...oken")


if __name__ == "__main__":
    unittest.main()
