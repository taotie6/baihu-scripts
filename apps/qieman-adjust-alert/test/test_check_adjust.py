#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).resolve().parents[1] / "main" / "check_adjust.py"
SPEC = importlib.util.spec_from_file_location("qieman_adjust_alert", MODULE_PATH)
assert SPEC and SPEC.loader
ALERT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ALERT
SPEC.loader.exec_module(ALERT)

TZ = ZoneInfo("Asia/Shanghai")


def ms(*, day: str, hour: int = 0) -> int:
    date = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
    return int((date.timestamp() + hour * 3600) * 1000)


def latest_response(
    *,
    adjustment_id: int = 841,
    txn_day: str = "2026-10-22",
    comment: str = "买入1份广发A500",
) -> dict:
    txn_ms = ms(day=txn_day)
    return {
        "latestAdjustments": [
            {
                "adjustmentId": adjustment_id,
                "description": "--",
                "url": "https://qieman.com/content/items/26125",
                "adjustCreateTime": txn_ms + 1000,
                "adjustTxnDate": txn_ms,
                "investType": "E",
                "orders": [
                    {
                        "fund": {"fundCode": "022424", "fundName": "广发中证A500ETF联接A"},
                        "orderCode": "022",
                        "tradeUnit": 1,
                        "postPlanUnit": 3,
                        "variety": "广发A500",
                        "adjustTxnDate": txn_ms,
                        "nav": 1.0505,
                        "navDate": txn_ms,
                    }
                ],
                "comment": comment,
            }
        ],
        "adjustTxnDateEndAt": ms(day=txn_day, hour=15),
        "waitForARDone": False,
        "expired": True,
    }


class ParsePlansTest(unittest.TestCase):
    def test_defaults_and_custom_plans(self) -> None:
        self.assertEqual(
            list(ALERT.parse_plans("")),
            ["LONG_WIN", "LONG_WIN_S"],
        )
        self.assertEqual(
            ALERT.parse_plans("LONG_WIN:计划 A"),
            {"LONG_WIN": "计划 A"},
        )

    def test_rejects_missing_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少计划名称"):
            ALERT.parse_plans("LONG_WIN:")


class OrganiseAdjustmentTest(unittest.TestCase):
    def test_keeps_comment_orders_and_deadline(self) -> None:
        response = latest_response()
        item = ALERT.organise_adjustment(
            response["latestAdjustments"][0],
            "长赢指数150计划",
            ALERT._timestamp(response["adjustTxnDateEndAt"]),
        )
        assert item
        self.assertEqual(item["adjustment_id"], 841)
        self.assertEqual(item["comment"], "买入1份广发A500")
        self.assertEqual(item["txn_date"], ms(day="2026-10-22"))
        self.assertEqual(item["end_at"], ms(day="2026-10-22", hour=15))
        self.assertEqual(len(item["orders"]), 1)

    def test_builds_comment_when_missing_or_placeholder(self) -> None:
        for comment in ("", "--"):
            response = latest_response(comment=comment)
            item = ALERT.organise_adjustment(
                response["latestAdjustments"][0], "计划", 0
            )
            assert item
            self.assertEqual(item["comment"], "买入广发A500 1份")

    def test_rejects_entry_without_adjustment_id(self) -> None:
        self.assertIsNone(ALERT.organise_adjustment({"comment": "x"}, "计划", 0))


class CollectAdjustmentsTest(unittest.TestCase):
    def test_filters_by_target_date(self) -> None:
        responses = [("长赢指数150计划", latest_response(txn_day="2026-10-22"))]
        self.assertEqual(
            [item["adjustment_id"] for item in ALERT.collect_adjustments(responses, "2026-10-22")],
            [841],
        )
        self.assertEqual(ALERT.collect_adjustments(responses, "2026-10-24"), [])

    def test_dedupes_same_batch_across_plans(self) -> None:
        responses = [
            ("长赢指数150计划", latest_response()),
            ("长赢指数S计划", latest_response()),
        ]
        items = ALERT.collect_adjustments(responses, "2026-10-22")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["plans"], ["长赢指数150计划", "长赢指数S计划"]
        )

    def test_keeps_multiple_batches_sorted(self) -> None:
        first = latest_response(adjustment_id=830, txn_day="2026-10-22", comment="第1批")
        second = latest_response(adjustment_id=841, txn_day="2026-10-22", comment="第2批")
        first["latestAdjustments"].append(second["latestAdjustments"][0])
        items = ALERT.collect_adjustments([("计划", first)], "2026-10-22")
        self.assertEqual([item["adjustment_id"] for item in items], [830, 841])

    def test_force_mode_ignores_date(self) -> None:
        responses = [("长赢指数150计划", latest_response(txn_day="2026-10-22"))]
        self.assertEqual(len(ALERT.collect_adjustments(responses, None)), 1)


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        response = latest_response()
        self.adjustments = ALERT.collect_adjustments(
            [("长赢指数150计划", response), ("长赢指数S计划", latest_response())],
            "2026-10-22",
        )
        self.before_deadline = ms(day="2026-10-22", hour=14) + 40 * 60 * 1000
        self.after_deadline = ms(day="2026-10-22", hour=16)

    def test_alert_contains_details_and_deadline(self) -> None:
        alert = ALERT.render_alert(self.adjustments, "2026-10-22", now_ms=self.before_deadline)
        self.assertIn("检测到今日（2026-10-22）调仓", alert)
        self.assertIn("买入1份广发A500", alert)
        self.assertIn("长赢指数150计划、长赢指数S计划", alert)
        self.assertIn("跟车截止 15:00", alert)
        self.assertIn("买入广发A500 1份（买后 3 份）@1.0505", alert)
        self.assertIn("公告：https://qieman.com/content/items/26125", alert)

    def test_alert_marks_expired_window(self) -> None:
        alert = ALERT.render_alert(self.adjustments, "2026-10-22", now_ms=self.after_deadline)
        self.assertIn("跟车已截止", alert)
        self.assertNotIn("跟车截止 15:00", alert)

    def test_alert_without_update(self) -> None:
        self.assertEqual(
            ALERT.render_alert([], "2026-10-24"),
            "今日（2026-10-24）无调仓更新。",
        )

    def test_marker_lines_are_stable(self) -> None:
        self.assertEqual(
            ALERT.render_marker([], now_ms=self.before_deadline),
            "NOTIFY_UPDATE=0 今日无调仓更新",
        )
        marker = ALERT.render_marker(self.adjustments, now_ms=self.before_deadline)
        self.assertTrue(marker.startswith("NOTIFY_UPDATE=1 #841 买入1份广发A500"))
        self.assertIn("截止15:00", marker)

    def test_marker_summary_is_truncated(self) -> None:
        response = latest_response(comment="超长摘要" * 60)
        items = ALERT.collect_adjustments([("计划", response)], "2026-10-22")
        marker = ALERT.render_marker(items)
        self.assertLessEqual(len(marker), len("NOTIFY_UPDATE=1 ") + ALERT.MARKER_SUMMARY_LIMIT)
        self.assertTrue(marker.endswith("…"))

    def test_nav_and_deadline_are_omitted_when_missing(self) -> None:
        response = latest_response()
        batch = response["latestAdjustments"][0]
        batch["orders"][0]["nav"] = 0
        batch["orders"][0]["postPlanUnit"] = None
        response.pop("adjustTxnDateEndAt")
        items = ALERT.collect_adjustments([("计划", response)], "2026-10-22")
        alert = ALERT.render_alert(items, "2026-10-22")
        self.assertIn("买入广发A500 1份\n", alert)
        self.assertNotIn("@", alert.split("\n")[2])
        self.assertNotIn("跟车", alert)


