# 且慢顾投计划同步

白虎面板应用：读取且慢公开页面接口的顾投计划持仓与调仓记录，整理为摘要后
通过白虎原生通知推送。飞书多维表格是可选输出，不配置飞书也能正常运行。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-sync/app.yaml
```

默认在工作日 `20:30` 执行，最多随机延迟 5 分钟。通知包含资产配置、前五持仓和
最近 3 批调仓记录。

## 计划配置

默认同步长赢指数 150 计划，无需任何必填变量。多个计划用逗号分隔：

```text
LONG_WIN:长赢指数150计划,LONG_WIN_S:长赢指数S计划
```

- `QIEMAN_HISTORY_LIMIT`：通知中保留的最近调仓批次数，默认 `3`。
- `QIEMAN_NOTIFY_MAX_LENGTH`：通知最大字符数，默认 `3500`。

## 白虎通知配置

脚本主动推送需要配置：

- `BHPKG_NOTIFY_TOKEN`：在白虎“消息推送 → 脚本调用说明”中复制。
- `BHPKG_NOTIFY_CHANNEL`：在白虎“消息推送 → 渠道列表”中复制渠道 ID。
- `BHPKG_NOTIFY_URL`：默认 `http://localhost:8052/api/v1/notify/send`，通常无需修改。

任务页面的“通知配置”用于白虎自身的任务事件通知，与脚本主动发送整理后的内容是
两套机制；仅绑定任务通知不会自动注入上述变量。如果日志提示“未配置白虎主动推送
参数”，请在应用环境变量中补齐 Token 和渠道 ID。

## 可选飞书同步

同时配置以下三个变量才会启用飞书写入；全部留空时自动跳过：

- `FEISHU_APP_ID`
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

飞书应用需要多维表格读写权限，并需要被添加为目标多维表格的协作者。脚本会为
每个计划创建“计划名称”和“计划名称-买卖记录”两个数据表，自动补齐字段；持仓按
基金代码更新，历史记录按“调仓 ID + 基金代码”增量写入。

## 本地检查

```bash
python apps/qieman-sync/main/sync_qieman.py --no-notify --no-feishu
```

加 `--json` 可额外输出完整的整理后数据。且慢接口属于公开页面接口，若上游接口
结构或访问策略变化，任务会以非零状态退出并在日志中给出错误。
