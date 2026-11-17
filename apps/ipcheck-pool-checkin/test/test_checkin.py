#!/usr/bin/env python3
"""蹬蹬堂订阅池签到逻辑测试。"""

from __future__ import annotations

import html
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "main" / "checkin.py"
SPEC = importlib.util.spec_from_file_location("ipcheck_pool_checkin", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKIN
SPEC.loader.exec_module(CHECKIN)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        text: str | None = None,
        reason: str = "reason",
    ):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(self.payload)
        self.reason = reason

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class LoadAccountsTest(unittest.TestCase):
    def load(self, values: dict[str, str]) -> list[CHECKIN.Account]:
        with patch.dict(os.environ, values, clear=True):
            return CHECKIN.load_accounts()

    def test_single_account(self) -> None:
        accounts = self.load(
            {
                "IPCHECK_POOL_ACCOUNT": " user@example.com ",
                "IPCHECK_POOL_PASSWORD": "secret",
            }
        )

        self.assertEqual(
            accounts,
            [CHECKIN.Account("账号1", "user@example.com", "secret")],
        )

    def test_multi_account_has_priority_and_deduplicates(self) -> None:
        accounts = self.load(
            {
                "IPCHECK_POOL_ACCOUNT": "ignored",
                "IPCHECK_POOL_PASSWORD": "ignored",
                "IPCHECK_POOL_ACCOUNTS": json.dumps(
                    [
                        {"label": "主号", "account": "User", "password": "one"},
                        {"label": "重复", "account": "user", "password": "two"},
                    ]
                ),
            }
        )

        self.assertEqual(accounts, [CHECKIN.Account("主号", "User", "one")])

    def test_partial_single_account_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须同时填写"):
            self.load({"IPCHECK_POOL_ACCOUNT": "user@example.com"})

    def test_accepts_literal_escaped_line_breaks_from_secret_variable(self) -> None:
        value = (
            r'[\n'
            r'  {"label":"账号1","account":"user1","password":"one"},\n'
            r'  {"label":"账号2","account":"user2","password":"two"}\n'
            r']'
        )

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": value}),
            [
                CHECKIN.Account("账号1", "user1", "one"),
                CHECKIN.Account("账号2", "user2", "two"),
            ],
        )

    def test_preserves_escapes_inside_json_strings(self) -> None:
        value = r'[\n{"label":"account\n1","account":"user","password":"one"}\n]'

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": value}),
            [CHECKIN.Account("account\n1", "user", "one")],
        )

    def test_accepts_leading_invisible_character(self) -> None:
        value = '\u200b[{"account":"user","password":"one"}]'

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": value}),
            [CHECKIN.Account("账号1", "user", "one")],
        )

    def test_accepts_common_copy_paste_wrappers(self) -> None:
        document = '[{"account":"user","password":"one"}]'
        documents = [
            f"IPCHECK_POOL_ACCOUNTS={document}",
            f"```json\n{document}\n```",
            "'" + document + "'",
            '［{"account":"user","password":"one"}］',
            json.dumps(document),
        ]

        for value in documents:
            with self.subTest(value=value):
                self.assertEqual(
                    self.load({"IPCHECK_POOL_ACCOUNTS": value}),
                    [CHECKIN.Account("账号1", "user", "one")],
                )

    def test_merges_duplicate_panel_secrets_joined_with_ampersand(self) -> None:
        first = '[{"label":"账号1","account":"user1","password":"one"}]'
        second = '[{"label":"账号2","account":"user2","password":"two"}]'

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": first + "&" + second}),
            [
                CHECKIN.Account("账号1", "user1", "one"),
                CHECKIN.Account("账号2", "user2", "two"),
            ],
        )

    def test_accepts_empty_duplicate_panel_secret_before_json(self) -> None:
        document = '[{"label":"账号1","account":"user1","password":"one"}]'

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": "&" + document}),
            [CHECKIN.Account("账号1", "user1", "one")],
        )

    def test_ampersand_bracket_inside_valid_json_is_not_split(self) -> None:
        document = '[{"label":"账号1","account":"user1","password":"one&[two]"}]'

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": document}),
            [CHECKIN.Account("账号1", "user1", "one&[two]")],
        )

    def test_accepts_backslash_line_continuations_and_unknown_prefix(self) -> None:
        value = (
            '&unknown-wrapper\n'
            '[{"label":"账号1","account":"user1","password":"one"},\
'
            '{"label":"账号2","account":"user2","password":"two"}]'
        )

        self.assertEqual(
            self.load({"IPCHECK_POOL_ACCOUNTS": value}),
            [
                CHECKIN.Account("账号1", "user1", "one"),
                CHECKIN.Account("账号2", "user2", "two"),
            ],
        )

    def test_accepts_html_entity_encoded_json(self) -> None:
        document = '[{"account":"user","password":"one"}]'
        documents = [
            html.escape(document),
            "&quot;" + html.escape(document) + "&quot;",
            "&#91;{&#34;account&#34;:&#34;user&#34;,&#34;password&#34;:&#34;one&#34;}&#93;",
        ]

        for value in documents:
            with self.subTest(value=value):
                self.assertEqual(
                    self.load({"IPCHECK_POOL_ACCOUNTS": value}),
                    [CHECKIN.Account("账号1", "user", "one")],
                )

    def test_invalid_multi_account_json_reports_location_without_echoing_secret(self) -> None:
        secret = "password-that-must-not-be-logged"
        value = f'[{{"account":"user","password":"{secret}"}},]'

        with self.assertRaisesRegex(ValueError, r"第 1 行，第 \d+ 列") as raised:
            self.load({"IPCHECK_POOL_ACCOUNTS": value})
        self.assertNotIn(secret, str(raised.exception))


