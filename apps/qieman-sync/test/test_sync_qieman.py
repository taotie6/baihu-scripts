#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
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
            QIEMAN.parse_plans(""),
            {
                "LONG_WIN": "长赢指数150计划",
                "LONG_WIN_S": "长赢指数S计划",
            },
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
        content = QIEMAN.render_task_summary(
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

    def test_baihu_joined_secrets_prefer_the_latest_value(self) -> None:
        values = {
            "FEISHU_APP_ID": "old-app&new-app",
            "FEISHU_APP_SECRET": "old-secret&old-secret&new-secret",
            "FEISHU_TABLE_TOKEN": "old-token&new-token",
        }
        with patch.dict(os.environ, values, clear=True):
            config = QIEMAN.FeishuConfig.from_env()
        assert config
        self.assertEqual(config.app_id, "new-app")
        self.assertEqual(config.app_secret, "new-secret")
        self.assertEqual(config.app_token, "new-token")
        self.assertEqual(config.app_secret_candidates, ("new-secret", "old-secret"))


class BaihuTaskNotificationTest(unittest.TestCase):
    def test_script_has_no_direct_baihu_notification_client(self) -> None:
        self.assertFalse(hasattr(QIEMAN, "notify_baihu"))
        self.assertFalse(hasattr(QIEMAN, "DEFAULT_BAIHU_NOTIFY_URL"))


class FeishuClientErrorTest(unittest.TestCase):
    def test_invalid_app_secret_error_explains_wiki_token_is_not_involved(self) -> None:
        config = QIEMAN.FeishuConfig("app", "bad-secret", "wikcn-token", "bitable")
        client = QIEMAN.FeishuBitableClient(config)
        with patch.object(QIEMAN.requests, "post") as post:
            post.return_value.json.return_value = {
                "code": 10003,
                "msg": "app secret invalid",
            }
            with self.assertRaisesRegex(RuntimeError, "同一个飞书自建应用") as context:
                client._tenant_token()
        self.assertIn("FEISHU_TABLE_TOKEN 无关", str(context.exception))

    def test_tries_credential_candidates_without_leaking_them(self) -> None:
        config = QIEMAN.FeishuConfig(
            "new-app",
            "new-secret",
            "base-token",
            "bitable",
            app_id_candidates=("new-app", "old-app"),
            app_secret_candidates=("new-secret", "old-secret"),
        )
        client = QIEMAN.FeishuBitableClient(config)
        responses = []
        for payload in (
            {"code": 10003, "msg": "app secret invalid"},
            {"code": 0, "tenant_access_token": "tenant-token"},
        ):
            response = unittest.mock.MagicMock()
            response.json.return_value = payload
            responses.append(response)
        with patch.object(QIEMAN.requests, "post", side_effect=responses) as post:
            self.assertEqual(client._tenant_token(), "tenant-token")

        self.assertEqual(post.call_count, 2)

    def test_all_failed_candidates_are_redacted_from_error(self) -> None:
        secrets = ("new-sensitive-secret", "old-sensitive-secret")
        config = QIEMAN.FeishuConfig(
            "app", secrets[0], "base-token", "bitable", app_secret_candidates=secrets
        )
        client = QIEMAN.FeishuBitableClient(config)
        response = unittest.mock.MagicMock()
        response.json.return_value = {
            "code": 10003,
            "msg": f"invalid {secrets[0]}",
        }
        with patch.object(QIEMAN.requests, "post", return_value=response):
            with self.assertRaises(RuntimeError) as context:
                client._tenant_token()
        message = str(context.exception)
        self.assertNotIn(secrets[0], message)
        self.assertNotIn(secrets[1], message)

    def test_tries_joined_table_token_candidates(self) -> None:
        config = QIEMAN.FeishuConfig(
            "app",
            "secret",
            "new-token",
            "bitable",
            app_token_candidates=("new-token", "old-token"),
        )
        client = QIEMAN.FeishuBitableClient(config)
        with patch.object(
            client,
            "_list_tables",
            side_effect=[RuntimeError("invalid new-token"), []],
        ) as list_tables:
            self.assertEqual(client.list_tables(), [])
        self.assertEqual(list_tables.call_count, 2)
        self.assertEqual(client.app_token, "old-token")


class MainExitStatusTest(unittest.TestCase):
    def test_returns_nonzero_when_qieman_fetch_fails(self) -> None:
        args = SimpleNamespace(
            plans="LONG_WIN:测试计划",
            history_limit=3,
            no_feishu=True,
            no_notify=False,
            json=False,
        )
        with patch.object(QIEMAN, "parse_args", return_value=args), patch.object(
            QIEMAN.QiemanClient,
            "get_plan_composition",
            side_effect=RuntimeError("上游失败"),
        ):
            self.assertEqual(QIEMAN.main(), 1)


class FeishuSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        config = QIEMAN.FeishuConfig("app", "secret", "base-token", "bitable")
        self.client = QIEMAN.FeishuBitableClient(config)

    def test_ensure_fields_creates_missing_and_updates_stale_description(self) -> None:
        expected = [
            {"name": "基金代码", "type": 1, "desc": "新说明"},
            {"name": "基金名称", "type": 1, "desc": "名称说明"},
        ]
        existing = {
            "基金代码": {
                "field_id": "fld-code",
                "field_name": "基金代码",
                "type": 2,
                "description": {"text": "旧说明"},
            }
        }
        with patch.object(self.client, "list_fields", return_value=existing), patch.object(
            self.client, "request"
        ) as request:
            result = self.client.ensure_fields("tbl-holding", expected)

        self.assertEqual(result, (1, 1))
        self.assertEqual(request.call_count, 2)
        payloads = [call.kwargs["data"] for call in request.call_args_list]
        self.assertEqual({payload["description"]["text"] for payload in payloads}, {"新说明", "名称说明"})
        self.assertEqual(request.call_args_list[0].kwargs["data"]["type"], 2)

    def test_sync_report_counts_holdings_and_skips_existing_history(self) -> None:
        report = {
            "code": "LONG_WIN",
            "name": "测试计划",
            "all_funds": [
                {
                    "fund_code": "000001",
                    "fund_name": "基金一",
                    "class_name": "A股",
                    "target_percent": 0.4,
                    "plan_unit": 4,
                    "nav": 1.2,
                    "nav_date": 1760000000000,
                    "unit_value": 1,
                },
                {
                    "fund_code": "000002",
                    "fund_name": "基金二",
                    "class_name": "债券",
                    "target_percent": 0.2,
                    "plan_unit": 2,
                    "nav": 2,
                    "nav_date": 1760000000000,
                    "unit_value": None,
                },
            ],
            "history": [
                {
                    "adjustment_id": 9,
                    "fund_code": "000001",
                    "fund_name": "基金一",
                    "order_code": "022",
                    "variety": "基金一",
                    "nav": 1.2,
                    "nav_date": 1760000000000,
                    "trade_unit": 1,
                    "adjust_txn_date": 1760000000000,
                },
                {
                    "adjustment_id": 8,
                    "fund_code": "000002",
                    "fund_name": "基金二",
                    "order_code": "024",
                    "variety": "基金二",
                    "nav": 2,
                    "nav_date": 1750000000000,
                    "trade_unit": -1,
                    "adjust_txn_date": 1750000000000,
                },
            ],
        }
        tables = {"测试计划": "tbl-holding", "测试计划-买卖记录": "tbl-history"}
        records = [
            [{"record_id": "rec-1", "fields": {"基金代码": "000001"}}],
            [
                {
                    "record_id": "rec-history",
                    "fields": {"调仓ID": 9, "基金代码": "000001"},
                }
            ],
        ]
        with patch.object(self.client, "ensure_fields", return_value=(0, 0)), patch.object(
            self.client, "list_records", side_effect=records
        ), patch.object(self.client, "batch_create") as batch_create, patch.object(
            self.client, "batch_update"
        ) as batch_update:
            result = self.client.sync_report(report, tables)

        self.assertIn("持仓新增 1、更新 1", result)
        self.assertIn("历史新增 1、跳过 1", result)
        self.assertEqual(len(batch_create.call_args_list[0].args[1]), 1)
        self.assertEqual(len(batch_create.call_args_list[1].args[1]), 1)
        self.assertEqual(len(batch_update.call_args.args[1]), 1)

    def test_wiki_resolution_logs_mask_tokens(self) -> None:
        wiki_token = "wikcn-sensitive-full-token"
        base_token = "bascn-sensitive-full-token"
        config = QIEMAN.FeishuConfig("app", "secret", wiki_token, "wiki")
        output = io.StringIO()
        with patch.object(
            QIEMAN.FeishuBitableClient,
            "request",
            return_value={
                "data": {"node": {"obj_type": "bitable", "obj_token": base_token}}
            },
        ), redirect_stdout(output):
            client = QIEMAN.FeishuBitableClient(config)

        self.assertEqual(client.app_token, base_token)
        self.assertNotIn(wiki_token, output.getvalue())
        self.assertNotIn(base_token, output.getvalue())
        self.assertIn("Wiki 节点类型 bitable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
