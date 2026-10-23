# 且慢每日估值

白虎面板应用：获取且慢公开页面的指数估值和债券收益，将低估/适中/高估分布、
估值榜单和债券收益摘要输出到任务日志，由白虎任务通知推送。飞书多维表格为可选输出。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-valuation/app.yaml
```

默认在工作日 `20:45` 执行，最多随机延迟 5 分钟。

## 摘要配置

- `QIEMAN_VALUATION_LIMIT`：低估、高估榜单各显示多少个指数，默认 `5`。

指数摘要同时显示 PE、PE 历史百分位、PB 和 PB 历史百分位。债券收益显示 YTM、
历史百分位和估值状态。百分位越接近 0%，表示指标越靠近历史低位。

## 白虎通知配置

脚本不再调用白虎通知 HTTP API，也不需要 `BHPKG_NOTIFY_*`。打开白虎生成的
“且慢每日估值”任务，在“通知配置”中选择渠道，开启成功、失败、超时通知，
并开启携带尾部日志。这样通知会包含估值与飞书同步摘要。

## 可选飞书同步

同时配置以下三个变量才会启用飞书写入；全部留空时自动跳过：

- `FEISHU_APP_ID`（机密变量）
- `FEISHU_APP_SECRET`（机密变量）
- `FEISHU_TABLE_TOKEN`（机密变量）

### 获取 FEISHU_TABLE_TOKEN

`FEISHU_TABLE_TOKEN` 是多维表格的 `app_token`，可以从浏览器地址栏中取得。

普通多维表格的地址通常类似：

```text
https://example.feishu.cn/base/AbCdEfGh...?table=tbl123...
```

此时配置：

```text
FEISHU_TABLE_TOKEN=AbCdEfGh...
FEISHU_TOKEN_TYPE=bitable
```

`?table=tbl123...` 中的 `tbl123...` 是单个数据表的 `table_id`，不是
`FEISHU_TABLE_TOKEN`，不要填入该变量。

如果多维表格位于知识库中，地址通常类似：

```text
https://example.feishu.cn/wiki/wikcnXXXX...
```

此时配置：

```text
FEISHU_TABLE_TOKEN=wikcnXXXX...
FEISHU_TOKEN_TYPE=wiki
```

脚本会通过 Wiki 节点 Token 自动解析底层多维表格的 `app_token`。

如果报错 `app secret invalid`，说明脚本尚未进入 Wiki Token 解析：请确认
`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 从同一个飞书自建应用复制，Secret 没有填成
Verification Token、Encrypt Key 或其他凭据。该错误与 `FEISHU_TABLE_TOKEN` 无关。

`1.0.3` 起三个凭据均由白虎机密变量注入，应用更新时会覆盖旧值。从旧版更新时，
请在应用配置页重新确认三个值后再应用更新。如存量变量已被合并成 `old&new`，脚本会优先尝试新值
并有界回退，日志不会输出凭据原文。

飞书应用需要多维表格读写权限和知识库节点读取权限（仅 Wiki Token 模式），并需要被添加为目标多维表格的协作者。

### 数据表与更新规则

| 数据表 | 主要字段 | 唯一键 |
| --- | --- | --- |
| `每日指数估值` | 指数代码、指数名称、PE/PB 及其历史百分位和高低值、ROE、估值状态、指数类型、更新时间 | 指数代码 |
| `每日债券收益` | 债券代码、债券名称、到期收益率、百分位、估值状态、债券分类、更新时间 | 债券代码 |

脚本会创建缺失的表和字段，并将 README 中的指标含义写入飞书字段说明。每次同步先读取现有唯一键，再以最多 500 条一批新增或更新；不删除且慢本次响应中没有的旧记录。

### 日志怎么看

- `[1/2]` 表示“每日指数估值”字段检查与数据同步，`[2/2]` 表示“每日债券收益”。
- 每张表分别输出字段新增/说明更新数，以及记录新增/更新数。
- 日常运行通常是“新增 0，更新 32”类似结果，表示已按代码覆盖当日数据。
- Wiki 模式仅输出脱敏 Token 和节点类型，不会输出完整 Token 或 App Secret。

### 常见问题

- `app secret invalid`：检查 App ID 和 App Secret 是否来自同一个飞书自建应用。
- Wiki 节点类型不是 `bitable`：链接未指向多维表格，或应用缺少节点读取权限。
- 多维表格接口报无权限：确认已发布应用权限，并把应用添加为目标 Base 的协作者。
- 部分 `FEISHU_*` 变量存在：补齐三个必需值；如不需要飞书，则将它们全部留空。

## 本地检查

```bash
python apps/qieman-valuation/main/sync_qieman_valuation.py --no-notify --no-feishu
```

其中 `--no-notify` 仅为旧版兼容参数，已不产生效果。加 `--json` 可额外输出完整的整理后数据。且慢接口属于公开页面接口，上游接口结构
或访问策略变化时，任务会以非零状态退出并记录错误。
