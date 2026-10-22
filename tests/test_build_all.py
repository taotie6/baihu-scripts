from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_all", ROOT / "build-all.py")
assert SPEC is not None and SPEC.loader is not None
BUILD_ALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_ALL)


class ManifestSourceTests(unittest.TestCase):
    def write_manifest(self, source: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manifest = Path(temporary.name) / "apps" / "example" / "app.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "\n".join(
                [
                    'spec_version: "v1"',
                    'id: "example"',
                    'name: "Example"',
                    'version: "1.0.0"',
                    'author: "tester"',
                    'category: "test"',
                    'last_commit: "2026-10-21T00:00:00+08:00"',
                    "sources:",
                    source,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return manifest

    def test_self_repo_requires_app_sparse_path(self) -> None:
        manifest = self.write_manifest(
            '\n'.join(
                [
                    '  - id: "repo"',
                    '    source_type: "git"',
                    '    source_url: "https://github.com/taotie6/baihu-scripts.git"',
                ]
            )
        )

        with self.assertRaisesRegex(ValueError, "必须设置 path"):
            BUILD_ALL.load_manifest(manifest, "taotie6", "baihu-scripts", "main")

    def test_self_repo_accepts_app_sparse_path(self) -> None:
        manifest = self.write_manifest(
            '\n'.join(
                [
                    '  - id: "repo"',
                    '    source_type: "git"',
                    '    source_url: "https://github.com/taotie6/baihu-scripts.git"',
                    '    path: "apps/example/main"',
                ]
            )
        )

        metadata, _ = BUILD_ALL.load_manifest(
            manifest, "taotie6", "baihu-scripts", "main"
        )

        self.assertEqual(metadata["id"], "example")

    def test_external_repo_does_not_require_sparse_path(self) -> None:
        manifest = self.write_manifest(
            '\n'.join(
                [
                    '  - id: "repo"',
                    '    source_type: "git"',
                    '    source_url: "https://github.com/example/project.git"',
                ]
            )
        )

        metadata, _ = BUILD_ALL.load_manifest(
            manifest, "taotie6", "baihu-scripts", "main"
        )

        self.assertEqual(metadata["id"], "example")


if __name__ == "__main__":
    unittest.main()
