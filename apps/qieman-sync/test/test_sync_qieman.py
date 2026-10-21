#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "main" / "sync_qieman.py"
SPEC = importlib.util.spec_from_file_location("qieman_sync", MODULE_PATH)
assert SPEC and SPEC.loader
QIEMAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QIEMAN
SPEC.loader.exec_module(QIEMAN)


class ParsePlansTest(unittest.TestCase):
    def test_defaults_and_multiple_plans(self) -> None:
        self.assertEqual(
            QIEMAN.parse_plans(""), {"LONG_WIN": "长赢指数150计划"}
        )
        self.assertEqual(
            QIEMAN.parse_plans("LONG_WIN:计划 A, LONG_WIN_S:计划 B"),
            {"LONG_WIN": "计划 A", "LONG_WIN_S": "计划 B"},
        )

    def test_rejects_missing_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少计划名称"):
            QIEMAN.parse_plans("LONG_WIN:")


class OrganisePlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.composition = {
            "investedUnit": 10,
            "composition": [
                {
                    "className": "A股",
                    "percent": 0.6,
                    "compList": [
                        {
                            "fund": {"fundCode": "000001", "fundName": "基金一"},
                            "percent": 0.4,
                            "planUnit": 4,
                            "nav": 1.2,
                            "navDate": 1760000000000,
                            "unitValue": 1.0,
                        },
                        {
                            "fund": {"fundCode": "000002", "fundName": "已清仓"},
                            "percent": 0,
                            "planUnit": 0,
                            "nav": 2,
                            "navDate": 1760000000000,
                            "unitValue": None,
                        },
                    ],
                }
            ],
        }
        self.history = [
            {
                "adjustmentId": 9,
                "fund": {"fundCode": "000001", "fundName": "基金一"},
                "orderCode": "022",
                "variety": "基金一",
                "nav": 1.2,
                "navDate": 1760000000000,
                "tradeUnit": 1,
                "adjustTxnDate": 1760000000000,
            },
            {
                "adjustmentId": 8,
                "fund": {"fundCode": "000002", "fundName": "已清仓"},
                "orderCode": "024",
                "variety": "已清仓",
                "nav": 2,
                "navDate": 1750000000000,
                "tradeUnit": -1,
                "adjustTxnDate": 1750000000000,
            },
        ]

    def test_organises_active_holdings_and_limits_adjustments(self) -> None:
        report = QIEMAN.organise_plan(
            "LONG_WIN",
            "测试计划",
            self.composition,
            self.history,
            history_limit=1,
        )
        self.assertEqual(report["active_fund_count"], 1)
        self.assertEqual(len(report["all_funds"]), 2)
        self.assertEqual([item["adjustment_id"] for item in report["recent_adjustments"]], [9])
        self.assertIn("A股 60.00%", QIEMAN.render_plan(report))
        self.assertIn("买入基金一 1份@1.2", QIEMAN.render_plan(report))

    def test_notification_includes_failures(self) -> None:
        report = QIEMAN.organise_plan(
            "LONG_WIN", "测试计划", self.composition, self.history, history_limit=1
        )
        content = QIEMAN.render_notification(
            [report], "未配置，已跳过", ["计划 B：请求失败"]
        )
        self.assertIn("飞书：未配置，已跳过", content)
        self.assertIn("异常：计划 B：请求失败", content)


class FeishuConfigTest(unittest.TestCase):
    def test_all_feishu_configuration_is_optional(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(QIEMAN.FeishuConfig.from_env())

    def test_partial_feishu_configuration_is_rejected(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app"}, clear=True):
            with self.assertRaisesRegex(ValueError, "启用时还需配置"):
                QIEMAN.FeishuConfig.from_env()

    def test_complete_feishu_configuration_is_loaded(self) -> None:
        values = {
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_TABLE_TOKEN": "token",
        }
        with patch.dict(os.environ, values, clear=True):
            config = QIEMAN.FeishuConfig.from_env()
        assert config
        self.assertEqual(config.app_token, "token")
        self.assertEqual(config.token_type, "bitable")


if __name__ == "__main__":
    unittest.main()
