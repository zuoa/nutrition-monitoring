# 学生营养报告与图片导出

个人报告的网页预览和 PNG 使用同一个后端 HTML/CSS 模板。底图位于
`backend/app/templates/reports/background.svg`，替换此文件即可调整头部和边缘装饰；
模板和底图在进程内缓存，修改后需重启 API 和 Celery worker。

## 使用

- 在“营养报告 → 个人报告”选择学生，点击“下载 PNG”。图片宽 1600px，高度随完整内容变化。
- 展开“按班级导出 PNG 压缩包”，选择班级及已有周报周期，提交后查看进度并下载 ZIP。
- 班级导出采用当前在籍、启用的学生名单。每名学生只取所选起止日期完全一致的最新个人周报，不自动生成缺失报告。
- ZIP 内包含每人的 PNG 和 UTF-8 BOM 编码的 `导出清单.csv`；缺失报告或个别渲染失败会列入清单。
- 任务 ID 按当前用户保存在浏览器，刷新后恢复最后一次任务。产物保留 24 小时，过期需重新导出。

## 部署

重新构建后端镜像以安装 `pypdfium2` 和中文字体、Pango 依赖。API、通用 Celery worker
必须挂载同一私有目录；两个 Compose 文件已添加
`${DATA_DIR:-./data}/report_exports:/data/report_exports`，不需要数据库迁移。

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
营养日均和评分分母沿用原规则。班级任务开始时批量读取统计，导出期间使用该批数据。

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/reports/<report_id>/export.png` | 下载个人报告 PNG |
| `GET /api/v1/reports/class/<class_id>/export-periods` | 获取已有个人周报周期 |
| `POST /api/v1/reports/class/<class_id>/exports` | 传入 `period_start`、`period_end`，创建任务，返回 HTTP 202 |
| `GET /api/v1/reports/exports/<task_id>` | 查询状态、进度及下载是否可用 |
| `GET /api/v1/reports/exports/<task_id>/download` | 下载 ZIP，过期返回 HTTP 410 |

个人下载沿用学生报告权限。班级导出仅开放给管理员和管理该班级的教师、年级负责人；
任务状态、下载同时校验申请人和当前班级权限。其他用户即使知道任务 ID 也无法下载。
