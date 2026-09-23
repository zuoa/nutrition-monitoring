# 体育推送配置与数据查看

管理员登录后，在 **系统设置 → 数据同步 → 体育推送 API** 配置：

- `school_ids`：允许推送成绩的外部学校编号列表，界面中每行一个编号。留空允许所有签名有效的学校。该编号不是本系统组织表的主键；编号本身可含逗号等任意字符，接口以字符串数组传递。
- `check_string`：与推送方约定的签名密钥。首次接入需填写；配置后留空保存表示保留原值，保存时首尾空格自动去除。需要停用推送时点击「清除签名密钥」：清除后回退到环境变量配置，环境变量为空时推送接口返回 503（与未配置时一致）。

配置保存至现有 `LOCAL_RUNTIME_CONFIG_PATH` 运行配置文件，优先于 `SPORTS_PUSH_ALLOWED_SCHOOL_IDS` 和 `SPORTS_PUSH_CHECK_STRING` 环境变量，每次推送读取最新配置，无需重启。多实例部署需要共享该配置文件，与系统其他运行配置保持一致；写入采用文件锁与原子替换，并发保存不会互相丢失配置。无需新增数据库迁移。`SPORTS_PUSH_ALLOWED_SCHOOL_IDS` 在运行配置文件中始终以字符串数组存储；兼容手工编辑的逗号分隔或 JSON 数组字符串，读取时统一规范化，不会按子串匹配。

在侧栏 **体育数据**（`/sports`）查看成绩，也可通过数据同步中的“查看体育数据”进入。支持姓名/学号、学校编号、运动项目、练习/测试模式和日期筛选，日期边界使用服务端 `APP_TIMEZONE`（默认 Asia/Shanghai，配置无效时回退 Asia/Shanghai）。每页 20 条，运动时间倒序，页码保留在 URL 中。项目与产品名称由服务端统一提供（`GET /api/v1/sports/meta`），未知项目显示为「项目 N」；展开详情时按需加载单条记录的完整推送指标（`raw_result`），列表接口不返回该字段。未识别人员、未建档学号和未知项目均保留显示。

管理接口均要求管理员登录：

- `GET /api/v1/sports/config`：返回 `school_ids`（字符串数组）和 `check_string_configured`，不回传密钥。
- `PUT /api/v1/sports/config`：更新 `school_ids`（字符串数组）、`check_string`（字符串）；省略字段保留原值，`check_string` 传空字符串表示清除该覆盖、回退环境变量。
- `GET /api/v1/sports/meta`：项目、产品、模式的编号—名称映射。
- `GET /api/v1/sports/records`：分页查询，参数为 `student`、`school_id`、`sport_type`、`mode`、`date_from`、`date_to`、`page`、`page_size`；不返回 `raw_result`。
- `GET /api/v1/sports/records/<id>`：单条记录详情，含 `raw_result` 完整推送指标。

第三方成绩及文件推送路径、签名算法和协议不变，参见 [体育接入协议](external-sports-api.md)。
