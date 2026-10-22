# Qoder CN 用量与签到

适配白虎面板声明式应用规范 v1，读取 Qoder CN 的用量、额度、活跃天数和
热力图，并自动领取可领取的活动权益。支持单账号、多账号及服务端幂等重放。

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qoder-cn-checkin/app.yaml
```

默认每天 `10:05` 执行，Cron 为六位表达式 `0 5 10 * * *`，并有最多
5 分钟的随机延迟。

## 准备凭证

Qoder 客户端使用 Windows DPAPI 保护 AES 密钥，因此白虎运行在 Linux 或其他
Windows 用户下时，不能直接解密原客户端的 `auth.v1.dat`。需要先在登录 Qoder
的原 Windows 用户环境中导出一次：

```powershell
python -m pip install -r main/requirements.txt
python main/checkin.py export-auth
```

也可在仓库根目录运行 `python tools/export_baihu_credentials.py qoder`，生成统一的
白虎变量包。

脚本会在 `main` 目录生成权限受限的 `auths.json`，只包含 `token`、到期时间和
用户标识，不会导出 `refreshToken`，也不会把 Token 打印到终端。
将该文件的完整内容保存到白虎机密变量 `QODER_CN_AUTHS`，确认面板配置成功后，
妥善删除本地导出文件。登录态失效时需要重新导出。

也可以把已有 `auths.json` 挂载到白虎主机，并将路径填入
`QODER_CN_AUTH_FILE`。如果白虎本身运行在原 Windows 用户环境中，则可设置
`QODER_CN_PROFILE` 指向包含 `Local State` 和 `auth.v1.dat` 的客户端目录，
脚本会直接通过 DPAPI 解密。

## 多账号

`QODER_CN_AUTHS` 可以填写登录态对象数组，也支持带名称的包装格式：

```json
[
  {
    "label": "账号1",
    "auth": {
      "token": "...",
      "expiresAt": "...",
      "user": {"name": "...", "id": "..."}
    }
  }
]
```

凭证必须保存为白虎机密变量，不能提交到仓库。脚本按 Token 指纹自动去重，
并会对错误响应中可能出现的 Token 做脱敏。

## 可调参数

- `QODER_CN_CLIENT_TYPE`：默认 `10`，活动接口依赖此请求头。
- `QODER_CN_VERSION`：默认 `0.3.4`，客户端升级后可在面板中修改。
- `QODER_CN_HEATMAP_DAYS`：热力图统计天数，默认 `30`。

脚本仍保留命令行模式：`usage`、`claim`、`credits-summary`、`seat-activity`、
`credits-heatmap`、`campaigns`、`plan`、`quota` 和 `export-auth`。

## 安全说明

- 不会在控制台或 `checkin.log` 中打印 Token。
- 默认只读取客户端凭证；仅显式执行 `export-auth` 时会写出 `auths.json`。
- `auths.json` 已加入仓库忽略规则，但仍应在导入面板后妥善删除。
