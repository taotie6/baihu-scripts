#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