class BaihuPushConfigTest(unittest.TestCase):
    def test_not_configured_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ALERT.BaihuPushConfig.from_env())

    def test_partial_configuration_returns_none(self) -> None:
        with patch.dict(os.environ, {"BHPKG_NOTIFY_TOKEN": "token"}, clear=True):
            self.assertIsNone(ALERT.BaihuPushConfig.from_env())

    def test_joined_values_prefer_latest(self) -> None:
        values = {
            "BHPKG_NOTIFY_TOKEN": "old&new-token",
            "BHPKG_NOTIFY_CHANNEL": "new-channel",
        }
        with patch.dict(os.environ, values, clear=True):
            config = ALERT.BaihuPushConfig.from_env()
        assert config
        self.assertEqual(config.token, "new-token")
        self.assertEqual(config.channel, "new-channel")
        self.assertEqual(config.url, ALERT.DEFAULT_BAIHU_NOTIFY_URL)


class PushBaihuTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ALERT.BaihuPushConfig("token", "channel-1", "http://baihu/api")

    def test_sends_markdown_payload_with_token_header(self) -> None:
        response = MagicMock()
        response.json.return_value = {"code": 200}
        with patch.object(ALERT.requests, "post", return_value=response) as post:
            self.assertTrue(ALERT.push_baihu(self.config, "标题", "内容"))
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"], {"notify-token": "token"})
        self.assertEqual(
            kwargs["json"],
            {
                "channel_id": "channel-1",
                "title": "标题",
                "content": "内容",
                "options": {"format": "markdown"},
            },
        )

    def test_failure_is_reported_without_raising(self) -> None:
        response = MagicMock()
        response.json.return_value = {"code": 400, "msg": "channel not found"}
        with patch.object(ALERT.requests, "post", return_value=response):
            self.assertFalse(ALERT.push_baihu(self.config, "标题", "内容"))


class MainFlowTest(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        out: list[str] = []

        class _Tee(io.StringIO):
            def write(self, value: str) -> int:
                out.append(value)
                return super().write(value)

        buffer = _Tee()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["check_adjust.py", *argv]), \
                redirect_stdout(buffer), redirect_stderr(stderr):
            code = ALERT.main()
        return code, "".join(out) + stderr.getvalue()

    def test_no_update_run_exits_zero_with_marker(self) -> None:
        client = MagicMock()
        client.get_latest_adjustment.return_value = latest_response(txn_day="2026-10-22")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(ALERT, "QiemanClient", return_value=client), \
                patch.object(ALERT, "today_str", return_value="2026-10-24"):
            code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("NOTIFY_UPDATE=0 今日无调仓更新", output)

    def test_update_run_sends_direct_push_when_configured(self) -> None:
        client = MagicMock()
        client.get_latest_adjustment.return_value = latest_response(txn_day="2026-10-22")
        push = MagicMock()
        push.json.return_value = {"code": 200}
        env = {
            "BHPKG_NOTIFY_TOKEN": "token",
            "BHPKG_NOTIFY_CHANNEL": "channel-1",
        }
        with patch.dict(os.environ, env, clear=True), \
                patch.object(ALERT, "QiemanClient", return_value=client), \
                patch.object(ALERT.requests, "post", return_value=push) as post, \
                patch.object(ALERT, "today_str", return_value="2026-10-22"):
            code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("NOTIFY_UPDATE=1 #841", output)
        self.assertIn("白虎直推：已发送", output)
        self.assertEqual(post.call_count, 1)

    def test_all_plans_failed_exits_non_zero(self) -> None:
        client = MagicMock()
        client.get_latest_adjustment.side_effect = RuntimeError("网络错误")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(ALERT, "QiemanClient", return_value=client):
            code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("ERROR", output)


if __name__ == "__main__":
    unittest.main()
