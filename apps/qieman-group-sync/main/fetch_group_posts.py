#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""且慢小组帖子归档（白虎面板版）。

抓取且慢小组（QIEMAN_GROUPS 可配多个，默认 43 长赢同路人）的帖子和评论，
支持双通道持久化：文件通道按小组输出 Markdown（默认开启），飞书通道以
知识库节点树“投资文章/且慢小组/{小组名}/{博主名}/{日期时间}-{标题}”
归档为 wiki 文档（配置 FEISHU_* 机密变量后启用）。日志末行输出
NOTIFY_POSTS 标记，配合白虎通知正则提取实现“有新帖才通知”。
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


QIEMAN_BASE_URL = "https://qieman.com"
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"
MAX_ENV_CANDIDATES = 4
DEFAULT_GROUP_ID = 43
DEFAULT_GROUP_NAME = "长赢同路人"
DEFAULT_FEISHU_CATEGORY = "且慢小组"
FILE_TITLE_LIMIT = 50
DOC_TITLE_LIMIT = 40
MARKER_SUMMARY_LIMIT = 120
DOC_BLOCK_BATCH = 100
QIEMAN_PAGE_SLEEP = 0.3
POST_SLEEP = 0.5


def log(message: str, *, error: bool = False) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)


def _timezone() -> ZoneInfo:
    name = os.environ.get("QIEMAN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log(f"WARN: 未找到时区 {name}，回退到 Asia/Shanghai")
        return ZoneInfo("Asia/Shanghai")


def _timestamp(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_created_at(value: Any) -> int:
    """把且慢的 createdAt（如 2026-10-23T14:48:32，北京时间）转毫秒时间戳。"""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})", text)
        if not match:
            return 0
        y, m, d, hh, mm = (int(part) for part in match.groups())
        return int(datetime(y, m, d, hh, mm, tzinfo=_timezone()).timestamp() * 1000)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone())
    return int(parsed.timestamp() * 1000)


def _date_label(created_ms: int, with_time: bool = False) -> str:
    if not created_ms:
        return ""
    moment = datetime.fromtimestamp(created_ms / 1000, tz=_timezone())
    label = f"{moment.year}年{moment.month}月{moment.day}日"
    if with_time:
        label += f" {moment.hour:02d}:{moment.minute:02d}"
    return label


def sanitize_text(value: str, limit: int) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|\r\n]', " ", str(value or "")).strip()
    return cleaned[:limit].strip()


def _env_candidates(raw: str, *, maximum: int = MAX_ENV_CANDIDATES) -> tuple[str, ...]:
    """解析白虎可能用 `&` 合并的同名变量，新值优先。"""
    values: list[str] = []
    for value in reversed(str(raw or "").split("&")):
        value = value.strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= maximum:
            break
    return tuple(values)


def _first_candidate(raw: str, default: str = "") -> str:
    values = _env_candidates(raw)
    return values[0] if values else default


def _env_flag(raw: str, default: str = "true") -> bool:
    return _first_candidate(raw, default).strip().lower() not in {"false", "0", "no"}


def parse_groups(raw: str) -> "list[tuple[int, str]]":
    """解析 43:长赢同路人,58:其他小组；未提供时使用默认小组。"""
    if not raw.strip():
        return [(DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME)]
    groups: list[tuple[int, str]] = []
    seen_ids: set[int] = set()
    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        code, separator, name = entry.partition(":")
        code = code.strip()
        name = name.strip() if separator else code
        if not code.isdigit():
            raise ValueError(f"QIEMAN_GROUPS 第 {position} 项的小组 ID 必须是数字：{code}")
        if not name:
            raise ValueError(f"QIEMAN_GROUPS 第 {position} 项缺少小组名称")
        group_id = int(code)
        if group_id in seen_ids:
            raise ValueError(f"QIEMAN_GROUPS 存在重复小组 ID：{group_id}")
        seen_ids.add(group_id)
        if any(existing == name for _, existing in groups):
            log(f"WARN: 小组名称重复（{name}），飞书归档会写入同一个文件夹")
        groups.append((group_id, name))
    if not groups:
        raise ValueError("QIEMAN_GROUPS 没有有效小组")
    return groups


def groups_from_env_and_args(groups_arg: str) -> list[tuple[int, str]]:
    """--groups 优先于 QIEMAN_GROUPS 变量；都未提供时使用默认小组。"""
    if groups_arg.strip():
        return parse_groups(groups_arg)
    env_groups = _first_candidate(os.environ.get("QIEMAN_GROUPS", ""), "")
    if env_groups:
        return parse_groups(env_groups)
    return parse_groups("")


def mask_token(value: str) -> str:
    token = str(value or "")
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---------------------------------------------------------------------------
# 且慢抓取层：帖子 / 评论 / 回复（公开接口，X-sign 签名即可）
# ---------------------------------------------------------------------------


class QiemanClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.bearer_token = _first_candidate(os.environ.get("QIEMAN_BEARER_TOKEN", ""))
        self.page_sleep = QIEMAN_PAGE_SLEEP

    @staticmethod
    def _sign() -> str:
        timestamp = int(time.time() * 1000)
        payload = str(math.floor(1.01 * timestamp))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32].upper()
        return f"{timestamp}{digest}"

    def _headers(self, referer: str) -> dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "X-sign": self._sign(),
            "Origin": QIEMAN_BASE_URL,
            "Referer": referer,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _get(self, path: str, *, params: dict[str, Any], referer: str) -> Any:
        response = self.session.get(
            QIEMAN_BASE_URL + path,
            params=params,
            headers=self._headers(referer),
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"且慢接口返回 {response.status_code}：接口可能已要求登录，"
                "请配置 QIEMAN_BEARER_TOKEN 机密变量后重试"
            )
        response.raise_for_status()
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(f"且慢接口 {path} 返回了非 JSON 响应") from exc

    @staticmethod
    def _items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("list") or data.get("records") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def fetch_all_posts(self, group_id: int) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "/pmdj/v2/community/post/list",
                params={
                    "pageNum": page,
                    "pageSize": 20,
                    "isSticky": "false",
                    "groupId": group_id,
                    "postType": 1,
                    "queryStrategy": "ONLY_GROUP_POST",
                    "isAwesome": "false",
                },
                referer=f"{QIEMAN_BASE_URL}/content/group-detail/{group_id}",
            )
            items = self._items(data)
            if not items:
                return posts
            posts.extend(items)
            if len(items) < 20:
                return posts
            page += 1
            time.sleep(self.page_sleep)

    def fetch_comments(self, post_id: int) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "/pmdj/v2/community/comment/list",
                params={"postId": post_id, "pageNum": page, "pageSize": 50, "sortType": "DEFAULT"},
                referer=f"{QIEMAN_BASE_URL}/content/content-detail?postId={post_id}",
            )
            items = self._items(data)
            if not items:
                return comments
            comments.extend(items)
            if len(items) < 50:
                return comments
            page += 1
            time.sleep(self.page_sleep)

    def fetch_replies(self, comment_id: int) -> list[dict[str, Any]]:
        replies: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "/pmdj/v2/community/reply/list",
                params={"commentId": comment_id, "pageNum": page, "pageSize": 50, "sortType": "DEFAULT"},
                referer=QIEMAN_BASE_URL + "/content/content-detail",
            )
            items = self._items(data)
            if not items:
                return replies
            replies.extend(items)
            if len(items) < 50:
                return replies
            page += 1
            time.sleep(self.page_sleep)


# ---------------------------------------------------------------------------
# 内容整理：帖子 → 标题 / 分段正文（文字与图片保序）/ 评论树
# ---------------------------------------------------------------------------


@dataclass
class Paragraph:
    kind: str  # "text" | "image"
    text: str = ""
    url: str = ""


