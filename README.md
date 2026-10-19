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
| [iios-checkin](apps/iios-checkin/) | iios.fun 每日签到，支持单账号与多账号 | [app.yaml](apps/iios-checkin/app.yaml) |

iios.fun 远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/iios-checkin/app.yaml
```

## 仓库结构

```text
.
├── .github/workflows/build-apps.yml
├── apps/
│   └── iios-checkin/
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
