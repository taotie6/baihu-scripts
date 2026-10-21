# 白虎个人应用市场

[![Build Baihu AppStore](https://github.com/taotie6/baihu-scripts/actions/workflows/build-apps.yml/badge.svg)](https://github.com/taotie6/baihu-scripts/actions/workflows/build-apps.yml)

按照白虎声明式应用规范 v1 组织的个人应用市场，同时保存应用所需的脚本源码。

## 市场地址

- GitHub Pages：`https://taotie6.github.io/baihu-scripts/apps.json`
- GitHub Raw：`https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps.json`

白虎面板目前将内置市场固定为 `engigu/baihu-appstore`。本仓库中的应用请通过
“导入应用”填写对应的远程 Manifest URL 安装。

## 应用列表

| 应用 | 说明 | Manifest |
| --- | --- | --- |
| [aliyundrive-checkin](apps/aliyundrive-checkin/) | **已归档**：领取奖励需要官方客户端 | [app.yaml](apps/aliyundrive-checkin/app.yaml) |
| [iios-checkin](apps/iios-checkin/) | iios.fun 每日签到，支持单账号与多账号 | [app.yaml](apps/iios-checkin/app.yaml) |
| [qieman-sync](apps/qieman-sync/) | 且慢顾投计划数据整理与推送，可选同步飞书 | [app.yaml](apps/qieman-sync/app.yaml) |
| [qieman-valuation](apps/qieman-valuation/) | 且慢指数估值与债券收益整理推送，可选同步飞书 | [app.yaml](apps/qieman-valuation/app.yaml) |
| [qoder-cn-checkin](apps/qoder-cn-checkin/) | Qoder CN 用量汇总与活动权益领取 | [app.yaml](apps/qoder-cn-checkin/app.yaml) |
| [qoder-global-checkin](apps/qoder-global-checkin/) | Qoder 国际版用量汇总与活动权益领取 | [app.yaml](apps/qoder-global-checkin/app.yaml) |
| [trae-cn-checkin](apps/trae-cn-checkin/) | Trae CN / TRAE SOLO CN 每日签到 | [app.yaml](apps/trae-cn-checkin/app.yaml) |
| [workbuddy-checkin](apps/workbuddy-checkin/) | WorkBuddy 每日积分领取，支持加密登录态与多账号 | [app.yaml](apps/workbuddy-checkin/app.yaml) |

iios.fun 远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/iios-checkin/app.yaml
```

阿里云盘远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/aliyundrive-checkin/app.yaml
```

Qoder CN 远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qoder-cn-checkin/app.yaml
```

Qoder 国际版远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qoder-global-checkin/app.yaml
```

WorkBuddy 远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/workbuddy-checkin/app.yaml
```

Trae CN 远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/trae-cn-checkin/app.yaml
```

且慢顾投计划同步远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-sync/app.yaml
```

且慢每日估值远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-valuation/app.yaml
```

## 仓库结构

```text
.
├── .github/workflows/build-apps.yml
├── apps/
│   ├── aliyundrive-checkin/
│   │   ├── app.yaml
│   │   └── main/
│   ├── iios-checkin/
│   │   ├── app.yaml
│   │   └── main/
│   ├── qieman-sync/
│   │   ├── app.yaml
│   │   └── main/
│   ├── qieman-valuation/
│   │   ├── app.yaml
│   │   └── main/
│   ├── qoder-cn-checkin/
│   │   ├── app.yaml
│   │   └── main/
│   ├── qoder-global-checkin/
│   │   ├── app.yaml
│   │   └── main/
│   ├── trae-cn-checkin/
│   │   ├── app.yaml
│   │   └── main/
│   └── workbuddy-checkin/
│       ├── app.yaml
│       └── main/
├── apps.json
├── build-all.py
└── requirements.txt
```

## 本地构建

```bash
python -m pip install -r requirements.txt
python build-all.py
python build-all.py --check
```

`build-all.py` 会扫描 `apps/*/app.yaml`，生成白虎市场使用的 `apps.json`。
生成结果是确定性的：Manifest 不变时重复构建不会修改索引。

## 安全

Token、Cookie 和多账号 JSON 只能保存到白虎变量机密中，不要写入脚本、
Manifest 或提交到仓库。

## 免责声明

仓库仅用于个人学习与自动化管理。使用者应遵守目标站点的服务条款，并自行承担使用风险。