def html_to_paragraphs(raw_html: str) -> list[Paragraph]:
    """把 richContent HTML 清洗为分段：图片保位，标签转换行。"""
    if not raw_html:
        return []
    image_urls: list[str] = []

    def stash_image(match: re.Match[str]) -> str:
        image_urls.append(match.group(1))
        return "\n\x00IMG\x00\n"

    text = re.sub(
        r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>',
        stash_image,
        raw_html,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<(br|p|div|li|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)

    paragraphs: list[Paragraph] = []
    index = 0
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        parts = block.split("\x00IMG\x00")
        for position, part in enumerate(parts):
            part = part.strip()
            if part:
                paragraphs.append(Paragraph(kind="text", text=part))
            if position < len(parts) - 1 and index < len(image_urls):
                paragraphs.append(Paragraph(kind="image", url=image_urls[index]))
                index += 1
    return paragraphs


def post_paragraphs(post: dict[str, Any]) -> list[Paragraph]:
    """按 contents 数组顺序提取文字与图片；空时回退 richContent / images。"""
    paragraphs: list[Paragraph] = []
    contents = (post.get("content") or {}).get("contents") or []
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            if item.get("contentType") == 1:
                detail = str(item.get("detail") or "").strip()
                if detail:
                    for piece in re.split(r"\n\s*\n", detail):
                        piece = piece.strip()
                        if piece:
                            paragraphs.append(Paragraph(kind="text", text=piece))
            elif item.get("contentType") == 2:
                url = str(item.get("url") or item.get("detail") or "").strip()
                if url:
                    paragraphs.append(Paragraph(kind="image", url=url))
    if not paragraphs and post.get("richContent"):
        paragraphs = html_to_paragraphs(str(post["richContent"]))
    if not paragraphs and post.get("images"):
        for image in post["images"]:
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                paragraphs.append(Paragraph(kind="image", url=str(url)))
    return paragraphs


def comment_text(item: dict[str, Any]) -> tuple[str, list[str]]:
    """评论/回复的文字与图片链接。"""
    text = str(item.get("content") or "").strip()
    images: list[str] = []
    for image in item.get("images") or []:
        url = image.get("url") if isinstance(image, dict) else image
        if url:
            images.append(str(url))
    return text, images


def display_name(item: dict[str, Any], name_key: str = "userName") -> str:
    user_info = item.get("userInfo") or {}
    return str(item.get(name_key) or user_info.get("nickName") or "未知用户")


def organise_post(post: dict[str, Any]) -> dict[str, Any]:
    """把帖子列表项整理为两通道共用的稳定结构。"""
    paragraphs = post_paragraphs(post)
    text_only = "\n".join(p.text for p in paragraphs if p.kind == "text")
    title = str(post.get("title") or "").strip()
    if not title:
        title = text_only[:15].replace("\n", " ").strip()
    if not title:
        title = "无标题"
    return {
        "post_id": _timestamp(post.get("id")),
        "author": display_name(post),
        "created_ms": parse_created_at(post.get("createdAt")),
        "title": title,
        "paragraphs": paragraphs,
        "comment_num": _timestamp(post.get("commentNum")),
        "link": f"{QIEMAN_BASE_URL}/content/content-detail?postId={post.get('id')}",
    }


def organise_comment(comment: dict[str, Any], replies: list[dict[str, Any]]) -> dict[str, Any]:
    text, images = comment_text(comment)
    return {
        "comment_id": _timestamp(comment.get("id")),
        "author": display_name(comment),
        "to_user": "",
        "text": text,
        "images": images,
        "created_ms": parse_created_at(comment.get("createdAt")),
        "replies": [
            {
                "comment_id": _timestamp(reply.get("id")),
                "author": display_name(reply),
                "to_user": str(
                    reply.get("toUserName")
                    or (reply.get("toUserInfo") or {}).get("toUserName")
                    or (reply.get("toUserInfo") or {}).get("nickName")
                    or ""
                ),
                "text": comment_text(reply)[0],
                "images": comment_text(reply)[1],
                "created_ms": parse_created_at(reply.get("createdAt")),
            }
            for reply in replies
        ],
    }


def collect_comments(client: QiemanClient, post_id: int) -> list[dict[str, Any]]:
    """抓取一个帖子的全部评论；子评论优先用预加载 children，不足再分页拉取。"""
    comments: list[dict[str, Any]] = []
    for comment in client.fetch_comments(post_id):
        replies: list[dict[str, Any]] = [
            reply for reply in comment.get("children") or [] if isinstance(reply, dict)
        ]
        if _timestamp(comment.get("commentNum")) > len(replies):
            loaded = client.fetch_replies(_timestamp(comment.get("id")))
            if loaded:
                replies = loaded
        comments.append(organise_comment(comment, replies))
    return comments


# ---------------------------------------------------------------------------
# 文件通道
# ---------------------------------------------------------------------------


def file_name(report: dict[str, Any]) -> str:
    return (
        f"{report['post_id']}_{sanitize_text(report['author'], FILE_TITLE_LIMIT)}"
        f"_{sanitize_text(report['title'], FILE_TITLE_LIMIT)}.md"
    )


def doc_title(report: dict[str, Any]) -> str:
    """飞书文档名：{日期时间}-{标题}，确定性函数，供索引自愈匹配。"""
    created = (
        datetime.fromtimestamp(report["created_ms"] / 1000, tz=_timezone())
        if report["created_ms"]
        else None
    )
    stamp = created.strftime("%Y-%m-%d %H.%M") if created else "未知时间"
    return f"{stamp}-{sanitize_text(report['title'], DOC_TITLE_LIMIT)}"


def _indent_continuation(header: str, body: str, indent_size: int) -> str:
    if not body:
        return header
    body_lines = body.split("\n")
    result = [f"{header}{body_lines[0]}"]
    indent = " " * indent_size
    result.extend(indent + line for line in body_lines[1:])
    return "\n".join(result)


def render_markdown(report: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    date_label = _date_label(report["created_ms"])
    label = f"{report['author']} `{date_label}`" if date_label else report["author"]
    lines = [
        "---",
        f"ID: {report['post_id']}",
        f"作者: {report['author']}",
        f"链接: {report['link']}",
        "---",
        "",
        f"## {label}",
    ]
    for paragraph in report["paragraphs"]:
        if paragraph.kind == "image":
            lines.append(f"\n![图片]({paragraph.url})\n")
        else:
            lines.append(paragraph.text)

    if comments:
        lines.append("### 评论")
        for comment in comments:
            header = f"- `{comment['author']}`："
            body = comment["text"] + "".join(f"\n![图片]({url})" for url in comment["images"])
            lines.append(_indent_continuation(header, body, len(header)))
            for reply in comment["replies"]:
                if reply["to_user"] and reply["to_user"] != comment["author"]:
                    reply_header = f"  - `{reply['author']}` > `{reply['to_user']}`："
                else:
                    reply_header = f"  - `{reply['author']}`："
                reply_body = reply["text"] + "".join(
                    f"\n![图片]({url})" for url in reply["images"]
                )
                lines.append(_indent_continuation(reply_header, reply_body, len(reply_header)))
    return "\n".join(lines) + "\n"


class FileSink:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir

    def existing_ids(self) -> set[int]:
        ids: set[int] = set()
        if not os.path.isdir(self.output_dir):
            return ids
        for filename in os.listdir(self.output_dir):
            match = re.match(r"^(\d+)_.*\.md$", filename)
            if match:
                ids.add(int(match.group(1)))
        return ids

    def save(self, report: dict[str, Any], comments: list[dict[str, Any]]) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, file_name(report))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report, comments))
        return path


