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


class ParseGroupsTest(unittest.TestCase):
    def test_default_group(self) -> None:
        self.assertEqual(GROUP.parse_groups(""), [(43, "长赢同路人")])

    def test_multiple_groups(self) -> None:
        self.assertEqual(
            GROUP.parse_groups("43:长赢同路人, 58:其他小组"),
            [(43, "长赢同路人"), (58, "其他小组")],
        )

    def test_name_defaults_to_id(self) -> None:
        self.assertEqual(GROUP.parse_groups("58"), [(58, "58")])

    def test_rejects_invalid_or_duplicate(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是数字"):
            GROUP.parse_groups("abc:小组")
        with self.assertRaisesRegex(ValueError, "缺少小组名称"):
            GROUP.parse_groups("43:")
        with self.assertRaisesRegex(ValueError, "重复小组 ID"):
            GROUP.parse_groups("43:A,43:B")

    def test_groups_arg_overrides_env(self) -> None:
        with patch.dict(os.environ, {"QIEMAN_GROUPS": "43:环境小组"}, clear=True):
            self.assertEqual(
                GROUP.groups_from_env_and_args("58:参数小组"), [(58, "参数小组")]
            )
            self.assertEqual(
                GROUP.groups_from_env_and_args(""), [(43, "环境小组")]
            )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                GROUP.groups_from_env_and_args(""), [(43, "长赢同路人")]
            )


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
            "FEISHU_WIKI_TOKEN": "wikcn-root",
        }
        with patch.dict(os.environ, values, clear=True):
            config = GROUP.FeishuDocsConfig.from_env()
        assert config
        self.assertEqual(config.app_id, "new-app")
        self.assertEqual(config.wiki_token, "wikcn-root")
        self.assertEqual(config.category, "且慢小组")
        self.assertTrue(config.upload_images)

    def test_flags_and_text_limit(self) -> None:
        values = {
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_WIKI_TOKEN": "wikcn",
            "QIEMAN_FEISHU_UPLOAD_IMAGES": "false",
            "QIEMAN_FEISHU_TEXT_LIMIT": "1500",
        }
        with patch.dict(os.environ, values, clear=True):
            config = GROUP.FeishuDocsConfig.from_env()
        assert config
        self.assertFalse(config.upload_images)
        self.assertEqual(config.text_limit, 1500)


class FeishuDocsClientTest(unittest.TestCase):
    def _config(self) -> GROUP.FeishuDocsConfig:
        return GROUP.FeishuDocsConfig("app", "secret", "wikcn-root")

    def test_resolve_root_prefers_node_token(self) -> None:
        client = GROUP.FeishuDocsClient(self._config())
        with patch.object(client, "request") as request:
            request.return_value = {
                "code": 0,
                "data": {"node": {"space_id": "sp1", "node_token": "wikN", "title": "投资文章"}},
            }
            self.assertEqual(client.resolve_root(), ("sp1", "wikN"))

    def test_resolve_root_falls_back_to_space(self) -> None:
        client = GROUP.FeishuDocsClient(self._config())

        def fake_request(method: str, endpoint: str, **kwargs) -> dict:
            if "get_node" in endpoint:
                raise RuntimeError("node not found")
            return {"code": 0, "data": {}}

        with patch.object(client, "request", side_effect=fake_request):
            self.assertEqual(client.resolve_root(), ("wikcn-root", ""))

    def test_create_node_posts_wiki_payload(self) -> None:
        client = GROUP.FeishuDocsClient(self._config())
        client._root = ("sp1", "wikN")
        with patch.object(client, "request") as request:
            request.return_value = {
                "code": 0,
                "data": {"node": {"node_token": "wikNew", "obj_token": "doxcnNew"}},
            }
            created = client.create_node("wikN", "标题")
        _, kwargs = request.call_args
        self.assertEqual(
            kwargs["data"],
            {"obj_type": "docx", "title": "标题", "parent_node_token": "wikN"},
        )
        self.assertEqual(created, {"node_token": "wikNew", "obj_token": "doxcnNew"})

    def test_list_children_paginates(self) -> None:
        client = GROUP.FeishuDocsClient(self._config())
        client._root = ("sp1", "wikN")
        pages = [
            {"code": 0, "data": {"items": [{"node_token": "a", "title": "A"}], "has_more": True, "page_token": "p2"}},
            {"code": 0, "data": {"items": [{"node_token": "b", "title": "B"}], "has_more": False}},
        ]
        with patch.object(client, "request", side_effect=pages) as request:
            items = client.list_children("wikN")
        self.assertEqual([item["title"] for item in items], ["A", "B"])
        self.assertEqual(request.call_count, 2)


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
        index = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index_43.json"))
        index.category_node_token = "nd-cat"
        index.group_node_token = "nd-g"
        index.authors = {"ETF拯救世界": "nd-a"}
        index.posts = {"93181": {"node_token": "wik1", "doc_id": "doccn1", "author": "ETF拯救世界", "title": "t"}}
        return index

    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._index(tmp).save()
            loaded = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index_43.json"))
            loaded.load()
            self.assertEqual(loaded.recorded_ids(), {93181})
            self.assertEqual(loaded.authors["ETF拯救世界"], "nd-a")
            self.assertEqual(loaded.group_node_token, "nd-g")

    def test_corrupted_file_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "feishu_index_43.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            index = GROUP.FeishuIndex(path)
            index.load()
            self.assertEqual(index.recorded_ids(), set())

    def test_rebuild_scans_under_group_node(self) -> None:
        report = GROUP.organise_post(raw_post())
        client = MagicMock()
        client.config = GROUP.FeishuDocsConfig("app", "secret", "wikcn-root")
        client.resolve_root.return_value = ("sp1", "nd-root")
        client.ensure_node.side_effect = lambda parent, name: {
            "且慢小组": "nd-cat",
            "长赢同路人": "nd-g",
        }[name]
        client.list_children.side_effect = lambda token: (
            [{"title": "ETF拯救世界", "node_token": "nd-a"}]
            if token == "nd-g"
            else [{"title": GROUP.doc_title(report), "node_token": "wik9"}]
        )
        index = GROUP.FeishuIndex("/nonexistent/feishu_index_43.json")
        index.rebuild(client, [report], "长赢同路人")
        self.assertEqual(index.recorded_ids(), {93181})
        self.assertEqual(index.posts["93181"]["node_token"], "wik9")
        self.assertEqual(index.group_node_token, "nd-g")


