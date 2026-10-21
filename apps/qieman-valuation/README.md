# 且慢每日估值

白虎面板应用：获取且慢公开页面的指数估值和债券收益，整理为低估/适中/高估
分布、估值榜单和债券收益摘要，然后通过白虎原生通知推送。飞书多维表格为可选
输出，不配置飞书也能正常运行。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-valuation/app.yaml
```

默认在工作日 `20:45` 执行，最多随机延迟 5 分钟。

## 通知配置

- `QIEMAN_VALUATION_LIMIT`：低估、高估榜单各显示多少个指数，默认 `5`。
- `QIEMAN_NOTIFY_MAX_LENGTH`：通知最大字符数，默认 `3500`。

指数通知同时显示 PE、PE 历史百分位、PB 和 PB 历史百分位。债券收益显示 YTM、
历史百分位和估值状态。百分位越接近 0%，表示指标越靠近历史低位。

## 可选飞书同步

同时配置以下三个变量才会启用飞书写入；全部留空时自动跳过：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`（机密变量）
- `FEISHU_TABLE_TOKEN`（机密变量）

飞书应用需要多维表格读写权限，并需要被添加为目标多维表格的协作者。脚本会创建
或复用“每日指数估值”和“每日债券收益”两张表，自动补齐字段与字段说明，并分别按
指数代码、债券代码批量新增或更新。

如果填写的是 Wiki 节点 Token，再设置 `FEISHU_TOKEN_TYPE=wiki`；普通多维表格
保持默认值 `bitable`。

## 本地检查

```bash
python apps/qieman-valuation/main/sync_qieman_valuation.py --no-notify --no-feishu
```

加 `--json` 可额外输出完整的整理后数据。且慢接口属于公开页面接口，上游接口结构
或访问策略变化时，任务会以非零状态退出并记录错误。
