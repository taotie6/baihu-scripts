# WorkBuddy 每日积分

适配白虎面板声明式应用规范 v1，通过 WorkBuddy HTTP API 领取每日积分，支持：

- 单账号与多账号登录态
- WorkBuddy 5.6.0+ 的 `$wbEncrypted` AES-256-GCM 字段解密
- 白虎机密变量导入，运行时不打印 Token
- 已挂载 `*.info` 登录态目录
- 白虎任务失败状态及可用时的原生通知

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/workbuddy-checkin/app.yaml
```

默认每天 `08:35` 执行，Cron 为六位表达式 `0 35 8 * * *`，并有最多
5 分钟的随机延迟。

## 导入登录态

先在 Windows 上打开 WorkBuddy 并完成登录。登录态文件位于：

```text
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info
```

将一个 `.info` 文件的完整内容粘贴到白虎机密变量 `WORKBUDDY_SESSIONS`。
多账号时，将多个文件内容组成 JSON 数组：

```json
[
  {
    "label": "账号1",
    "account": {"uid": "..."},
    "auth": {"accessToken": "$wbEncrypted:...", "expiresAt": 0, "domain": "..."}
  },
  {
    "label": "账号2",
    "account": {"uid": "..."},
    "auth": {"accessToken": "$wbEncrypted:...", "expiresAt": 0, "domain": "..."}
  }
]
```

除可选的 `label` 外，请保留 `.info` 中字段的原始值。示例中的空值仅用于展示
结构，不能直接使用。登录态过期后，需要重新启动 WorkBuddy 刷新文件并再次导入。

兼容 5.6.0+ 的字符串式 `$wbEncrypted:<base64>` 信封，也兼容部分构建使用的
对象式 `{"$wbEncrypted": 1, ...}` 信封以及旧版本的明文 accessToken。

也可在仓库根目录运行 `python tools/export_baihu_credentials.py workbuddy`，自动扫描
所有 `.info`、按 Token 去重，并只把签到所需字段写入白虎变量包。

如果白虎运行环境已经挂载了登录态目录，也可以填写普通变量
`WORKBUDDY_AUTH_DIR`；脚本会读取其中所有 `*.info` 文件。变量和目录可以同时使用，
重复 Token 会自动去重。

## 安全说明

- `WORKBUDDY_SESSIONS` 必须保存为白虎的机密变量。
- 脚本只在内存中解密 Token，不会修改登录态文件或变量。
- Token 不会输出到控制台或 `checkin.log`。
- `checkin.log` 位于任务工作目录，白虎任务日志也会保留同样的非敏感输出。
