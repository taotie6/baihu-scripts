# Trae CN 每日签到

适配白虎面板声明式应用规范 v1，通过官方 HTTP API 完成 Trae CN / TRAE
SOLO CN 每日签到，支持：

- 导入完整 `storage.json`，无需在白虎主机运行客户端
- 单账号与多账号登录态
- iCube 加密登录态解密，运行时不打印 Token
- 已挂载 `storage.json` 或客户端数据目录
- 自动读取设备 ID、白虎任务失败状态及可用时的原生通知

## 安装

在白虎面板中通过远程 Manifest 安装：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/trae-cn-checkin/app.yaml
```

默认每天 `08:30` 执行，Cron 为六位表达式 `0 30 8 * * *`，并有最多
5 分钟的随机延迟。

## 导入登录态

先在 Windows 上打开 Trae CN 或 TRAE SOLO CN 并完成登录。Trae CN 的登录态
通常位于：

```text
%APPDATA%\Trae CN\User\globalStorage\storage.json
```

将完整文件内容粘贴到白虎机密变量 `TRAE_CN_SESSIONS`。多账号时可以使用包装格式：

```json
[
  {
    "label": "账号1",
    "storage": {
      "iCubeAuthInfo://icube.cloudide": "原文件中的完整加密字符串"
    }
  },
  {
    "label": "账号2",
    "storage": {
      "iCubeAuthInfo://icube.cloudide": "原文件中的完整加密字符串"
    }
  }
]
```

单账号无需包装，直接粘贴完整 `storage.json` 即可。登录态过期后，需要重新
启动 Trae 刷新文件并再次导入。

也可在仓库根目录运行 `python tools/export_baihu_credentials.py trae`，自动扫描两种
客户端、校验加密登录态并整理 `TRAE_CN_SESSIONS` 和设备 ID。

## 配置设备 ID

建议将设备 ID 保存到白虎机密变量 `TRAE_CN_DEVICE_ID`。通常可在客户端数据目录的
`ahanet/tt_net_config.config` 中找到 `device_id`，也可在近期 `logs/*/main.log`
中搜索 `[ICDRS]` 和 `did:`。

如果白虎运行环境已经挂载客户端数据，可以将单个 `storage.json`、客户端目录，
或由多个路径组成的 JSON 数组填入 `TRAE_CN_STORAGE_PATHS`。此时脚本会同时尝试
自动读取登录态和设备 ID。

## 安全说明

- `TRAE_CN_SESSIONS` 和 `TRAE_CN_DEVICE_ID` 应保存为白虎机密变量。
- 脚本只在内存中解密 Token，不会修改登录态或设备 ID 文件。
- Token 不会输出到控制台或 `checkin.log`。
- `checkin.log` 位于任务工作目录，白虎任务日志也会保留同样的非敏感输出。
