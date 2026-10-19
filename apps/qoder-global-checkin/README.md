# Qoder 国际版用量与签到

适配白虎面板声明式应用规范 v1，读取 Qoder 国际版的用量、额度、活跃天数和
热力图，并自动领取可领取的活动权益。

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qoder-global-checkin/app.yaml
```

默认每天 `08:35` 执行，Cron 为六位表达式 `0 35 8 * * *`，并有最多
5 分钟的随机延迟。

## 推荐：使用 PAT

1. 登录 `https://qoder.com`，在 Settings → Personal Access Tokens 创建 PAT。
2. 将完整的 `pt-...` 保存到白虎机密变量 `QODER_GLOBAL_PAT`。
3. 脚本每次运行都会用 PAT 换取短时 `jt-...`，PAT 不会直接放进业务接口的
   `Authorization` 请求头。

PAT、job token 和 refresh token 均不会输出到控制台或 `checkin.log`。

## 多账号

将 `QODER_GLOBAL_AUTHS` 保存为白虎机密变量。该变量配置后优先于单账号 PAT：

```json
[
  {"label": "账号1", "pat": "pt-..."},
  {
    "label": "账号2",
    "token": "jt-...",
    "refreshToken": "jrt-...",
    "user": {"name": "...", "id": "..."}
  }
]
```

推荐每个账号都使用 PAT。直接配置 job token 时，遇到 401/403 会在内存中使用
`refreshToken` 刷新一次；刷新结果不会写回白虎变量，因此若 refresh token 发生
轮换，需要手动更新凭证。

## 客户端凭证方式

脚本也保留 Windows DPAPI 客户端登录仓解密。白虎运行在原 Windows 用户环境时，
可以填写 `QODER_GLOBAL_PROFILE`；未填写时会依次检查常见客户端目录名。

需要迁移客户端凭证时，可在原 Windows 用户环境执行：

```powershell
python -m pip install -r main/requirements.txt
python main/checkin.py export-auth
```

脚本会生成权限受限且已被 Git 忽略的 `auths.json`。将内容保存到
`QODER_GLOBAL_AUTHS` 后，应妥善删除本地副本。也可以挂载该文件并设置
`QODER_GLOBAL_AUTH_FILE`。

## 可调参数

- `QODER_GLOBAL_CLIENT_TYPE`：默认 `10`，活动接口依赖此请求头。
- `QODER_GLOBAL_VERSION`：默认 `0.4.2`。
- `QODER_GLOBAL_HEATMAP_DAYS`：热力图统计天数，默认 `30`。

脚本保留 `usage`、`claim`、`credits-summary`、`seat-activity`、
`credits-heatmap`、`campaigns`、`plan`、`quota` 和 `export-auth` 命令行模式。

## 安全说明

- PAT、job token、refresh token 必须保存为白虎机密变量。
- 错误响应和异常消息中可能出现的凭证会被脱敏。
- 命令行 `--pat` 可能出现在进程列表中，白虎任务应使用机密变量。
- 默认不修改客户端文件；仅显式执行 `export-auth` 时写出凭证文件。