# ---------------------------------------------------------------------------
# 飞书文档通道
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeishuDocsConfig:
    app_id: str
    app_secret: str
    table_token: str
    category: str = DEFAULT_FEISHU_CATEGORY
    upload_images: bool = True
    text_limit: int = 9000
    app_id_candidates: tuple[str, ...] = ()
    app_secret_candidates: tuple[str, ...] = ()
    table_token_candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.table_token_candidates:
            object.__setattr__(self, "table_token_candidates", (self.table_token,))

    @classmethod
    def from_env(cls) -> "FeishuDocsConfig | None":
        values = {
            "FEISHU_APP_ID": _env_candidates(os.environ.get("FEISHU_APP_ID", "")),
            "FEISHU_APP_SECRET": _env_candidates(os.environ.get("FEISHU_APP_SECRET", "")),
            "FEISHU_TABLE_TOKEN": _env_candidates(os.environ.get("FEISHU_TABLE_TOKEN", "")),
        }
        configured = [key for key, value in values.items() if value]
        if not configured:
            return None
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("飞书文档归档为可选功能；启用时还需配置 " + "、".join(missing))
        text_limit = _first_candidate(os.environ.get("QIEMAN_FEISHU_TEXT_LIMIT", ""), "9000")
        return cls(
            app_id=values["FEISHU_APP_ID"][0],
            app_secret=values["FEISHU_APP_SECRET"][0],
            table_token=values["FEISHU_TABLE_TOKEN"][0],
            category=(
                _first_candidate(
                    os.environ.get("QIEMAN_FEISHU_CATEGORY", ""), DEFAULT_FEISHU_CATEGORY
                )
                or DEFAULT_FEISHU_CATEGORY
            ),
            upload_images=_env_flag(os.environ.get("QIEMAN_FEISHU_UPLOAD_IMAGES", "")),
            text_limit=max(500, int(text_limit) if text_limit.isdigit() else 9000),
            app_id_candidates=values["FEISHU_APP_ID"],
            app_secret_candidates=values["FEISHU_APP_SECRET"],
            table_token_candidates=values["FEISHU_TABLE_TOKEN"],
        )


