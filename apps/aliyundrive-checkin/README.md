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

### 获取 Refresh Token

1. 在浏览器中打开[阿里云盘官网](https://www.aliyundrive.com/drive)并登录。
2. 按 `F12` 打开开发者工具，然后刷新页面。
3. 进入开发者工具的 Console（控制台），执行：

   ```javascript
   copy(JSON.parse(localStorage.token).refresh_token)
   ```

4. 此时 `refresh_token` 已复制到剪贴板，将它填入白虎的机密变量
   `ALIYUNDRIVE_REFRESH_TOKEN`。

`refresh_token` 等同于账号登录凭据，请勿发送给他人、截图公开或提交到仓库。

## 多账号

将 `ALIYUNDRIVE_ACCOUNTS` 保存为白虎机密变量。配置后优先于单账号变量：

```json
[
  {"label": "账号1", "refresh_token": "..."},
  {"label": "账号2", "refreshToken": "..."}
]
```

请只粘贴 JSON 内容，不要包含 Markdown 代码围栏或
`ALIYUNDRIVE_ACCOUNTS=` 前缀。推荐在白虎变量输入框中使用单行 JSON：

```json
[{"label":"账号1","refreshToken":"..."},{"label":"账号2","refreshToken":"..."}]
```

脚本也会自动清理零宽字符、全角外括号和常见的复制粘贴包装。

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
