# 蹬蹬堂订阅池每日签到

适配白虎面板声明式应用规范 v1，通过账号密码登录
[蹬蹬堂订阅共享池](https://ai01.teleapi.top/ipcheck/pool) 并完成每日签到，支持：

- 用户名或邮箱 + 密码登录
- 单账号与多账号
- 同一 `requests.Session` 保持登录 Cookie
- 今日已签到幂等处理
- Cloudflare 人机验证拦截提示
- 白虎任务失败状态与原生通知

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/ipcheck-pool-checkin/app.yaml
```

默认每天 `08:20` 执行，Cron 为六位表达式 `0 20 8 * * *`，并有最多
5 分钟的随机延迟。

## 单账号

在应用变量中填写：

| 变量 | 类型 | 说明 |
| --- | --- | --- |
| `IPCHECK_POOL_ACCOUNT` | 机密 | 用户名或邮箱 |
| `IPCHECK_POOL_PASSWORD` | 机密 | 登录密码 |

两个变量必须同时填写。

## 多账号

填写机密变量 `IPCHECK_POOL_ACCOUNTS`。配置后优先于单账号变量：

```json
[
  {"label": "账号1", "account": "user1@example.com", "password": "password-1"},
  {"label": "账号2", "account": "user2", "password": "password-2"}
]
```

推荐直接粘贴标准 JSON 数组。脚本同时兼容白虎机密变量常见的转义形式：
字面量 `\n` 换行、外层代码围栏、`IPCHECK_POOL_ACCOUNTS=` 前缀、单/双引号包装、HTML 实体（如 `&quot;`）、
零宽字符和全角括号。密码不会写入脚本、Manifest 或任务日志。

## 通知

脚本会输出每个账号的签到结果，并通过退出码让白虎判断任务是否成功。请在白虎
任务的通知配置中选择渠道，开启成功、失败、超时事件和尾部日志即可。脚本不会
自行发送通知，也不会读取通知 Token。

## 请求流程

1. `POST /ipcheck/api/pool/local_login` 使用账号密码登录。
2. 复用登录后由站点设置的会话 Cookie。
3. `POST /ipcheck/api/pool/checkin` 完成当天签到。

如果站点返回“今日已签到”或等价状态，脚本会按成功处理，避免白虎重试时把幂等
结果误报为失败。

## 安全说明

- `IPCHECK_POOL_ACCOUNT`、`IPCHECK_POOL_PASSWORD` 和
  `IPCHECK_POOL_ACCOUNTS` 应只保存为白虎机密变量。
- 脚本只在内存中使用账号密码和会话 Cookie，不持久化 Cookie。
- 错误日志会移除账号、密码和响应中的敏感内容，最长保留 300 个字符。
- `checkin.log` 位于任务工作目录，只包含非敏感执行结果。

## 常见问题

- HTTP 401：账号或密码错误，或站点登录接口发生变化。
- HTTP 403 且提示 Cloudflare：白虎出口 IP 触发了人机验证，可稍后重试或更换出口。
- 其他签到错误：站点返回的错误会记录到任务日志，便于后续适配。
