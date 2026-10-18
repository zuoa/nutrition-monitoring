# 学生营养报告与图片导出

个人报告的网页预览和 PNG 使用同一个后端 HTML/CSS 模板。底图位于
`backend/app/templates/reports/background.svg`，替换此文件即可调整头部和边缘装饰；
模板和底图在进程内缓存，修改后需重启 API 和 Celery worker。

## 使用

- 在“营养报告 → 个人报告”选择学生，点击“下载 PNG”。图片宽 1600px，高度随完整内容变化。
- 切换“周报 / 月报”，选择指定周或月份查看历史报告；点击“最新周报 / 最新月报”返回最新报告。
- 管理员可生成当前层级（个人、班级、年级、校区）的周报或月报，按所选周期批量生成。未选择周期时，生成上一完整周或上一自然月。
- 周报周期为周一至周日，支持 ISO 跨年周；月报覆盖所选自然月，包含闰年二月。
- 点击“批量导出”，选择班级及已有周报或月报周期，提交后查看进度并下载 ZIP。导出类型跟随页面上的周报 / 月报选择。
- 班级导出采用当前在籍、启用的学生名单。每名学生只取所选类型、起止日期完全一致的最新个人报告，不自动生成缺失报告。
- ZIP 内包含每人的 PNG 和 UTF-8 BOM 编码的 `导出清单.csv`；缺失报告或个别渲染失败会列入清单。
- 任务 ID 按当前用户保存在浏览器，刷新后恢复最后一次任务。产物保留 24 小时，过期需重新导出。

## 部署

重新构建后端镜像以安装 `pypdfium2` 和中文字体、Pango 依赖。API、通用 Celery worker
必须挂载同一私有目录；两个 Compose 文件已添加
`${DATA_DIR:-./data}/report_exports:/data/report_exports`。

月报功能新增 `class_monthly` 和 `campus_monthly` 枚举值，已有数据库需先运行 `make migrate`
（生产环境使用 `make prod-migrate`），再重启 API 和 Celery worker。

`REPORT_EXPORT_STORAGE_PATH` 默认 `/data/report_exports`，可覆盖为本地可写目录。
本地运行 API 和 worker 时须配置相同路径；Docker 内修改路径时也要同步调整挂载目标。
此目录不能配置成 Nginx 公开静态目录。Celery beat 每小时清理过期产物，并将超过两小时仍未完成的任务标记失败。
API、worker、beat 都需要运行。单份 PNG 同步返回；班级导出使用 `maintenance` 队列，任务上限为 30 分钟。

## 数据与接口

个人报告读取会补充 `content.meal_statistics`，包含 `meal_count` 和 `breakfast_count`。
最新报告与详情还返回 `content.presentation_html`，供禁用脚本的 iframe 预览；报告列表不携带 HTML。
历史营养分析不被改写，消费次数在读取或导出时补算。

次数仅查询学生已关联的负金额消费，遵循启用的消费地点范围，按应用时区、当前餐段配置归类，
同一天同一餐段去重。早餐使用 `breakfast` 餐段；餐段外消费不计入；不使用餐盘匹配或识别结果。
原始营养日均仍按有效记录日统计；餐次折算另行展示，计算方法见下文。班级任务开始时批量读取统计，导出期间使用该批数据。

## 按餐次折算全天供给

新版生成的个人和群体报告增加 `content.meal_projection`，网页预览与 PNG 使用同一份计算结果。
原始营养日均与全天折算值分别显示。历史报告为生成时的快照，需重新生成报告才能看到此分析。

早餐、午餐、晚餐的默认份额为 **25%、40%、35%**，合计100%。依据
[《学生餐营养指南》WS/T 554—2017 第3.3节](https://www.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/s6211/s6329/s6366/201712/W020171226542698597687.pdf)，
三餐能量和营养素的份额范围分别为25%～30%、35%～40%、30%～35%；所选默认值位于对应范围内。
标准给出的是配餐份额，按此反推全天是本系统采用的估算假设，不是指南规定的实际摄入测量方法。

- 单餐：仅午餐800 kcal，全天折算为 `800 / 0.4 = 2000 kcal`。
- 多餐：早午餐合计除以0.65，午晚餐合计除以0.75，三餐齐全除以1。
- 多天：每项分别按 `有效餐次营养量总和 / 对应份额总和` 计算，不先平均原始日均再除以统一份额。
- 群体：先计算每名学生的周期折算量，再按学生等权汇总；样本人数按营养素单独统计。
- 份额分母只使用该营养素数据完整的餐次；仅消费记录、识别缺失和餐段未归类不增加分母。
  同一餐有多个餐盘时，先合并所有餐盘；其中缺失的营养素不将部分合计视作完整一餐。
- 每日营养日志在JSON内保存 `_quality_version: 3`、按餐段合并的 `_meals`，不涉及数据库结构变更。
  重新生成时优先从原始匹配与识别记录重建旧日汇总的餐次快照，不改写历史日汇总。
  无原始识别可追溯时，仅接受餐次明确、无未知餐段的v2完整汇总；其他数据不参与折算。

全天折算假设未记录餐次与已记录餐次具有相同营养供给水平。奶类、水果等在不同餐次分布可能不同，
折算值不代表全天实际摄入。报告展示各年龄、性别组的参考比；年龄资料缺失时不自动选择个人目标，
也不恢复旧版综合评分。生成报告后仍沿用现有推送流程。

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/reports/student/<student_id>/latest` | 可传 `type=personal_weekly/personal_monthly`、`period=YYYY-Www/YYYY-MM`；不传周期返回该类型最新报告 |
| `GET /api/v1/reports/<scope>/<scope_id>/latest` | scope 为 class/grade/campus；type 对应 `<scope>_weekly/monthly`，period 同上 |
| `POST /api/v1/reports/generate` | 传入 `type` 和 `period`，或完整周期的 `period_start`、`period_end`；仅管理员可用 |
| `GET /api/v1/reports/<report_id>/export.png` | 下载个人报告 PNG |
| `GET /api/v1/reports/class/<class_id>/export-periods` | type 为 personal_weekly（默认）或 personal_monthly，获取对应个人报告周期 |
| `POST /api/v1/reports/class/<class_id>/exports` | 传入 `type`（默认 personal_weekly）、`period_start`、`period_end`，创建任务，返回 HTTP 202 |
| `GET /api/v1/reports/exports/<task_id>` | 查询状态、进度及下载是否可用 |
| `GET /api/v1/reports/exports/<task_id>/download` | 下载 ZIP，过期返回 HTTP 410 |

个人下载沿用学生报告权限。班级导出仅开放给管理员和管理该班级的教师、年级负责人；
任务状态、下载同时校验申请人和当前班级权限。其他用户即使知道任务 ID 也无法下载。
