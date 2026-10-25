#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).resolve().parents[1] / "main" / "fetch_group_posts.py"
SPEC = importlib.util.spec_from_file_location("qieman_group_sync", MODULE_PATH)
assert SPEC and SPEC.loader
GROUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GROUP
SPEC.loader.exec_module(GROUP)

TZ = ZoneInfo("Asia/Shanghai")


def raw_post(
    post_id: int = 93181,
    title: str = "发车说明",
    created: str = "2026-10-23T14:48:32",
    author: str = "ETF拯救世界",
    contents: list | None = None,
    comment_num: int = 1,
) -> dict:
    return {
        "id": post_id,
        "userName": author,
        "createdAt": created,
        "title": title,
        "commentNum": comment_num,
        "content": {
            "contents": contents
            if contents is not None
            else [
                {"contentType": 1, "detail": "第一段\n\n第二段"},
                {"contentType": 2, "url": "https://cdn.qieman.com/a.jpg"},
            ]
        },
    }


def raw_comment(
    cid: int = 2503273,
    author: str = "路人甲",
    content: str = "说得好",
    comment_num: int = 0,
    children: list | None = None,
) -> dict:
    return {
        "id": cid,
        "userName": author,
        "content": content,
        "commentNum": comment_num,
        "children": children or [],
        "createdAt": "2026-10-23T15:00:00",
    }


class ParseCreatedAtTest(unittest.TestCase):
    def test_iso_without_timezone_is_shanghai(self) -> None:
        self.assertEqual(
            GROUP.parse_created_at("2026-10-23T14:48:32"),
            int(__import__("datetime").datetime(2026, 10, 23, 14, 48, 32, tzinfo=TZ).timestamp() * 1000),
        )

    def test_invalid_values_return_zero(self) -> None:
        self.assertEqual(GROUP.parse_created_at(""), 0)
        self.assertEqual(GROUP.parse_created_at("garbage"), 0)


class PostParagraphsTest(unittest.TestCase):
    def test_contents_keep_text_and_image_order(self) -> None:
        paragraphs = GROUP.post_paragraphs(raw_post())
        self.assertEqual([p.kind for p in paragraphs], ["text", "text", "image"])
        self.assertEqual(paragraphs[2].url, "https://cdn.qieman.com/a.jpg")

    def test_rich_content_html_fallback(self) -> None:
        post = raw_post(contents=[])
        post["richContent"] = "<p>你好</p><img src=\"https://x/1.jpg\"><p>再见</p>"
        paragraphs = GROUP.post_paragraphs(post)
        self.assertEqual([p.kind for p in paragraphs], ["text", "image", "text"])
        self.assertEqual(paragraphs[0].text, "你好")
        self.assertEqual(paragraphs[1].url, "https://x/1.jpg")
        self.assertEqual(paragraphs[2].text, "再见")

    def test_images_field_fallback(self) -> None:
        post = raw_post(contents=[])
        post["images"] = [{"url": "https://x/2.jpg"}]
        paragraphs = GROUP.post_paragraphs(post)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0].url, "https://x/2.jpg")


