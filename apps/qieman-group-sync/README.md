# 且慢小组帖子归档

白虎面板应用：每天 `21:30` 抓取且慢小组（默认 43 长赢同路人）的帖子和
评论，双通道持久化——本地 Markdown（默认开启）与飞书文档（配置机密变量
后开启），可同时启用。日志末行输出 `NOTIFY_POSTS` 标记，配合白虎任务通知
的正则提取实现“有新帖才通知”。

且慢小组接口为公开接口（X-sign 签名即可），无需任何登录凭据。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-group-sync/app.yaml
```

默认每天 `21:30` 执行（含周末，帖子多发于晚间），最多随机延迟 5 分钟。
无需任何必填变量即可按文件通道运行。

## 小组配置

抓取哪个小组通过变量（或命令行参数）配置：

- `QIEMAN_GROUP_ID`：小组数字 ID，默认 `43`（长赢同路人）。小组页面地址
  `qieman.com/content/group-detail/43` 中的数字即 ID；非数字值会告警并回退默认。
- `QIEMAN_GROUP_NAME`：显示名称，仅用于日志，默认 `长赢同路人`。

文件通道输出目录名为 `qieman_posts_<小组ID>`，切换小组后会自动使用新目录，
互不干扰；`--group-id`、`--group-name` 参数可临时覆盖。

## 持久化通道

### 文件通道（默认开启）

- 输出 `qieman_posts_<小组ID>/{帖子ID}_{作者}_{标题}.md`，含 front matter、
  正文（图片保位）与全部评论/回复。
- 增量：已有帖子 ID 的文件不再重写；旧帖的新评论不回填（与原版脚本一致）。
- 建议将 `QIEMAN_POSTS_DIR` 指向面板宿主机的持久目录；归档丢失后一次
  运行即可自动全量重建。
- `QIEMAN_SAVE_FILES=false` 可关闭文件通道（纯飞书模式）。

### 飞书文档通道（配置机密后开启）

同时配置以下三个变量才会启用；全留空跳过，部分配置会报错拒绝模糊执行：

- `FEISHU_APP_ID`（机密变量）
- `FEISHU_APP_SECRET`（机密变量）
- `FEISHU_FOLDER_TOKEN`（机密变量，归档根目录的文件夹 token，地址栏
  `feishu.cn/drive/folder/fldcn...` 中的 `fldcn...`）

归档目录结构（二级以下自动创建，博主子目录按作者自动生成）：

```text
投资文章/                 ← FEISHU_FOLDER_TOKEN 指向这里
└── 且慢小组/             ← QIEMAN_FEISHU_CATEGORY，可改名
    └── {博主名}/
        └── {日期时间}-{标题}   ← 一帖一文档，如 2026-10-23 14.48-发车说明
```

- 文档内容与 Markdown 文件同源：作者+日期标题、原文链接、正文（图片默认
  真实上传为图片块，`QIEMAN_FEISHU_UPLOAD_IMAGES=false` 降级为链接）、
  评论列表（楼中楼用 `↳ A > B：` 前缀保留层级）。
- 文档创建后只写不改；每帖写完才登记本地索引（`feishu_index.json`），
  中断重跑自动补缺不重复。
- 增量：稳态无新帖时飞书零调用；索引丢失时自动扫描文件夹按文档名
  （确定性命名）重建，也可手动 `--rebuild-feishu-index`。

### 飞书应用前置条件

- 自建应用开通「云文档」相关权限（文档读写、云空间文件、上传多媒体文件）
  并发布版本。
- 把应用机器人添加为归档根文件夹的**可编辑协作者**，否则无法在其下
  建目录和文档。

## 白虎通知配置

任务日志末行稳定输出标记：

```text
NOTIFY_POSTS=1 新帖 2 篇：ETF拯救世界《...》；...   ← 有新帖
NOTIFY_POSTS=0 今日无新帖                            ← 无新帖
```

在任务的“通知配置”中用日志变量正则提取 `NOTIFY_POSTS=1` 作为发送条件；
建议同时开启失败通知（列表抓取失败、全部通道写入失败时任务非零退出）。

## 调试与本地检查

```bash
# 只看流程，处理最新 2 篇待归档帖子，输出到临时目录
python apps/qieman-group-sync/main/fetch_group_posts.py \
  --posts-dir /tmp/qieman_posts_43 --limit 2

# 首次全量回填建议分批
python apps/qieman-group-sync/main/fetch_group_posts.py --limit 50

# 强制从飞书文件夹重建本地索引（修复用）
python apps/qieman-group-sync/main/fetch_group_posts.py --rebuild-feishu-index
```

`--full` 仅对文件通道全量重写；飞书文档只增不改，不受影响。
`--json` 逐帖输出处理数据。若且慢接口开始要求登录（HTTP 401/403），
配置 `QIEMAN_BEARER_TOKEN` 机密变量（浏览器请求头中的 Bearer Token）。