class FeishuDocsClient:
    """飞书知识库（wiki）文档客户端：目录树以 wiki 节点组织。"""

    RETRYABLE_CODES = {1254002, 1254290, 1254291, 1254607, 1255040}

    def __init__(self, config: FeishuDocsConfig) -> None:
        self.config = config
        self.access_token = ""
        self._root: tuple[str, str] | None = None  # (space_id, root_node_token)

    def _redact(self, message: Any) -> str:
        result = str(message or "")
        for value in sorted(
            set(
                self.config.app_id_candidates
                + self.config.app_secret_candidates
                + self.config.table_token_candidates
            ),
            key=len,
            reverse=True,
        ):
            if value:
                result = result.replace(value, "[REDACTED]")
        return result

    def _tenant_token(self) -> str:
        pairs = [
            (app_id, app_secret)
            for app_id in self.config.app_id_candidates
            for app_secret in self.config.app_secret_candidates
        ][: MAX_ENV_CANDIDATES**2]
        last_error = "未返回访问令牌"
        for app_id, app_secret in pairs:
            response = requests.post(
                f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal/",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=30,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                last_error = "飞书返回了非 JSON 响应"
                continue
            if data.get("code") == 0 and data.get("tenant_access_token"):
                return str(data["tenant_access_token"])
            last_error = self._redact(data.get("msg") or data.get("code"))
        raise RuntimeError(
            f"获取飞书 tenant_access_token 失败：{last_error}；"
            "请确认 FEISHU_APP_ID 与 FEISHU_APP_SECRET 来自同一个飞书自建应用，"
            "且应用具有知识库读写权限"
        )

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(1, 6):
            if not self.access_token:
                self.access_token = self._tenant_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}
            if not files:
                headers["Content-Type"] = "application/json; charset=utf-8"
            response = requests.request(
                method,
                FEISHU_BASE_URL + endpoint,
                headers=headers,
                params=params,
                json=None if files else data,
                files=files,
                timeout=60,
            )
            try:
                result = response.json()
            except requests.exceptions.JSONDecodeError as exc:
                raise RuntimeError(
                    f"飞书接口返回非 JSON 响应（HTTP {response.status_code}）"
                ) from exc
            code = result.get("code")
            if response.ok and code == 0:
                return result
            if response.status_code == 401:
                self.access_token = ""
            if (code in self.RETRYABLE_CODES or response.status_code >= 500) and attempt < 5:
                time.sleep(attempt * 2)
                continue
            message = self._redact(
                result.get("msg") or result.get("message") or response.reason
            )
            raise RuntimeError(
                f"飞书接口失败（HTTP {response.status_code}, code={code}）：{message}"
            )
        raise RuntimeError("飞书接口重试次数已用尽")

    def list_spaces(self) -> list[dict[str, Any]]:
        """列出机器人可见的知识库空间。"""
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token
            result = self.request("GET", "/wiki/v2/spaces", params=params)
            data = result.get("data", {})
            items.extend(
                item for item in data.get("items") or [] if isinstance(item, dict)
            )
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more"):
                return items

    def resolve_root(self) -> tuple[str, str]:
        """解析归档根：依次尝试 wiki 节点 → 空间 ID → 唯一可见空间自动定位。

        返回 (space_id, 根节点token)；根为空间时节点token 为空串。
        候选链兼容白虎 `old&new` 合并的同名变量值。
        """
        if self._root is not None:
            return self._root
        candidates = self.config.table_token_candidates
        for token in candidates:
            try:
                result = self.request(
                    "GET",
                    "/wiki/v2/spaces/get_node",
                    params={"token": token, "obj_type": "wiki"},
                )
            except RuntimeError:
                continue
            node = (result.get("data") or {}).get("node") or {}
            if node.get("space_id") and node.get("node_token"):
                log(
                    "飞书：根节点为知识库节点"
                    f"「{node.get('title') or '未命名'}」"
                    f"（{mask_token(str(node.get('node_token')))}）"
                )
                self._root = (str(node["space_id"]), str(node["node_token"]))
                return self._root
        for token in candidates:
            try:
                self.request("GET", f"/wiki/v2/spaces/{token}")
            except RuntimeError:
                continue
            log("飞书：根节点为知识库空间（目录建在空间顶层）")
            self._root = (token, "")
            return self._root
        # 候选都无法直接定位（例如 qieman-sync 填的多维表格 app_token）：
        # 按机器人可见的知识库空间自动定位。
        spaces = self.list_spaces()
        if len(spaces) == 1:
            space_id = str(spaces[0].get("space_id") or "")
            log(
                "飞书：Token 无法直接定位，已自动选择唯一可见知识库空间"
                f"「{spaces[0].get('name') or space_id}」，归档树建在其顶层"
            )
            self._root = (space_id, "")
            return self._root
        names = "、".join(
            f"「{item.get('name') or item.get('space_id')}」" for item in spaces[:5]
        )
        raise RuntimeError(
            f"FEISHU_TABLE_TOKEN 无法解析为知识库节点或空间"
            f"（已尝试 {len(candidates)} 个候选值）。"
            + (
                f"机器人可见 {len(spaces)} 个知识库空间：{names}；"
                "请把本应用的 FEISHU_TABLE_TOKEN 填为其中一个空间的 ID"
                "或归档根节点的 token（wikcn 开头）"
                if spaces
                else "机器人没有任何可见知识库空间；请把应用机器人添加为"
                "目标知识库的可编辑协作者，或将 FEISHU_TABLE_TOKEN 填为"
                "知识库节点 token（wikcn 开头）或空间 ID"
            )
        )

    def list_children(self, parent_node_token: str = "") -> list[dict[str, Any]]:
        """列出某 wiki 节点（或空间顶层）的子节点。"""
        space_id, _ = self.resolve_root()
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 50}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params
            )
            data = result.get("data", {})
            items.extend(
                item for item in data.get("items") or [] if isinstance(item, dict)
            )
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more"):
                return items

    def create_node(
        self, parent_node_token: str, title: str, obj_type: str = "docx"
    ) -> dict[str, str]:
        """在 wiki 中创建节点，返回 {node_token, obj_token}。"""
        space_id, _ = self.resolve_root()
        data: dict[str, Any] = {"obj_type": obj_type, "title": title}
        if parent_node_token:
            data["parent_node_token"] = parent_node_token
        result = self.request("POST", f"/wiki/v2/spaces/{space_id}/nodes", data=data)
        node = (result.get("data") or {}).get("node") or {}
        node_token = str(node.get("node_token") or "")
        if not node_token:
            raise RuntimeError(f"创建知识库节点「{title}」后未返回 node_token")
        return {"node_token": node_token, "obj_token": str(node.get("obj_token") or "")}

    def ensure_node(self, parent_node_token: str, name: str) -> str:
        for item in self.list_children(parent_node_token):
            if str(item.get("title")) == name:
                return str(item.get("node_token"))
        created = self.create_node(parent_node_token, name)
        return created["node_token"]

    def upload_image(self, document_id: str, url: str) -> str:
        image = requests.get(url, timeout=30)
        image.raise_for_status()
        payload = image.content
        filename = re.sub(r"[^A-Za-z0-9._-]", "", url.rsplit("/", 1)[-1]) or "image"
        result = self.request(
            "POST",
            "/drive/v1/medias/upload_all",
            files={
                "file_name": (None, filename),
                "parent_type": (None, "docx_image"),
                "parent_node": (None, document_id),
                "size": (None, str(len(payload))),
                "file": (filename, payload),
            },
        )
        file_token = (result.get("data") or {}).get("file_token")
        if not file_token:
            raise RuntimeError(f"上传图片 {mask_token(url)} 后未返回 file_token")
        return str(file_token)

    def append_blocks(self, document_id: str, blocks: list[dict[str, Any]]) -> None:
        for chunk in _chunks(blocks, DOC_BLOCK_BATCH):
            self.request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                data={"children": chunk},
            )


