# 监考提醒系统 · 数据字典

> 通用约定：
> - 主键均为 `TEXT` 类型，存 36 位 UUID 字符串，`CHECK (length(id) = 36)`。
> - 时间字段均为 `TEXT` 类型，Asia/Shanghai 本地墙钟：`*_at` / `scheduled_at` / `sent_at` 为 `YYYY-MM-DD HH:MM:SS`，`exam_date` 为 `YYYY-MM-DD`，`start_time` / `end_time` 为 `HH:MM`。
> - 「可空」列：`否` 表示 `NOT NULL`；「约束」列仅列主键/外键/唯一/检查等关键约束，`NOT NULL` 不再重复。

## 1. teachers（教师名册）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 教师唯一标识（UUID） |
| name | TEXT | 否 | — | — | 姓名，与 Excel/user.json 对齐，允许重名 |
| dingtalk_id | TEXT | 否 | `''` | — | 钉钉账号 ID（兜底标识） |
| mobile | TEXT | 否 | `''` | — | 手机号，`@` 人的唯一依据 |
| role | TEXT | 否 | `'teacher'` | CHECK in ('teacher','admin','developer') | 角色：admin/developer 接收异常通知 |
| is_active | INTEGER | 否 | 1 | CHECK in (0,1) | 是否在职（0=停用） |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

索引：`idx_teachers_name`(name)、`idx_teachers_role`(role)；部分唯一索引 `uq_teachers_dingtalk`(dingtalk_id 非空时唯一)、`uq_teachers_mobile`(mobile 非空时唯一)，用于教师身份判重。

## 2. courses（课程字典）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 课程唯一标识 |
| code | TEXT | 否 | `''` | — | 课程号（可能为空） |
| name | TEXT | 否 | — | — | 课程名 |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

约束：`UNIQUE (code, name)`。

## 3. rooms（考场字典）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 考场唯一标识 |
| name | TEXT | 否 | — | UNIQUE | 规范化考场名称（如「南教3-104」） |
| building | TEXT | 否 | `''` | — | 楼号（来源字段） |
| room_no | TEXT | 否 | `''` | — | 考试教室（来源字段） |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

## 4. exam_slots（考试时间段）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 时间段唯一标识 |
| exam_date | TEXT | 否 | — | CHECK GLOB 日期 | 考试日期 `YYYY-MM-DD` |
| start_time | TEXT | 否 | — | CHECK GLOB 时间 | 开始时间 `HH:MM` |
| end_time | TEXT | 否 | — | CHECK GLOB 时间 | 结束时间 `HH:MM` |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

约束：`UNIQUE (exam_date, start_time, end_time)`、`CHECK (end_time > start_time)`。

## 5. exams（考试安排）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 考试唯一标识 |
| course_id | TEXT(36) | 否 | — | FK→courses, RESTRICT | 所属课程 |
| room_id | TEXT(36) | 否 | — | FK→rooms, RESTRICT | 所在考场 |
| slot_id | TEXT(36) | 否 | — | FK→exam_slots, RESTRICT | 所属时间段 |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

约束：`UNIQUE (room_id, slot_id)`。
索引：`idx_exams_course`、`idx_exams_room`、`idx_exams_slot`。

## 6. exam_invigilators（监考安排）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 监考安排唯一标识 |
| exam_id | TEXT(36) | 否 | — | FK→exams, CASCADE | 所属考试 |
| teacher_id | TEXT(36) | 是 | NULL | FK→teachers, SET NULL | 教师 id；NULL=名册外 |
| teacher_name | TEXT | 否 | — | — | 原始姓名快照（名册外时唯一标识） |
| seat_label | TEXT | 否 | — | — | 座位标签（主监考/副监考1/副监考2…） |
| created_at | TEXT | 否 | — | — | 创建时间 |

约束：`UNIQUE (exam_id, seat_label)`。
索引：`idx_exam_invigilators_exam`、`idx_exam_invigilators_teacher`。

## 7. reminders（提醒队列）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 提醒唯一标识 |
| reminder_type | TEXT | 否 | — | CHECK in ('day_before','t2h') | 提醒类型 |
| slot_id | TEXT(36) | 否 | — | FK→exam_slots, CASCADE | 所属时间段 |
| scheduled_at | TEXT | 否 | — | — | 计划触发时刻 |
| message | TEXT | 否 | — | — | 待发消息正文（快照） |
| exception_message | TEXT | 否 | `''` | — | 管理员异常通知正文（快照） |
| status | TEXT | 否 | `'pending'` | CHECK in (pending,sending,sent,failed,expired,cancelled) | 状态机 |
| attempts | INTEGER | 否 | 0 | — | 发送尝试次数 |
| last_error | TEXT | 否 | `''` | — | 最近一次错误 |
| sent_at | TEXT | 是 | NULL | — | 发送成功时刻 |
| created_at | TEXT | 否 | — | — | 创建时间 |
| updated_at | TEXT | 否 | — | — | 更新时间 |

