# iios.fun 每日签到

适配白虎面板声明式应用规范 v1，支持：

- 单账号与多账号签到
- WASM 请求签名和响应解密
- 每个账号独立 Token、Cookie、User-Agent
- 白虎原生通知与任务失败状态

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/iios-checkin/app.yaml
```

默认每天 `08:10` 执行，Cron 为六位表达式 `0 10 8 * * *`，并有最多
5 分钟的随机延迟。

## 单账号

填写以下应用变量：

| 变量 | 类型 | 说明 |
| --- | --- | --- |
| `IIOS_TOKEN` | 机密 | `Authorization` 请求头，可带或不带 `Basic ` |
| `IIOS_COOKIE` | 机密 | 可选，浏览器请求中的完整 Cookie |
| `IIOS_USER_AGENT` | 普通变量 | 抓取 Token 时浏览器的完整 User-Agent |

## 多账号

只需填写机密变量 `IIOS_SESSIONS`。配置后它优先于单账号变量：

```json
[
  {
    "label": "账号1",
    "token": "token-1",
    "cookie": "cf_clearance=...",
    "userAgent": "Mozilla/5.0 ..."
  },
  {
    "label": "账号2",
    "token": "token-2",
    "cookie": "cf_clearance=...",
    "userAgent": "Mozilla/5.0 ..."
  }
]
```

如果账号来自不同浏览器或设备，每项必须填写抓取该 Token 时对应的完整
`userAgent`，否则可能出现 HTTP 412。HTTP 403 通常表示 Token/Cookie 已失效，
或请求被 Cloudflare 拦截。

## 获取登录信息

1. 登录 `https://www.iios.fun/`。
2. 打开浏览器开发者工具的 Network 面板。
3. 找到 `/api/` 请求，复制 `authorization`、`cookie` 和 `user-agent`。
4. 将它们只保存到白虎的变量机密中。

`x-signature` 与 `x-timestamp` 无需填写，脚本会在执行时生成。

## 文件

- `main/run.cjs`：任务入口。
- `main/baihu-shim.cjs`：白虎/Node.js 兼容层。
- `main/iios-original.js`：签到逻辑和 WASM 运行时。