# --- docx 块构造 ---


def _text_elements(text: str, link: str | None = None) -> list[dict[str, Any]]:
    run: dict[str, Any] = {"content": text}
    if link:
        run["text_element_style"] = {"link": {"url": quote(link, safe="")}}
    return [{"text_run": run}]


def heading_block(text: str, level: int) -> dict[str, Any]:
    return {
        "block_type": 2 + level,
        f"heading{level}": {"elements": _text_elements(text)},
    }


def text_block(text: str, link: str | None = None) -> dict[str, Any]:
    return {"block_type": 2, "text": {"elements": _text_elements(text, link)}}


def bullet_block(text: str) -> dict[str, Any]:
    return {"block_type": 12, "bullet": {"elements": _text_elements(text)}}


def image_block(file_token: str) -> dict[str, Any]:
    return {"block_type": 27, "image": {"token": file_token}}


class FeishuIndex:
    """单小组的 帖子ID → wiki 节点 本地索引；丢失时可从知识库扫描重建。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.category_node_token = ""
        self.group_node_token = ""
        self.authors: dict[str, str] = {}  # 博主名 → 节点 token
        self.posts: dict[str, dict[str, str]] = {}

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            log("WARN: 飞书索引文件损坏，视为空索引")
            return
        self.category_node_token = str(data.get("category_node_token") or "")
        self.group_node_token = str(data.get("group_node_token") or "")
        self.authors = {
            str(name): str(token)
            for name, token in (data.get("authors") or {}).items()
            if name and token
        }
        self.posts = {
            str(post_id): entry
            for post_id, entry in (data.get("posts") or {}).items()
            if str(post_id).isdigit() and isinstance(entry, dict)
        }

    def save(self) -> None:
        payload = {
            "category_node_token": self.category_node_token,
            "group_node_token": self.group_node_token,
            "authors": self.authors,
            "posts": self.posts,
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def recorded_ids(self) -> set[int]:
        return {int(post_id) for post_id in self.posts}

    def rebuild(
        self,
        client: FeishuDocsClient,
        reports: list[dict[str, Any]],
        group_name: str,
    ) -> None:
        """扫描 且慢小组/{小组名}/{博主}/ 下的节点标题，按确定性命名匹配回填索引。"""
        _, root_node = client.resolve_root()
        category = client.ensure_node(root_node, client.config.category)
        group_node = client.ensure_node(category, group_name)
        self.category_node_token = category
        self.group_node_token = group_node
        authors = {
            str(item.get("title")): str(item.get("node_token"))
            for item in client.list_children(group_node)
        }
        matched: dict[str, dict[str, str]] = {}
        for report in reports:
            author_node = authors.get(report["author"])
            if not author_node:
                continue
            title = doc_title(report)
            for item in client.list_children(author_node):
                if str(item.get("title")) == title:
                    matched[str(report["post_id"])] = {
                        "node_token": str(item.get("node_token")),
                        "doc_id": "",
                        "author": report["author"],
                        "title": title,
                    }
                    break
        self.authors = {
            name: token for name, token in authors.items() if token
        }
        self.posts = matched
        log(f"飞书：索引重建完成（{group_name}），匹配 {len(matched)} 篇已归档文档")


class FeishuSink:
    def __init__(
        self,
        client: FeishuDocsClient,
        index: FeishuIndex,
        group_name: str,
    ) -> None:
        self.client = client
        self.index = index
        self.config = client.config
        self.group_name = group_name

    def _author_node(self, author: str) -> str:
        node = self.index.authors.get(author)
        if node:
            return node
        if not self.index.group_node_token:
            _, root_node = self.client.resolve_root()
            category = self.index.category_node_token or self.client.ensure_node(
                root_node, self.config.category
            )
            self.index.category_node_token = category
            self.index.group_node_token = self.client.ensure_node(
                category, self.group_name
            )
        node = self.client.ensure_node(self.index.group_node_token, author)
        self.index.authors[author] = node
        return node

    def save(self, report: dict[str, Any], comments: list[dict[str, Any]]) -> str:
        """写入一篇帖子 wiki 文档并登记索引；索引条目即“该帖已完成”标记。"""
        author_node = self._author_node(report["author"])
        created = self.client.create_node(author_node, doc_title(report))
        document_id = created["obj_token"]

        label = f"{report['author']} {_date_label(report['created_ms'], with_time=True)}".strip()
        blocks: list[dict[str, Any]] = [
            heading_block(label, 2),
            text_block("原文链接", report["link"]),
        ]
        for paragraph in report["paragraphs"]:
            if paragraph.kind == "image":
                blocks.append(self._image_or_link(document_id, paragraph.url))
            else:
                blocks.append(text_block(self._clip(paragraph.text)))

        if comments:
            blocks.append(heading_block("评论", 3))
            for comment in comments:
                blocks.append(bullet_block(self._clip(f"{comment['author']}：{comment['text']}")))
                blocks.extend(self._image_items(document_id, comment["images"]))
                for reply in comment["replies"]:
                    if reply["to_user"] and reply["to_user"] != comment["author"]:
                        prefix = f"↳ {reply['author']} > {reply['to_user']}："
                    else:
                        prefix = f"↳ {reply['author']}："
                    blocks.append(bullet_block(self._clip(f"{prefix}{reply['text']}")))
                    blocks.extend(self._image_items(document_id, reply["images"]))

        self.client.append_blocks(document_id, blocks)
        self.index.posts[str(report["post_id"])] = {
            "node_token": created["node_token"],
            "doc_id": document_id,
            "author": report["author"],
            "title": doc_title(report),
        }
        self.index.save()
        return created["node_token"]

    def _image_items(self, document_id: str, urls: list[str]) -> list[dict[str, Any]]:
        return [self._image_or_link(document_id, url) for url in urls]

    def _image_or_link(self, document_id: str, url: str) -> dict[str, Any]:
        if not self.config.upload_images:
            return text_block(f"🖼 {url}")
        try:
            return image_block(self.client.upload_image(document_id, url))
        except Exception as exc:
            log(f"WARN: 图片上传失败，降级为链接：{url[:60]}（{exc}）")
            return text_block(f"🖼 {url}")

    def _clip(self, text: str) -> str:
        limit = self.config.text_limit
        if len(text) <= limit:
            return text
        return text[: limit - 6].rstrip() + "…（已截断）"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="归档且慢小组帖子到文件与飞书文档")
    parser.add_argument(
        "--groups",
        default="",
        help="小组列表，格式 43:长赢同路人,58:其他小组；默认取 QIEMAN_GROUPS 变量",
    )
    parser.add_argument(
        "--posts-dir",
        default=os.environ.get("QIEMAN_POSTS_DIR", ""),
        help="文件通道输出根目录，各小组写入其下 qieman_posts_<小组ID>",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="文件通道全量重写（覆盖已有 md）；飞书文档只增不改，不受影响",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="每个小组本次最多处理 N 篇待归档帖子（最新优先），用于全量回填分批",
    )
    parser.add_argument(
        "--rebuild-feishu-index",
        action="store_true",
        help="启动时从飞书文件夹扫描重建本地索引（自愈/修复用）",
    )
    parser.add_argument("--json", action="store_true", help="输出本次处理帖子的 JSON")
    return parser.parse_args()


def run_group(
    args: argparse.Namespace,
    group_id: int,
    group_name: str,
    base_dir: str,
    feishu_config: FeishuDocsConfig | None,
    counts: dict[str, int],
    archived: list[dict[str, Any]],
) -> bool:
    """归档单个小组；返回是否发生“应写全失败”。"""
    posts_dir = os.path.join(base_dir, f"qieman_posts_{group_id}")
    log(f"开始归档且慢小组 {group_id}（{group_name}）")
    client = QiemanClient()
    try:
        posts = client.fetch_all_posts(group_id)
    except Exception as exc:
        log(f"ERROR: 获取小组 {group_id} 帖子列表失败：{exc}", error=True)
        counts["group_fail"] += 1
        return False
    log(f"帖子列表共 {len(posts)} 篇")
    reports = [organise_post(post) for post in posts if _timestamp(post.get("id"))]

    file_sink = FileSink(posts_dir) if _env_flag(os.environ.get("QIEMAN_SAVE_FILES", "")) else None
    feishu_sink: FeishuSink | None = None
    feishu_index: FeishuIndex | None = None
    if feishu_config is not None:
        index_path = os.path.join(base_dir, f"feishu_index_{group_id}.json")
        feishu_index = FeishuIndex(index_path)
        feishu_index.load()
        if args.rebuild_feishu_index or (
            not feishu_index.posts and not feishu_index.authors
        ):
            try:
                feishu_index.rebuild(FeishuDocsClient(feishu_config), reports, group_name)
                feishu_index.save()
            except Exception as exc:
                log(f"WARN: 飞书索引重建失败，按空索引继续：{exc}", error=True)
                feishu_index = FeishuIndex(index_path)
        feishu_sink = FeishuSink(FeishuDocsClient(feishu_config), feishu_index, group_name)

    file_existing = file_sink.existing_ids() if file_sink else set()
    feishu_existing = feishu_index.recorded_ids() if feishu_index else set()

    # 各通道独立判需：任一通道缺失该帖即需处理；通道内部再决定是否写自己。
    pending = []
    for report in reports:
        file_needs = bool(file_sink) and (
            args.full or report["post_id"] not in file_existing
        )
        feishu_needs = bool(feishu_sink) and report["post_id"] not in feishu_existing
        if file_needs or feishu_needs:
            pending.append((report, file_needs, feishu_needs))
    if args.limit > 0:
        pending = pending[: args.limit]
    log(f"待归档 {len(pending)} 篇（列表总数 {len(reports)}）")

    file_ok = file_fail = feishu_ok = feishu_fail = 0
    for position, (report, file_needs, feishu_needs) in enumerate(pending, 1):
        log(f"[{position}/{len(pending)}] 归档帖子 {report['post_id']}（{report['author']}）")
        archived.append({"group": group_name, "report": report})
        comments: list[dict[str, Any]] = []
        if report["comment_num"] > 0:
            try:
                comments = collect_comments(client, report["post_id"])
            except Exception as exc:
                log(
                    f"WARN: 帖子 {report['post_id']} 评论获取失败，按无评论归档：{exc}",
                    error=True,
                )

        if file_needs:
            try:
                file_sink.save(report, comments)
                file_ok += 1
            except Exception as exc:
                file_fail += 1
                log(f"ERROR: 文件通道写入 {report['post_id']} 失败：{exc}", error=True)
        if feishu_needs:
            try:
                feishu_sink.save(report, comments)
                feishu_ok += 1
            except Exception as exc:
                feishu_fail += 1
                log(f"ERROR: 飞书通道写入 {report['post_id']} 失败：{exc}", error=True)
        if args.json:
            print(
                json.dumps(
                    {"group": group_name, **report, "comments": comments},
                    ensure_ascii=False,
                    default=str,
                )
            )
        time.sleep(POST_SLEEP)

    counts["file_ok"] += file_ok
    counts["file_fail"] += file_fail
    counts["feishu_ok"] += feishu_ok
    counts["feishu_fail"] += feishu_fail
    part = [f"{group_name}：待归档 {len(pending)}"]
    if file_sink:
        part.append(f"文件新增 {file_ok}、失败 {file_fail}")
    if feishu_sink:
        part.append(f"飞书新增 {feishu_ok}、失败 {feishu_fail}")
    log("小组结果：" + "；".join(part))

    total_expected = sum(
        int(file_needs) + int(feishu_needs) for _, file_needs, feishu_needs in pending
    )
    return bool(pending) and total_expected > 0 and file_ok + feishu_ok == 0


def main() -> int:
    args = parse_args()
    try:
        groups = groups_from_env_and_args(args.groups)
    except ValueError as exc:
        log(f"ERROR: {exc}", error=True)
        return 2

    base_dir = (
        args.posts_dir.strip()
        or os.path.dirname(os.path.abspath(__file__))
    )
    save_files = _env_flag(os.environ.get("QIEMAN_SAVE_FILES", ""))

    try:
        feishu_config = FeishuDocsConfig.from_env()
    except ValueError as exc:
        log(f"ERROR: {exc}", error=True)
        return 2

    if not save_files and feishu_config is None:
        log("ERROR: 文件通道已关闭且未配置飞书，没有任何可用持久化通道", error=True)
        return 2

    counts = {"file_ok": 0, "file_fail": 0, "feishu_ok": 0, "feishu_fail": 0, "group_fail": 0}
    archived: list[dict[str, Any]] = []
    all_failed = True
    for group_id, group_name in groups:
        group_all_failed = run_group(
            args, group_id, group_name, base_dir, feishu_config, counts, archived
        )
        all_failed = all_failed and group_all_failed

    summary_parts = []
    if _env_flag(os.environ.get("QIEMAN_SAVE_FILES", "")):
        summary_parts.append(f"文件：新增 {counts['file_ok']}、失败 {counts['file_fail']}")
    if feishu_config is not None:
        summary_parts.append(f"飞书：新增 {counts['feishu_ok']}、失败 {counts['feishu_fail']}")
    if counts["group_fail"]:
        summary_parts.append(f"小组失败 {counts['group_fail']} 个")
    log("本次结果：" + "；".join(summary_parts))
    log(render_marker(archived, multi_group=len(groups) > 1))

    if counts["group_fail"] == len(groups):
        log("ERROR: 所有小组的帖子列表均获取失败", error=True)
        return 1
    if all_failed and archived:
        log("ERROR: 所有帖子在全部开启通道均写入失败", error=True)
        return 1
    return 0


def render_marker(archived: list[dict[str, Any]], multi_group: bool = False) -> str:
    if not archived:
        return "NOTIFY_POSTS=0 今日无新帖"
    summary = "；".join(
        (f"[{entry['group']}]" if multi_group else "")
        + f"{entry['report']['author']}《{entry['report']['title'][:20]}》"
        for entry in archived[:3]
    )
    if len(archived) > 3:
        summary += f" 等 {len(archived)} 篇"
    marker = f"NOTIFY_POSTS=1 新帖 {len(archived)} 篇：{summary}"
    if len(marker) > MARKER_SUMMARY_LIMIT:
        marker = marker[: MARKER_SUMMARY_LIMIT - 1].rstrip() + "…"
    return marker


if __name__ == "__main__":
    raise SystemExit(main())
