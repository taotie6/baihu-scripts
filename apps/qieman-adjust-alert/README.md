# 且慢顾投调仓提醒

白虎面板应用：工作日 `14:40` 调用且慢公开页面的“最新调仓”接口，
当天存在调仓批次时输出 `NOTIFY_UPDATE=1` 标记日志，配合白虎任务通知
的日志变量正则提取实现“有更新才通知”。可选配置 `BHPKG_*` 变量改为
脚本直接调用白虎通知接口推送。

且慢长赢计划的调仓公告通常在交易日午间或午后发布、`15:00` 截止跟车，
`14:40` 检查正好留出操作窗口；任务随机延迟已收敛到 60 秒内。

## 安装

在白虎面板中导入远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-adjust-alert/app.yaml
```

默认在工作日 `14:40` 执行，无需任何必填变量。

## 判定规则

读取每个计划的 `plan/adjustments/latest`：

- `adjustTxnDate`（交易日，北京时间）等于今天 → 当天有更新；
- 同一批次调仓会按 `adjustmentId` 去重（长赢 150 与 S 计划通常同批发车）；
- 通知内容包含摘要（如“买入1份广发A500”）、涉及计划、跟车截止时间
  （窗口内提示 `截止 HH:MM`，过期提示 `已截止`）与公告链接；
- 判定基于交易日而非公告分钟，盘前公告当天执行、隔晚公告次日执行
  两种情况都会命中。

## 白虎通知配置（推荐）

脚本始终以退出码 0 结束正常执行，日志最后一行输出稳定标记：

```text
NOTIFY_UPDATE=1 #841 买入1份广发A500（截止15:00）   ← 有更新
NOTIFY_UPDATE=0 今日无调仓更新                        ← 无更新
```

打开白虎生成的“且慢顾投调仓提醒”任务，在“通知配置”中：

1. 选择已创建的通知渠道；
2. 在日志变量提取中用正则 `NOTIFY_UPDATE=1` 作为发送条件（有更新才通知），
   也可以用 `NOTIFY_UPDATE=\d (.+)` 把摘要填进通知模板；
3. 建议开启失败通知，接口异常时脚本以非零退出，能及时暴露；
4. 开启携带尾部日志，让通知包含调仓明细与公告链接。

若面板的日志变量提取不支持“匹配才发送”的门控，可改用下面的直推模式。

## 可选：脚本直推模式

同时配置以下两个变量后，脚本在检测到当日调仓时直接调用白虎通知接口：

- `BHPKG_NOTIFY_TOKEN`（机密变量）
- `BHPKG_NOTIFY_CHANNEL`

`BHPKG_NOTIFY_URL` 通常保持默认 `http://localhost:8052/api/v1/notify/send`。
只配置其中之一时脚本会告警并跳过直推，不影响任务本身；直推失败也只
记录告警，不会让任务失败。直推与任务通知配置可以并存。

## 计划配置

默认检查长赢指数 150 和长赢指数 S 两个计划，`QIEMAN_PLANS` 是完整覆盖
而不是追加，格式同 `qieman-sync`：

```text
LONG_WIN:长赢指数150计划,LONG_WIN_S:长赢指数S计划
```

## 调试与本地检查

```bash
# 模拟某个发车日（例如 2026-10-22 有 #841 批次）
python apps/qieman-adjust-alert/main/check_adjust.py --date 2026-10-22

# 无视日期，用最新批次强制走有更新分支，验证通知链路
python apps/qieman-adjust-alert/main/check_adjust.py --force-notify

# 额外输出整理后的完整 JSON
python apps/qieman-adjust-alert/main/check_adjust.py --json
```

且慢接口属于公开页面接口，若上游接口结构或访问策略变化，任务会以
非零状态退出并在日志中给出错误。