约束：`UNIQUE (reminder_type, slot_id)`。
索引：`idx_reminders_slot`、`idx_reminders_due`(status, scheduled_at)。

状态机：`pending → sending → sent`；`pending → failed`（可重试）；考试结束/进入当天/记录删除 → `expired` / `cancelled`。

## 8. reminder_recipients（提醒收件人）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 收件人记录唯一标识 |
| reminder_id | TEXT(36) | 否 | — | FK→reminders, CASCADE | 所属提醒 |
| teacher_id | TEXT(36) | 是 | NULL | FK→teachers, SET NULL | 教师 id；NULL=名册外 |
| teacher_name | TEXT | 否 | — | — | 姓名快照 |
| mobile | TEXT | 否 | `''` | — | 发送时使用的手机号快照（空=未登记） |
| created_at | TEXT | 否 | — | — | 创建时间 |

约束：`UNIQUE (reminder_id, teacher_name)`。
索引：`idx_reminder_recipients_reminder`、`idx_reminder_recipients_teacher`。

## 9. import_batches（导入批次）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 批次唯一标识 |
| source_type | TEXT | 否 | — | CHECK in ('excel','json') | 来源类型 |
| source_file | TEXT | 否 | `''` | — | 来源文件名 |
| rows_seen | INTEGER | 否 | 0 | — | 读取行数 |
| rows_imported | INTEGER | 否 | 0 | — | 成功导入行数 |
| rows_rejected | INTEGER | 否 | 0 | — | 拒收行数 |
| created_at | TEXT | 否 | — | — | 创建时间 |

## 10. import_errors（导入拒收明细）

| 字段名 | 类型 | 可空 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| id | TEXT(36) | 否 | — | PK, CHECK length=36 | 拒收记录唯一标识 |
| batch_id | TEXT(36) | 否 | — | FK→import_batches, CASCADE | 所属批次 |
| row_no | INTEGER | 是 | NULL | — | Excel 行号（1-based，含表头） |
| reason | TEXT | 否 | — | — | 拒收原因 |
| created_at | TEXT | 否 | — | — | 创建时间 |

索引：`idx_import_errors_batch`(batch_id)。

## 11. 视图

用户查看类视图（`v_exam_details` / `v_invigilator_details`）不暴露 UUID 等系统字段；轮询服务类视图（`v_due_reminders`）保留 `reminder_id` 供后台 claim。

### 11.1 v_exam_details（考试全貌）

| 字段 | 来源 |
| --- | --- |
| 课程名 | courses.name |
| 考场名 | rooms.name |
| 考试日期 / 开始时间 / 结束时间 | exam_slots |

### 11.2 v_invigilator_details（监考人员全貌）

| 字段 | 来源 |
| --- | --- |
| 课程名 | courses.name |
| 考场名 | rooms.name |
| 考试日期 / 开始时间 | exam_slots |
| 监考座位 | exam_invigilators.seat_label |
| 教师姓名 | exam_invigilators.teacher_name（原始姓名快照） |
| 手机号 / 钉钉号 / 角色 | teachers（名册外教师为 NULL） |

### 11.3 v_due_reminders（轮询服务专用）

| 字段 | 来源 |
| --- | --- |
| 提醒ID | reminders.id |
| 提醒类型 | reminders.reminder_type |
| 计划触发时间 | reminders.scheduled_at |
| 消息内容 / 异常消息 | reminders |
| 教师姓名 / 手机号 | reminder_recipients |

> `v_due_reminders` 只含 `status='pending'` 的提醒，且一行一个收件人。

## 12. 触发器

| 触发器 | 表 | 时机 | 作用 |
| --- | --- | --- | --- |
| trg_teachers_updated_at | teachers | AFTER UPDATE | 刷新 `updated_at` |
| trg_courses_updated_at | courses | AFTER UPDATE | 刷新 `updated_at` |
| trg_rooms_updated_at | rooms | AFTER UPDATE | 刷新 `updated_at` |
| trg_exam_slots_updated_at | exam_slots | AFTER UPDATE | 刷新 `updated_at` |
| trg_exams_updated_at | exams | AFTER UPDATE | 刷新 `updated_at` |
| trg_reminders_updated_at | reminders | AFTER UPDATE | 刷新 `updated_at` |
| trg_reminders_sent_at | reminders | AFTER UPDATE OF status | `status→sent` 且未填 `sent_at` 时补记发送时刻 |

> 触发器时间统一用 `strftime('now','+8 hours')` 折算 Asia/Shanghai（中国无夏令时），与 `config.tz()` 一致。
