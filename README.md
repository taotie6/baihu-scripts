# Baihu Scripts

适用于 [白虎面板](https://github.com/engigu/baihu-panel) 的个人应用仓库。

## 应用列表

| 应用 | 说明 | Manifest |
| --- | --- | --- |
| [iios-checkin](apps/iios-checkin/) | iios.fun 每日签到，支持单账号与多账号 | [app.yaml](apps/iios-checkin/app.yaml) |

## 安装

在白虎面板中选择通过远程 Manifest 安装，填写：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/iios-checkin/app.yaml
```

安装页面会要求填写账号变量。Token、Cookie 和多账号 JSON 请保存为机密，
不要写入脚本或提交到仓库。

## 兼容性

- 白虎面板：声明式应用规范 v1
- Node.js：23.11.1
- Cron：6 位秒级表达式

## 免责声明

仓库仅用于个人学习与自动化管理。使用者应遵守目标站点的服务条款，并自行承担使用风险。
