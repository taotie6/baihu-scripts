from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import struct
import unittest
from pathlib import Path
from types import ModuleType

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QoderCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("test_qoder", "apps/qoder-cn-checkin/main/checkin.py")

    def test_panel_auth_drops_refresh_token(self) -> None:
        source = {
            "token": "access-token",
            "refreshToken": "must-not-leave-the-client",
            "expiresAt": "2026-10-30T00:00:00Z",
            "user": {"id": "42", "name": "tester"},
        }
        self.assertEqual(
            self.module.panel_auth(source),
            {
                "token": "access-token",
                "expiresAt": "2026-10-30T00:00:00Z",
                "user": {"id": "42", "name": "tester"},
            },
        )


class TraeCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("test_trae", "apps/trae-cn-checkin/main/checkin.py")

    def test_decrypt_auth(self) -> None:
        key = bytes(range(32))
        sha = hashlib.sha512(key).digest()
        xor = bytes(a ^ b for a, b in zip(self.module.URE, self.module.DRE))
        derived = hashlib.sha512(sha + xor).digest()
        payload = {"token": "trae-token", "userRegion": {"region": "cn"}}
        plain = bytes(64) + json.dumps(payload).encode("utf-8")
        encrypted = AES.new(derived[:16], AES.MODE_CBC, derived[16:32]).encrypt(pad(plain, 16))
        blob = b"HEADER" + key + encrypted
        self.assertEqual(self.module.decrypt_auth(base64.b64encode(blob).decode()), payload)


class WorkBuddyCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("test_workbuddy", "apps/workbuddy-checkin/main/checkin.py")

    def _encrypt_prefixed(self, token: str, *, json_envelope: bool) -> str:
        nonce = bytes(range(12))
        cipher = AES.new(self.module._WB_KEY, AES.MODE_GCM, nonce=nonce, mac_len=16)
        cipher.update(
            b"WB-AAD\x00" + b"\x01" + self.module._WB_KEY_ID.encode("ascii") + nonce
        )
        ciphertext, auth_tag = cipher.encrypt_and_digest(token.encode())
        if json_envelope:
            packed = json.dumps(
                {
                    "keyId": self.module._WB_KEY_ID,
                    "nonce": base64.b64encode(nonce).decode(),
                    "ciphertext": base64.b64encode(ciphertext).decode(),
                    "authTag": base64.b64encode(auth_tag).decode(),
                },
                separators=(",", ":"),
            ).encode()
        else:
            packed = nonce + ciphertext + auth_tag
        return "$wbEncrypted:" + base64.b64encode(packed).decode()

    def test_decrypt_prefixed_binary_envelope(self) -> None:
        value = self._encrypt_prefixed("binary-token", json_envelope=False)
        self.assertEqual(self.module.open_wb_field(value), "binary-token")

    def test_decrypt_prefixed_json_envelope(self) -> None:
        value = self._encrypt_prefixed("json-token", json_envelope=True)
        self.assertEqual(self.module.open_wb_field(value), "json-token")

    def test_decrypt_legacy_object_envelope(self) -> None:
        nonce = bytes(range(12))
        suite = 1
        tag = b"WBEV1"
        aad = b"".join(
            [
                b"WB-AAD\x00",
                b"\x01",
                struct.pack(">I", len(tag)) + tag,
                struct.pack(">I", 6) + b"sym-v1",
                struct.pack(">I", suite),
                struct.pack(">I", len(self.module._WB_KEY_ID))
                + self.module._WB_KEY_ID.encode(),
                b"\x02\x00\x00",
            ]
        )
        cipher = AES.new(self.module._WB_KEY, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        ciphertext, auth_tag = cipher.encrypt_and_digest(b"legacy-token")
        envelope = {
            "keyId": self.module._WB_KEY_ID,
            "suite": suite,
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "authTag": base64.b64encode(auth_tag).decode(),
        }
        value = {
            "$wbEncrypted": 1,
            "scheme": "sym-v1",
            "envelope": base64.b64encode(json.dumps(envelope).encode()).decode(),
        }
        self.assertEqual(self.module.open_wb_field(value), "legacy-token")


if __name__ == "__main__":
    unittest.main()