class FeishuSinkTest(unittest.TestCase):
    def _sink(self, tmp: str, upload_images: bool = False) -> tuple[GROUP.FeishuSink, MagicMock]:
        config = GROUP.FeishuDocsConfig(
            "app", "secret", "wikcn-root", upload_images=upload_images
        )
        client = MagicMock()
        client.config = config
        client.create_node.return_value = {"node_token": "wikPost", "obj_token": "doccn1"}
        client.upload_image.return_value = "ftoken"
        index = GROUP.FeishuIndex(os.path.join(tmp, "feishu_index_43.json"))
        index.category_node_token = "nd-cat"
        index.group_node_token = "nd-g"
        index.authors = {"ETF拯救世界": "nd-a"}
        return GROUP.FeishuSink(client, index, "长赢同路人"), client

    def test_save_writes_blocks_and_updates_index(self) -> None:
        report = GROUP.organise_post(raw_post())
        comments = [GROUP.organise_comment(raw_comment(), [])]
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp)
            node_token = sink.save(report, comments)
            index_saved = os.path.exists(sink.index.path)
        self.assertTrue(index_saved)
        self.assertEqual(node_token, "wikPost")
        client.create_node.assert_called_once_with("nd-a", GROUP.doc_title(report))
        document_id = client.append_blocks.call_args[0][0]
        self.assertEqual(document_id, "doccn1")
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
        self.assertEqual(sink.index.posts["93181"]["node_token"], "wikPost")

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
            node_token = sink.save(report, [])
        self.assertEqual(node_token, "wikPost")
        blocks = client.append_blocks.call_args[0][1]
        self.assertFalse(any(b["block_type"] == 27 for b in blocks))
        self.assertTrue(any("🖼" in str(b) for b in blocks))

    def test_new_author_node_is_created_under_group(self) -> None:
        report = GROUP.organise_post(raw_post(author="新博主"))
        with tempfile.TemporaryDirectory() as tmp:
            sink, client = self._sink(tmp)
            sink.index.category_node_token = ""  # 迫使完整节点链走创建逻辑
            sink.index.group_node_token = ""
            client.resolve_root.return_value = ("sp1", "nd-root")
            client.ensure_node.side_effect = lambda parent, name: {
                "且慢小组": "nd-cat",
                "长赢同路人": "nd-g",
                "新博主": "nd-new",
            }[name]
            sink.save(report, [])
        client.ensure_node.assert_any_call("nd-root", "且慢小组")
        client.ensure_node.assert_any_call("nd-cat", "长赢同路人")
        client.ensure_node.assert_any_call("nd-g", "新博主")
        self.assertEqual(sink.index.authors["新博主"], "nd-new")
        self.assertEqual(sink.index.group_node_token, "nd-g")


