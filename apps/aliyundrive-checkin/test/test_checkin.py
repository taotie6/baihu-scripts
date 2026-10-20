#!/usr/bin/env python3
"""阿里云盘账号配置解析测试。"""

from __future__ import annotations

import argparse
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "main" / "checkin.py"
SPEC = importlib.util.spec_from_file_location("aliyundrive_checkin", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKIN)


class LoadAccountsTest(unittest.TestCase):
    def load(self, value: str):
        with patch.dict(
            os.environ,
            {
                "ALIYUNDRIVE_ACCOUNTS": value,
                "ALIYUNDRIVE_REFRESH_TOKEN": "",
                "ALIYUNDRIVE_ACCOUNTS_FILE": "",
            },
            clear=False,
        ):
            return CHECKIN.load_accounts(argparse.Namespace(accounts_file=None))

    def test_accepts_literal_escaped_line_breaks_from_secret_variable(self) -> None:
        value = (
            r'[\n'
            r'  {"label":"account-1","refreshToken":"token-one"},\n'
            r'  {"label":"account-2","refreshToken":"token-two"}\n'
            r']'
        )

        self.assertEqual(
            self.load(value),
            [("account-1", "token-one"), ("account-2", "token-two")],
        )

    def test_preserves_escapes_inside_json_strings(self) -> None:
        value = r'[\n{"label":"account\n1","refreshToken":"token-one"}\n]'

        self.assertEqual(self.load(value), [("account\n1", "token-one")])

    def test_accepts_leading_invisible_character(self) -> None:
        value = '\u200b[{"label":"account-1","refreshToken":"token-one"}]'

        self.assertEqual(self.load(value), [("account-1", "token-one")])

    def test_accepts_common_copy_paste_wrappers(self) -> None:
        documents = [
            'ALIYUNDRIVE_ACCOUNTS=[{"refreshToken":"token-one"}]',
            '```json\n[{"refreshToken":"token-one"}]\n```',
            "'[{\"refreshToken\":\"token-one\"}]'",
            '［{\"refreshToken\":\"token-one\"}］',
        ]

        for value in documents:
            with self.subTest(value=value):
                self.assertEqual(self.load(value), [("\u8d26\u53f71", "token-one")])

    def test_invalid_json_reports_location_without_echoing_secret(self) -> None:
        secret = "secret-token-that-must-not-be-logged"
        value = f'[{{"refreshToken":"{secret}"}},]'

        with self.assertRaisesRegex(ValueError, r"第 1 行，第 \d+ 列") as raised:
            self.load(value)
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