class OrganiseTest(unittest.TestCase):
    def test_title_falls_back_to_body(self) -> None:
        report = GROUP.organise_post(raw_post(title=""))
        self.assertEqual(report["title"], "第一段 第二段"[:15])
        self.assertEqual(report["post_id"], 93181)
        self.assertIn("postId=93181", report["link"])

    def test_author_falls_back_to_user_info(self) -> None:
        post = raw_post()
        post["userName"] = ""
        post["userInfo"] = {"nickName": "昵称"}
        self.assertEqual(GROUP.organise_post(post)["author"], "昵称")

    def test_collect_comments_prefers_children_when_enough(self) -> None:
        client = MagicMock()
        client.fetch_comments.return_value = [
            raw_comment(comment_num=1, children=[{"id": 111, "content": "回复", "createdAt": "2026-10-23T15:01:00"}])
        ]
        comments = GROUP.collect_comments(client, 93181)
        client.fetch_replies.assert_not_called()
        self.assertEqual(comments[0]["replies"][0]["comment_id"], 111)

    def test_collect_comments_pages_replies_when_short(self) -> None:
        client = MagicMock()
        client.fetch_comments.return_value = [raw_comment(comment_num=2, children=[])]
        client.fetch_replies.return_value = [
            {"id": 211, "content": "一楼", "createdAt": "2026-10-23T15:02:00"},
            {"id": 212, "content": "二楼", "createdAt": "2026-10-23T15:03:00"},
        ]
        comments = GROUP.collect_comments(client, 93181)
        client.fetch_replies.assert_called_once_with(2503273)
        self.assertEqual(len(comments[0]["replies"]), 2)

    def test_reply_to_user_resolves_from_to_user_info(self) -> None:
        reply = {
            "id": 300,
            "userName": "路人乙",
            "content": "回复",
            "createdAt": "2026-10-23T15:05:00",
            "toUserInfo": {"nickName": "被回复人"},
        }
        comment = GROUP.organise_comment(raw_comment(), [reply])
        self.assertEqual(comment["replies"][0]["to_user"], "被回复人")

    def test_reply_to_user_empty_when_no_source(self) -> None:
        reply = {"id": 301, "userName": "路人乙", "content": "回复"}
        comment = GROUP.organise_comment(raw_comment(), [reply])
        self.assertEqual(comment["replies"][0]["to_user"], "")


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.report = GROUP.organise_post(raw_post())
        self.comments = [
            GROUP.organise_comment(
                raw_comment(),
                [{"id": 300, "userName": "路人乙", "toUserName": "其他人",
                  "content": "同意", "createdAt": "2026-10-23T15:05:00"}],
            )
        ]

    def test_markdown_contains_front_matter_and_comments(self) -> None:
        markdown = GROUP.render_markdown(self.report, self.comments)
        self.assertIn("ID: 93181", markdown)
        self.assertIn("## ETF拯救世界 `2026年10月23日`", markdown)
        self.assertIn("第一段", markdown)
        self.assertIn("![图片](https://cdn.qieman.com/a.jpg)", markdown)
        self.assertIn("- `路人甲`：说得好", markdown)
        self.assertIn("- `路人乙` > `其他人`：同意", markdown)

    def test_doc_title_format(self) -> None:
        self.assertEqual(GROUP.doc_title(self.report), "2026-10-23 14.48-发车说明")

    def test_doc_title_sanitizes(self) -> None:
        report = GROUP.organise_post(raw_post(title='2026/10"计划:说明'))
        title = GROUP.doc_title(report)
        for forbidden in '\\/*?:"<>|':
            self.assertNotIn(forbidden, title)


class FileSinkTest(unittest.TestCase):
    def test_save_and_existing_ids_roundtrip(self) -> None:
        report = GROUP.organise_post(raw_post())
        with tempfile.TemporaryDirectory() as tmp:
            sink = GROUP.FileSink(tmp)
            self.assertEqual(sink.existing_ids(), set())
            path = sink.save(report, [])
            self.assertTrue(os.path.exists(path))
            self.assertIn("93181_ETF拯救世界_发车说明.md", os.path.basename(path))
            self.assertEqual(sink.existing_ids(), {93181})


