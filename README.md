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
| [qieman-adjust-alert](apps/qieman-adjust-alert/) | 工作日 14:40 检查且慢顾投计划当日调仓，有更新才通知 | [app.yaml](apps/qieman-adjust-alert/app.yaml) |
| [qieman-group-sync](apps/qieman-group-sync/) | 且慢小组帖子归档：本地 Markdown 与飞书文档双通道 | [app.yaml](apps/qieman-group-sync/app.yaml) |
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

且慢调仓提醒远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-adjust-alert/app.yaml
```

且慢小组帖子归档远程 Manifest：

```text
https://raw.githubusercontent.com/taotie6/baihu-scripts/main/apps/qieman-group-sync/app.yaml
```

## 且慢应用的白虎通知与机密变量

`qieman-sync` 和 `qieman-valuation` 使用白虎任务自带的“通知配置”，
不再需要脚本调用 Token、渠道 ID 或通知 URL。安装后请在对应任务中选择通知渠道，
开启成功、失败、超时事件和尾部日志。

`qieman-adjust-alert` 工作日 14:40 运行，日志输出 `NOTIFY_UPDATE=1/0` 标记行，
在任务通知配置中用日志变量正则提取（如 `NOTIFY_UPDATE=1`）即可实现“当天有
调仓才通知”；也可配置 `BHPKG_NOTIFY_TOKEN`、`BHPKG_NOTIFY_CHANNEL` 机密变量
改由脚本直推。

`qieman-group-sync` 每天 21:30 归档且慢小组（`QIEMAN_GROUPS` 可配多个）帖子：
文件通道默认开启，配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
`FEISHU_TABLE_TOKEN` 后按
“投资文章/且慢小组/{小组名}/{博主名}/{日期时间}-{标题}”同步归档为飞书
知识库文档，双通道可同时开启；日志输出 `NOTIFY_POSTS=1/0` 标记行供通知
正则门控。`FEISHU_TABLE_TOKEN` 与 qieman-sync 同名，可直接复用其值：
解析顺序为 wiki 节点 token → 知识库空间 ID → 自动选择机器人唯一可见的
知识库空间（多个可见空间时报错并列出）。

飞书同步的 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `FEISHU_TABLE_TOKEN` 都是
白虎机密变量。从 `1.0.2` 更新到 `1.0.3` 时，应在应用配置页重新确认三个值，
以覆盖白虎中可能保留的旧凭据。

## Windows 登录态一键整理

Qoder CN、Trae CN / TRAE SOLO CN 和 WorkBuddy 均可从本机客户端的登录态生成
白虎变量包。请在已登录这些客户端的同一个 Windows 用户会话中运行：

```powershell
python -m pip install -r tools/requirements.txt
python tools/export_baihu_credentials.py
```

脚本只读客户端文件，输出 `baihu-credentials.json`，不会在终端打印 Token。
将文件中各顶层键对应的值完整复制到白虎面板的同名“机密变量”：

| 客户端 | 白虎机密变量 |
| --- | --- |
| Qoder CN | `QODER_CN_AUTHS` |
| Trae CN / TRAE SOLO CN | `TRAE_CN_SESSIONS`、可选 `TRAE_CN_DEVICE_ID` |
| WorkBuddy | `WORKBUDDY_SESSIONS` |

仅导出指定客户端可使用 `qoder`、`trae`、`workbuddy` 参数，例如
`python tools/export_baihu_credentials.py qoder workbuddy`。导入完成后应妥善删除输出文件；
脚本不会调用 refresh token，也不会改写客户端登录态。

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
│   ├── qieman-adjust-alert/
│   │   ├── app.yaml
│   │   └── main/
│   ├── qieman-group-sync/
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
├── tools/
│   ├── export_baihu_credentials.py
│   └── requirements.txt
├── tests/
│   └── test_credentials.py
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

各应用通过 `sources.path` 仅稀疏检出自己的 `apps/<应用 ID>/main/`。白虎仍会为
每个应用创建独立目录，以保证安装、更新和卸载互不影响，但不会在每个应用目录中检出
完整的市场仓库。构建器会校验这一约束，避免新增应用时遗漏稀疏检出路径。

## 安全

Token、Cookie 和多账号 JSON 只能保存到白虎变量机密中，不要写入脚本、
Manifest 或提交到仓库。

## 免责声明

仓库仅用于个人学习与自动化管理。使用者应遵守目标站点的服务条款，并自行承担使用风险。
