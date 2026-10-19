# 阿里云盘每日签到

适配白虎面板声明式应用规范 v1，使用 refresh token 换取短时 access token，
完成每日签到并领取当天奖励。支持单账号、多账号及原 `config.json` 格式。

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/aliyundrive-checkin/app.yaml
```

默认每天 `08:40` 执行，Cron 为六位表达式 `0 40 8 * * *`，并有最多
5 分钟的随机延迟。

## 单账号

将阿里云盘登录态中的 `refresh_token` 保存到白虎机密变量
`ALIYUNDRIVE_REFRESH_TOKEN`。Token 只能保存在机密变量中，不要写入脚本或提交
到仓库。

## 多账号

将 `ALIYUNDRIVE_ACCOUNTS` 保存为白虎机密变量。配置后优先于单账号变量：

```json
[
  {"label": "账号1", "refresh_token": "..."},
  {"label": "账号2", "refreshToken": "..."}
]
```

也可以把账号 JSON 挂载到白虎主机，并设置 `ALIYUNDRIVE_ACCOUNTS_FILE`。该文件
既可以使用上述数组，也兼容原脚本的配置结构：

```json
{
  "ALIYUN": [
    {"refresh_token": "..."}
  ]
}
```

## 安全说明

- refresh token 和 access token 不会输出到控制台或 `checkin.log`。
- 脚本不会修改白虎变量或账号文件。
- 登录态失效时，需要重新获取 refresh token 并更新机密变量。
