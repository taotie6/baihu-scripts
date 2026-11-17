# 变量与机密审计

本仓库按白虎面板 v1 的变量模型分类：

- **机密（`secret`）**：密码、Token、Cookie、登录态、PAT、账号 JSON，以及能用于关联身份的账号、邮箱和设备 ID。
- **普通变量**：App ID、渠道 ID、URL、文件路径、枚举、数量、布尔开关等无法单独用于认证的配置。
- **控件类型**：数量使用 `number`，开关使用 `boolean`，有限枚举使用 `select`。

进入任务进程后，变量和机密都会成为普通环境变量；机密的价值在于加密存储、列表掩码、二次验证查看和日志脱敏。

## 应用分类

### aliyundrive-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `ALIYUNDRIVE_REFRESH_TOKEN` | 机密 | 可换取访问令牌 |
| `ALIYUNDRIVE_ACCOUNTS` | 机密 | 多账号登录态 |
| `ALIYUNDRIVE_ACCOUNTS_FILE` | 普通 | 账号文件路径 |

### iios-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `IIOS_TOKEN` | 机密 | 登录 Token |
| `IIOS_COOKIE` | 机密 | 登录 Cookie |
| `IIOS_SESSIONS` | 机密 | 多账号登录态 |
| `IIOS_USER_AGENT` | 普通 | 浏览器请求标识 |

### ipcheck-pool-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `IPCHECK_POOL_ACCOUNT` | 机密 | 用户名或邮箱，属于账号标识 |
| `IPCHECK_POOL_PASSWORD` | 机密 | 登录密码 |
| `IPCHECK_POOL_ACCOUNTS` | 机密 | 多账号账号密码 JSON |

### qieman-adjust-alert

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QIEMAN_PLANS` | 普通 | 计划代码和显示名称 |
| `QIEMAN_TIMEZONE` | 普通 | IANA 时区 |
| `BHPKG_NOTIFY_TOKEN` | 机密 | 白虎通知接口 Token |
| `BHPKG_NOTIFY_CHANNEL` | 普通 | 通知渠道 ID，不是凭据 |
| `BHPKG_NOTIFY_URL` | 普通 | 本地通知接口地址 |

### qieman-group-sync

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QIEMAN_GROUPS` | 普通 | 小组 ID 和显示名称 |
| `QIEMAN_TIMEZONE` | 普通 | IANA 时区 |
| `QIEMAN_CONCURRENCY` | 数字 | 并发数量 |
| `QIEMAN_SAVE_FILES` | 布尔 | 文件通道开关 |
| `QIEMAN_POSTS_DIR` | 普通 | 输出路径 |
| `FEISHU_APP_ID` | 普通 | 飞书应用标识，不是密钥 |
| `FEISHU_APP_SECRET` | 机密 | 飞书应用密钥 |
| `FEISHU_DOC_TOKEN` | 机密 | 知识库节点访问 Token |
| `FEISHU_TABLE_TOKEN` | 机密 | 多维表格或 Wiki 访问 Token |
| `QIEMAN_FEISHU_CATEGORY` | 普通 | 归档目录名称 |
| `QIEMAN_FEISHU_TEXT_LIMIT` | 数字 | 文本块长度 |
| `QIEMAN_FEISHU_UPLOAD_IMAGES` | 布尔 | 图片上传开关 |
| `QIEMAN_BEARER_TOKEN` | 机密 | 且慢登录 Token |

### qieman-sync

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QIEMAN_PLANS` | 普通 | 计划代码和显示名称 |
| `QIEMAN_TIMEZONE` | 普通 | IANA 时区 |
| `QIEMAN_HISTORY_LIMIT` | 数字 | 调仓批次数量 |
| `FEISHU_APP_ID` | 普通 | 飞书应用标识，不是密钥 |
| `FEISHU_APP_SECRET` | 机密 | 飞书应用密钥 |
| `FEISHU_TABLE_TOKEN` | 机密 | 多维表格或 Wiki 访问 Token |
| `FEISHU_TOKEN_TYPE` | 枚举 | `bitable` 或 `wiki` |

### qieman-valuation

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QIEMAN_TIMEZONE` | 普通 | IANA 时区 |
| `QIEMAN_VALUATION_LIMIT` | 数字 | 榜单数量 |
| `FEISHU_APP_ID` | 普通 | 飞书应用标识，不是密钥 |
| `FEISHU_APP_SECRET` | 机密 | 飞书应用密钥 |
| `FEISHU_TABLE_TOKEN` | 机密 | 多维表格或 Wiki 访问 Token |
| `FEISHU_TOKEN_TYPE` | 枚举 | `bitable` 或 `wiki` |

### qoder-cn-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QODER_CN_AUTHS` | 机密 | 登录凭证 JSON |
| `QODER_CN_AUTH_FILE` | 普通 | 凭证文件路径 |
| `QODER_CN_PROFILE` | 普通 | 客户端目录路径 |
| `QODER_CN_CLIENT_TYPE` | 普通 | Cosy 请求头标识 |
| `QODER_CN_VERSION` | 普通 | 客户端版本 |
| `QODER_CN_HEATMAP_DAYS` | 数字 | 热力图统计天数 |

### qoder-global-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `QODER_GLOBAL_PAT` | 机密 | Personal Access Token |
| `QODER_GLOBAL_AUTHS` | 机密 | 多账号凭证 JSON |
| `QODER_GLOBAL_AUTH_FILE` | 普通 | 凭证文件路径 |
| `QODER_GLOBAL_PROFILE` | 普通 | 客户端目录路径 |
| `QODER_GLOBAL_CLIENT_TYPE` | 普通 | Cosy 请求头标识 |
| `QODER_GLOBAL_VERSION` | 普通 | 客户端版本 |
| `QODER_GLOBAL_HEATMAP_DAYS` | 数字 | 热力图统计天数 |

### trae-cn-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `TRAE_CN_SESSIONS` | 机密 | 加密登录态 |
| `TRAE_CN_DEVICE_ID` | 机密 | 与登录态配合的设备标识，按隐私数据处理 |
| `TRAE_CN_STORAGE_PATHS` | 普通 | 登录态文件或目录路径 |

### workbuddy-checkin

| 变量 | 类型 | 原因 |
| --- | --- | --- |
| `WORKBUDDY_SESSIONS` | 机密 | 加密登录态 |
| `WORKBUDDY_AUTH_DIR` | 普通 | 登录态目录路径 |

## 迁移注意

- 已安装应用不会因为 Manifest 类型调整而必然修改已有记录；需要时应在变量机密页面手动更新类型。
- 普通变量和机密不能使用同一个名称。白虎会把同名值用 `&` 拼接，可能导致 JSON、URL 或命令行参数损坏。
- 新增或修改凭证时，优先编辑已有记录，不要在另一页签重复创建同名项。
- 不应把 Token、Cookie、密码或账号 JSON 放入普通变量，即使开启了“隐藏变量值”；隐藏只影响展示，不提供加密保护。