class MarkerTest(unittest.TestCase):
    def test_no_new_posts_marker(self) -> None:
        self.assertEqual(GROUP.render_marker([]), "NOTIFY_POSTS=0 今日无新帖")

    def test_marker_lists_authors_and_titles(self) -> None:
        archived = [
            {"group": "长赢同路人", "report": GROUP.organise_post(raw_post(post_id=i, title=f"标题{i}"))}
            for i in (1, 2, 3, 4, 5)
        ]
        marker = GROUP.render_marker(archived)
        self.assertTrue(marker.startswith("NOTIFY_POSTS=1 新帖 5 篇："))
        self.assertIn("等 5 篇", marker)
        self.assertLessEqual(len(marker), 121)

    def test_marker_adds_group_label_when_multiple_groups(self) -> None:
        archived = [
            {"group": "小组A", "report": GROUP.organise_post(raw_post(title="标题A"))},
            {"group": "小组B", "report": GROUP.organise_post(raw_post(title="标题B"))},
        ]
        marker = GROUP.render_marker(archived, multi_group=True)
        self.assertIn("[小组A]ETF拯救世界《标题A》", marker)
        self.assertIn("[小组B]ETF拯救世界《标题B》", marker)


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
            base = os.path.join(tmp, "posts")
            group_dir = os.path.join(base, "qieman_posts_43")
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)):
                code, output = self.run_main("--posts-dir", base)
            self.assertEqual(code, 0)
            self.assertIn("NOTIFY_POSTS=1 新帖 2 篇", output)
            self.assertEqual(len(os.listdir(group_dir)), 2)

            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)):
                code, output = self.run_main("--posts-dir", base)
            self.assertEqual(code, 0)
            self.assertIn("NOTIFY_POSTS=0 今日无新帖", output)
            self.assertIn("文件：新增 0、失败 0", output)

    def test_feishu_backfills_post_missing_only_in_feishu(self) -> None:
        posts = [raw_post(post_id=101)]
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "posts")
            GROUP.FileSink(os.path.join(base, "qieman_posts_43")).save(
                GROUP.organise_post(posts[0]), []
            )
            sink_client = MagicMock()
            sink_client.config = GROUP.FeishuDocsConfig("app", "secret", "wikcn-root")
            sink_client.resolve_root.return_value = ("sp1", "nd-root")
            sink_client.ensure_node.side_effect = lambda parent, name: {
                "且慢小组": "nd-cat",
                "长赢同路人": "nd-g",
                "ETF拯救世界": "nd-a",
            }[name]
            sink_client.create_node.return_value = {"node_token": "wikPost", "obj_token": "doccn1"}
            env = {
                "FEISHU_APP_ID": "app",
                "FEISHU_APP_SECRET": "secret",
                "FEISHU_WIKI_TOKEN": "wikcn-root",
            }
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(GROUP, "QiemanClient", return_value=self._client(posts)), \
                    patch.object(GROUP, "FeishuDocsClient", return_value=sink_client), \
                    patch.object(GROUP.FeishuIndex, "rebuild"):
                code, output = self.run_main("--posts-dir", base)
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

    def test_groups_configured_by_environment(self) -> None:
        posts = [raw_post(post_id=101)]
        env = {"QIEMAN_GROUPS": "99:测试小组"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), \
                patch.object(GROUP, "QiemanClient", return_value=self._client(posts)) as factory:
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
            group_dir_exists = os.path.exists(
                os.path.join(tmp, "posts", "qieman_posts_99")
            )
        self.assertEqual(code, 0)
        self.assertIn("开始归档且慢小组 99（测试小组）", output)
        factory.return_value.fetch_all_posts.assert_called_once_with(99)
        self.assertTrue(group_dir_exists)

    def test_invalid_groups_exits_with_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"QIEMAN_GROUPS": "abc:小组"}, clear=True):
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
        self.assertEqual(code, 2)
        self.assertIn("ERROR", output)

    def test_multiple_groups_isolate_dirs_and_index_files(self) -> None:
        client = MagicMock()
        client.fetch_all_posts.side_effect = lambda group_id: (
            [raw_post(post_id=201, title="A组帖子")] if group_id == 43
            else [raw_post(post_id=202, title="B组帖子")]
        )
        client.fetch_comments.return_value = []
        env = {"QIEMAN_GROUPS": "43:A组,44:B组"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), \
                patch.object(GROUP, "QiemanClient", return_value=client):
            code, output = self.run_main("--posts-dir", os.path.join(tmp, "posts"))
            base = os.path.join(tmp, "posts")
            dir_43 = os.path.exists(os.path.join(base, "qieman_posts_43"))
            dir_44 = os.path.exists(os.path.join(base, "qieman_posts_44"))
        self.assertEqual(code, 0)
        self.assertIn("NOTIFY_POSTS=1 新帖 2 篇", output)
        self.assertIn("[A组]ETF拯救世界《A组帖子》", output)
        self.assertIn("[B组]ETF拯救世界《B组帖子》", output)
        self.assertTrue(dir_43)
        self.assertTrue(dir_44)

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
