#!/usr/bin/env python3
"""校验应用 Manifest 中的变量/机密分类符合仓库策略。"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_SUFFIXES = (
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_COOKIE",
    "_SESSIONS",
    "_AUTHS",
    "_ACCOUNTS",
    "_PAT",
    "_DEVICE_ID",
)
SENSITIVE_EXACT = {"IPCHECK_POOL_ACCOUNT"}
PATH_SUFFIXES = ("_FILE", "_DIR", "_PATHS")


class EnvSchemaPolicyTest(unittest.TestCase):
    def manifests(self):
        for path in sorted((ROOT / "apps").glob("*/app.yaml")):
            yield path, yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_credentials_and_identifiers_are_secrets(self) -> None:
        for path, manifest in self.manifests():
            for item in manifest.get("env_schema", []):
                key = item["key"]
                if key in SENSITIVE_EXACT or (
                    key.endswith(SENSITIVE_SUFFIXES) and not key.endswith(PATH_SUFFIXES)
                ):
                    with self.subTest(manifest=path.parent.name, key=key):
                        self.assertEqual(item["type"], "secret")

    def test_app_ids_channels_urls_and_paths_are_normal(self) -> None:
        for path, manifest in self.manifests():
            for item in manifest.get("env_schema", []):
                key = item["key"]
                if key.endswith(("_APP_ID", "_CHANNEL", "_URL", "_FILE", "_DIR", "_PATHS")):
                    with self.subTest(manifest=path.parent.name, key=key):
                        self.assertEqual(item["type"], "string")

    def test_structured_controls_have_compatible_defaults(self) -> None:
        supported = {"string", "secret", "number", "boolean", "select"}
        for path, manifest in self.manifests():
            keys: set[str] = set()
            for item in manifest.get("env_schema", []):
                key = item["key"]
                with self.subTest(manifest=path.parent.name, key=key):
                    self.assertNotIn(key, keys, "同一 Manifest 内变量名重复")
                    keys.add(key)
                    self.assertIn(item["type"], supported)
                    if item["type"] == "number" and "default" in item:
                        self.assertIsInstance(item["default"], (int, float))
                    if item["type"] == "boolean" and "default" in item:
                        self.assertIsInstance(item["default"], bool)
                    if item["type"] == "select":
                        self.assertTrue(item.get("options"))
                    if item["type"] == "secret":
                        self.assertIsNone(item.get("default"))


if __name__ == "__main__":
    unittest.main()