class FeishuDocsConfigTest(unittest.TestCase):
    def test_not_configured_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(GROUP.FeishuDocsConfig.from_env())

    def test_partial_configuration_is_rejected(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app"}, clear=True):
            with self.assertRaisesRegex(ValueError, "还需配置"):
                GROUP.FeishuDocsConfig.from_env()

    def test_complete_configuration_and_joined_values(self) -> None:
        values = {
            "FEISHU_APP_ID": "old&new-app",
            "FEISHU_APP_SECRET": "new-secret",
            "FEISHU_FOLDER_TOKEN": "fld-root",
        }
        with patch.dict(os.environ, values, clear=True):
            config = GROUP.FeishuDocsConfig.from_env()
        assert config
        self.assertEqual(config.app_id, "new-app")
        self.assertEqual(config.folder_token, "fld-root")
        self.assertEqual(config.category, "且慢小组")
        self.assertTrue(config.upload_images)

    def test_flags_and_text_limit(self) -> None:
        values = {
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_FOLDER_TOKEN": "fld",
            "QIEMAN_FEISHU_UPLOAD_IMAGES": "false",
            "QIEMAN_FEISHU_TEXT_LIMIT": "1500",
        }
        with patch.dict(os.environ, values, clear=True):
            config = GROUP.FeishuDocsConfig.from_env()
        assert config
        self.assertFalse(config.upload_images)
        self.assertEqual(config.text_limit, 1500)


class BlockBuilderTest(unittest.TestCase):
    def test_block_shapes(self) -> None:
        self.assertEqual(GROUP.heading_block("标题", 2)["block_type"], 4)
        self.assertIn("heading2", GROUP.heading_block("标题", 2))
        self.assertEqual(GROUP.heading_block("评论", 3)["block_type"], 5)
        text = GROUP.text_block("原文链接", "https://qieman.com/x")
        run = text["text"]["elements"][0]["text_run"]
        self.assertEqual(run["text_element_style"]["link"]["url"], "https://qieman.com/x")
        self.assertEqual(GROUP.bullet_block("内容")["block_type"], 12)
        self.assertEqual(GROUP.image_block("ft")["image"]["token"], "ft")


class FeishuIndexTest(unittest.TestCase):
    def _index(self, tmp: str) -> GROUP.FeishuIndex:
        index = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index.json"))
        index.category_folder_id = "fld-cat"
        index.authors = {"ETF拯救世界": "fld-a"}
        index.posts = {"93181": {"doc_id": "doccn1", "author": "ETF拯救世界", "title": "t"}}
        return index

    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._index(tmp).save()
            loaded = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index.json"))
            loaded.load()
            self.assertEqual(loaded.recorded_ids(), {93181})
            self.assertEqual(loaded.authors["ETF拯救世界"], "fld-a")

    def test_corrupted_file_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "feishu_index.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            index = GROUP.FeishuIndex(path)
            index.load()
            self.assertEqual(index.recorded_ids(), set())

    def test_rebuild_matches_documents_by_deterministic_title(self) -> None:
        report = GROUP.organise_post(raw_post())
        client = MagicMock()
        client.config = GROUP.FeishuDocsConfig("app", "secret", "fld-root")
        client.ensure_folder.return_value = "fld-cat"
        client.list_children.side_effect = lambda token: (
            [{"type": "folder", "name": "ETF拯救世界", "token": "fld-a"}]
            if token == "fld-cat"
            else [{"name": GROUP.doc_title(report), "token": "doccn9"}]
        )
        index = GROUP.FeishuIndex("/nonexistent/feishu_index.json")
        index.rebuild(client, [report])
        self.assertEqual(index.recorded_ids(), {93181})
        self.assertEqual(index.posts["93181"]["doc_id"], "doccn9")


class FeishuSinkTest(unittest.TestCase):
    def _sink(self, tmp: str, upload_images: bool = False) -> tuple[GROUP.FeishuSink, MagicMock]:
        config = GROUP.FeishuDocsConfig(
            "app", "secret", "fld-root", upload_images=upload_images
        )
        client = MagicMock()
        client.config = config
        client.create_document.return_value = "doccn1"
        client.upload_image.return_value = "ftoken"
        index = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index.json"))
        index.category_folder_id = "fld-cat"
        index.authors = {"ETF拯救世界": "fld-a"}
        return GROUP.FeishuSink(client, index), client

    def test_save_writes_blocks_and_updates_index(self) -> None:
        report = GROUP.organise_post(raw_post())
        comments = [GROUP.organise_comment(raw_comment(), [])]
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp)
            doc_id = sink.save(report, comments)
            index_saved = os.path.exists(sink.index.path)
        self.assertTrue(index_saved)
        self.assertEqual(doc_id, "doccn1")
        client.create_document.assert_called_once_with(
            GROUP.doc_title(report), "fld-a"
        )
        blocks = client.append_blocks.call_args[0][1]
        self.assertEqual(blocks[0]["block_type"], 4)
        self.assertEqual(blocks[1]["text"]["elements"][0]["text_run"]["content"], "原文链接")
        self.assertEqual(blocks[2]["text"]["elements"][0]["text_run"]["content"], "第一段")
        self.assertEqual(blocks[3]["text"]["elements"][0]["text_run"]["content"], "第二段")
        # 无图片上传模式：图片以文本链接呈现
        self.assertIn("🖼 https://cdn.qieman.com/a.jpg", blocks[4]["text"]["elements"][0]["text_run"]["content"])
        self.assertEqual(blocks[5]["block_type"], 5)  # ### 评论
        self.assertIn("路人甲：说得好", blocks[6]["bullet"]["elements"][0]["text_run"]["content"])
        self.assertEqual(sink.index.recorded_ids(), {93181})

    def test_save_uploads_images_when_enabled(self) -> None:
        report = GROUP.organise_post(raw_post())
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp, upload_images=True)
            sink.save(report, [])
        client.upload_image.assert_called_once()
        blocks = client.append_blocks.call_args[0][1]
        image_blocks = [b for b in blocks if b["block_type"] == 27]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0]["image"]["token"], "ftoken")

    def test_image_upload_failure_falls_back_to_link(self) -> None:
        report = GROUP.organise_post(raw_post())
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp, upload_images=True)
            client.upload_image.side_effect = RuntimeError("too large")
            doc_id = sink.save(report, [])
        self.assertEqual(doc_id, "doccn1")
        blocks = client.append_blocks.call_args[0][1]
        self.assertFalse(any(b["block_type"] == 27 for b in blocks))
        self.assertTrue(any("🖼" in str(b) for b in blocks))

    def test_new_author_folder_is_created(self) -> None:
        report = GROUP.organise_post(raw_post(author="新博主"))
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp)
            sink.index.category_folder_id = ""  # 迫使二级目录也走创建逻辑
            client.ensure_folder.side_effect = lambda parent, name: (
                "fld-cat" if name == "且慢小组" else "fld-new"
            )
            sink.save(report, [])
        client.ensure_folder.assert_any_call("fld-root", "且慢小组")
        client.ensure_folder.assert_any_call("fld-cat", "新博主")
        self.assertEqual(sink.index.authors["新博主"], "fld-new")
        self.assertEqual(sink.index.category_folder_id, "fld-cat")