class CheckinAccountTest(unittest.TestCase):
    account = CHECKIN.Account("测试账号", "user@example.com", "super-secret")

    def test_login_then_checkin_reuses_one_session(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"points": 5, "balance": 15, "streak": 2}),
        )
        with patch.object(CHECKIN, "log") as logger:
            result = CHECKIN.checkin_account(
                self.account, session_factory=lambda: session
            )

        self.assertTrue(result)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(
            session.calls[0]["url"],
            f"{CHECKIN.BASE_URL}{CHECKIN.LOGIN_PATH}",
        )
        self.assertEqual(
            session.calls[0]["json"],
            {"account": "user@example.com", "password": "super-secret"},
        )
        self.assertEqual(
            session.calls[1]["url"],
            f"{CHECKIN.BASE_URL}{CHECKIN.CHECKIN_PATH}",
        )
        messages = [call.args[0] for call in logger.call_args_list]
        self.assertTrue(any("签到成功，+5 虾仁" in message for message in messages))

    def test_already_checked_in_is_success(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"ok": True}),
            FakeResponse(400, {"error": "今天已经签到了"}),
        )
        with patch.object(CHECKIN, "log") as logger:
            result = CHECKIN.checkin_account(
                self.account, session_factory=lambda: session
            )

        self.assertTrue(result)
        messages = [call.args[0] for call in logger.call_args_list]
        self.assertTrue(any("今日已签到" in message for message in messages))

    def test_bad_password_fails_without_calling_checkin(self) -> None:
        session = FakeSession(
            FakeResponse(401, {"error": "账号或密码不对。"}),
        )
        with patch.object(CHECKIN, "log") as logger:
            result = CHECKIN.checkin_account(
                self.account, session_factory=lambda: session
            )

        self.assertFalse(result)
        self.assertEqual(len(session.calls), 1)
        messages = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertIn("账号或密码不对", messages)
        self.assertNotIn("super-secret", messages)
        self.assertNotIn("user@example.com", messages)

    def test_cloudflare_challenge_has_actionable_error(self) -> None:
        session = FakeSession(
            FakeResponse(
                403,
                headers={"cf-mitigated": "challenge"},
                text="<html><title>Just a moment...</title></html>",
            ),
        )
        with patch.object(CHECKIN, "log") as logger:
            result = CHECKIN.checkin_account(
                self.account, session_factory=lambda: session
            )

        self.assertFalse(result)
        messages = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertIn("Cloudflare", messages)

    def test_error_message_redacts_credentials(self) -> None:
        session = FakeSession(
            FakeResponse(
                401,
                {"error": "bad user@example.com super-secret"},
            ),
        )
        with patch.object(CHECKIN, "log") as logger:
            result = CHECKIN.checkin_account(
                self.account, session_factory=lambda: session
            )

        self.assertFalse(result)
        messages = " ".join(call.args[0] for call in logger.call_args_list)
        self.assertNotIn("super-secret", messages)
        self.assertNotIn("user@example.com", messages)
        self.assertIn("[REDACTED]", messages)


if __name__ == "__main__":
    unittest.main()
