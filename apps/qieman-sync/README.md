# 且慢顾投计划同步

白虎面板应用：读取且慢公开页面接口的顾投计划持仓与调仓记录，将摘要
输出到任务日志，由白虎任务通知推送。飞书多维表格是可选输出。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-sync/app.yaml
```

默认在工作日 `20:30` 执行，最多随机延迟 5 分钟。日志摘要包含资产配置、前五持仓和
最近 3 批调仓记录。

## 计划配置

默认同步长赢指数 150 和长赢指数 S 两个计划，无需任何必填变量：

```text
LONG_WIN:长赢指数150计划,LONG_WIN_S:长赢指数S计划
```

`QIEMAN_PLANS` 是完整覆盖而不是追加。例如只同步 150 计划：

```text
QIEMAN_PLANS=LONG_WIN:长赢指数150计划
```

- `QIEMAN_HISTORY_LIMIT`：任务日志中保留的最近调仓批次数，默认 `3`。

## 白虎通知配置

脚本不再调用白虎通知 HTTP API，也不需要 `BHPKG_NOTIFY_*`。打开白虎生成的
“且慢顾投计划同步”任务，在“通知配置”中：

1. 选择已创建的通知渠道。
2. 开启成功、失败和超时通知。
3. 开启携带尾部日志，让通知包含持仓、调仓与飞书同步摘要。

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
请在应用配置页重新确认三个值后再应用更新。对于白虎历史上已合并成 `old&new` 的同名值，
脚本会优先尝试新值并有界回退，日志不会输出凭据原文。

飞书应用需要多维表格读写权限和知识库节点读取权限（仅 Wiki Token 模式），并需要被添加为目标多维表格的协作者。

### 数据表与增量规则

每个计划对应两张表，不同计划不会混写：

| 数据表 | 主要字段 | 同步规则 |
| --- | --- | --- |
| `计划名称` | 基金代码、基金名称、资产类别、目标占比、计划份数、单位净值、净值日期、主理人持有单价 | 以“基金代码”为唯一键；已有记录更新，新标的新增 |
| `计划名称-买卖记录` | 调仓 ID、基金代码、基金名称、操作类型、品种、成交净值、净值日期、交易份数、交易日期 | 以“调仓 ID + 基金代码”为复合唯一键；仅新增不存在的历史明细 |

脚本每次会读取全量已有键用于对比，再以最多 500 条一批请求飞书。表和字段只在缺失时创建：建表时一次性带出全部列及字段说明，后续只为缺失的列补建字段。已有字段的说明、类型和显示属性（如数字/日期格式）不会被读取或修改，也不会删除记录。

### 日志怎么看

启用飞书后，日志会按计划分两阶段输出：

- `[1/2]` 显示持仓表的目标数、已有数、新增数和更新数。
- `[2/2]` 显示历史表的已有数、且慢全量数、新增数和已有跳过数。
- Wiki 模式会显示节点类型和脱敏后的 Token，不会输出完整 Token 或 App Secret。

连续运行时，持仓通常显示全部更新，历史则显示“新增 0，跳过 N”，这是正常的幂等结果。

### 常见问题

- `app secret invalid`：App ID 与 App Secret 不匹配，或把 Verification Token / Encrypt Key 当成了 Secret。
- `Wiki 节点不是多维表格`：Wiki Token 指向的不是 Base 节点，或应用无权读取节点。
- 列表、字段或记录接口报权限错误：确认已发布应用权限，并把应用添加为该 Base 的协作者。
- 部分 `FEISHU_*` 变量存在：脚本会拒绝模糊执行并列出缺少项；如不需要飞书，请将它们全部留空。

## 本地检查

```bash
python apps/qieman-sync/main/sync_qieman.py --no-notify --no-feishu
```

其中 `--no-notify` 仅为旧版兼容参数，已不产生效果。加 `--json` 可额外输出完整的整理后数据。且慢接口属于公开页面接口，若上游接口
结构或访问策略变化，任务会以非零状态退出并在日志中给出错误。