class MarkerTest(unittest.TestCase):
    def test_no_new_posts_marker(self) -> None:
        self.assertEqual(GROUP.render_marker([]), "NOTIFY_POSTS=0 今日无新帖")

    def test_marker_lists_authors_and_titles(self) -> None:
        pending = [
            (GROUP.organise_post(raw_post(post_id=i, title=f"标题{i}")), True, True)
            for i in (1, 2, 3, 4, 5)
        ]
        marker = GROUP.render_marker(pending)
        self.assertTrue(marker.startswith("NOTIFY_POSTS=1 新帖 5 篇："))
        self.assertIn("等 5 篇", marker)
        self.assertLessEqual(len(marker), 121)


class MainFlowTest(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        out: list[str] = []

        class _Tee(io.StringIO):
            def write(self, value: str) -> int:
                out.append(value)
                return super().write(value)

        with patch.object(sys, "argv", ["fetch_group_posts.py", *argv]), \
                redirect_stdout(_Tee()), redirect_stderr(io.StringIO()) as err:
            code = GROUP.main()
        return code, "".join(out) + err.getvalue()

    def _client(self, posts: list[dict]) -> MagicMock:
        client = MagicMock()
        client.fetch_all_posts.return_value = posts
        client.fetch_comments.return_value = []
        return client

    def test_file_channel_run_and_incremental(self) -> None:
        posts = [raw_post(post_id=101), raw_post(post_id=102, title="旧帖", created="2026-10-20T10:00:00")]
        with tempfile.TemporaryDirectory() as tmp:
            posts_dir = os.path.join(tmp, "posts")
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)):
                code, output = self.run_main("--posts-dir", posts_dir, "--group-id", "43")
            self.assertEqual(code, 0)
            self.assertIn("NOTIFY_POSTS=1 新帖 2 篇", output)
            self.assertEqual(len(os.listdir(posts_dir)), 2)

            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)):
                code, output = self.run_main("--posts-dir", posts_dir, "--group-id", "43")
            self.assertEqual(code, 0)
            self.assertIn("NOTIFY_POSTS=0 今日无新帖", output)
            self.assertIn("文件：新增 0、失败 0", output)

    def test_feishu_backfills_post_missing_only_in_feishu(self) -> None:
        posts = [raw_post(post_id=101)]
        with tempfile.TemporaryDirectory() as tmp:
            posts_dir = os.path.join(tmp, "posts")
            GROUP.FileSink(posts_dir).save(GROUP.organise_post(posts[0]), [])
            index = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index.json"))
            sink_client = MagicMock()
            sink_client.config = GROUP.FeishuDocsConfig("app", "secret", "fld-root")
            sink_client.ensure_folder.return_value = "fld-cat"
            sink_client.create_document.return_value = "doccn1"
            env = {
                "FEISHU_APP_ID": "app",
                "FEISHU_APP_SECRET": "secret",
                "FEISHU_FOLDER_TOKEN": "fld-root",
            }
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)), \
                    patch.object(GROUP, "FeishuDocsClient", return_value=sink_client), \
                    patch.object(GROUP.FeishuIndex, "rebuild"):
                code, output = self.run_main("--posts-dir", posts_dir)
            self.assertEqual(code, 0)
            self.assertIn("文件：新增 0、失败 0", output)
            self.assertIn("飞书：新增 1、失败 0", output)

    def test_partial_feishu_env_exits_with_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"FEISHU_APP_ID": "app"}, clear=True):
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 2)
        self.assertIn("ERROR", output)

    def test_all_sinks_disabled_exits_with_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"QIEMAN_SAVE_FILES": "false"}, clear=True):
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 2)

    def test_group_configured_by_environment(self) -> None:
        posts = [raw_post(post_id=101)]
        env = {"QIEMAN_GROUP_ID": "99", "QIEMAN_GROUP_NAME": "测试小组"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), \
                patch.object(GROUP, "QiemanClient", return_value=self._client(posts)) as factory:
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 0)
        self.assertIn("开始归档且慢小组 99（测试小组）", output)
        factory.return_value.fetch_all_posts.assert_called_once_with(99)

    def test_invalid_group_id_falls_back_with_warning(self) -> None:
        posts = [raw_post(post_id=101)]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"QIEMAN_GROUP_ID": "abc"}, clear=True), \
                patch.object(GROUP, "QiemanClient", return_value=self._client(posts)) as factory:
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 0)
        self.assertIn("WARN", output)
        factory.return_value.fetch_all_posts.assert_called_once_with(43)

    def test_list_failure_exits_non_zero(self) -> None:
        client = MagicMock()
        client.fetch_all_posts.side_effect = RuntimeError("网络错误")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(GROUP, "QiemanClient", return_value=client):
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
