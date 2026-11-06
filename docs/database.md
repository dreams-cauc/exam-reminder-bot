# 监考提醒系统 · 数据库设计文档

## 1. 概述

监考提醒系统通过钉钉群自定义机器人，在考试前一天 18:00（可以自定义） 与开考前 2 小时向监考教师推送提醒并 `@` 到人。数据来源为教务系统导出的监考安排 Excel（`.xlsx`）与教师名册 `user.json`，落库后由常驻服务轮询待发提醒。

- **数据库引擎**：SQLite 3（标准库 `sqlite3`）
- **主键约定**：所有表主键均为 **36 位 UUID 字符串**（`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`），由 Python 应用层生成后写入（SQLite 无内置 UUID 函数）
- **时间约定**：统一采用 `Asia/Shanghai` 本地墙钟；`exam_date` 为 `YYYY-MM-DD`，`start_time` / `end_time` 为 `HH:MM`，`*_at` / `scheduled_at` / `sent_at` 为 `YYYY-MM-DD HH:MM:SS`
- **建表脚本**：`src/db/sql/schema.sql`（含表、约束、索引），由连接层按 `PRAGMA user_version` 自动初始化

## 2. 需求分析

### 2.1 数据来源与处理流程

```
user.json（教师名册）              教务监考表 .xlsx
      │                                  │
      ▼                                  ▼
  load_user_json                   import_excel
      │                                  │
      └──────────────┬───────────────────┘
                     ▼
             SQLite（10 张表）
                     │  queue_reminders()
                     ▼
             reminders 提醒队列
                     │  process_due()
                     ▼
            钉钉群机器人（Webhook）
```

### 2.2 核心业务实体

| 实体 | 说明 | 来源 |
| --- | --- | --- |
| 教师（Teacher） | 自然人：姓名、钉钉账号、手机号、角色（teacher/admin） | `user.json` |
| 课程（Course） | 课程号 + 课程名 | Excel「课程」列 |
| 考场（Room） | 物理教室：楼号、教室号，归一化为一个名称 | Excel 楼号/教室列 |
| 考试时间段（ExamSlot） | 日期 + 起止时间，多个考场可共享同一时间段 | Excel「考试时间」列 |
| 考试安排（Exam） | 一个考场在某个时间段内的一场考试（关联课程） | Excel 一行 |
| 监考安排（ExamInvigilator） | 某场考试的监考座位（主监考/副监考1/副监考2…）与教师 | Excel 监考列 |
| 提醒（Reminder） | 一个时间段 + 提醒类型 = 一条待发消息 | 由 `queue_reminders()` 生成 |
| 提醒收件人（ReminderRecipient） | 一条提醒所 @ 的教师快照 | 由 `queue_reminders()` 生成 |
| 导入批次 / 拒收明细 | 每次导入的统计与被拒收行 | 导入时写入 |

## 3. 实体关系

```
teachers 1 ──< exam_invigilators >── 1 exams
                 │ (teacher_id 可空，空=名册外)
courses  1 ──< exams >── 1 exam_slots
rooms    1 ──< exams
exam_slots 1 ──< reminders >──< reminder_recipients >── teachers
import_batches 1 ──< import_errors
```

- `exams(room_id, slot_id)` 唯一：同一考场同一时间段只有一场考试。
- `exam_invigilators(exam_id, seat_label)` 唯一：一场考试的每个座位至多一名教师。
- `reminders(reminder_type, slot_id)` 唯一：同一时间段同一类型只有一条提醒。
- `reminder_recipients(reminder_id, teacher_name)` 唯一：同一提醒内同名教师去重。

## 4. 规范化设计

### 4.1 原设计存在的问题

| 问题 | 违反范式 | 修复方式 |
| --- | --- | --- |
| `exams` 中 `course_code → course_name`，课程名部分/传递依赖 | 2NF / 3NF / BCNF | 抽取 `courses` 表，`exams` 只存 `course_id` |
| `exams` 中 `exam_date → weekday_num`（纯派生） | 2NF | 删除 `weekday_num`，运行时由日期计算 |
| `exams` 中 `room` 列内嵌 `campus`+`building`（如「南教3-104」） | 1NF（原子性） | 抽取 `rooms` 表，以规范化 `name` 为唯一标识 |
| `teachers.name` 作自然主键，同名教师互相覆盖 | 键选型 | 改 UUID 代理主键，`name` 允许重名 |
| `assignments.resolved` 为跨表物化派生字段 | 冗余 | 删除，改为 `teacher_id` 外键，`resolved = teacher_id IS NOT NULL` |
| `reminders.group_key` 复合值（`日期\|起\|止`） | 1NF | 改为 `slot_id` 外键 |
| `reminders.names_json` / `at_ids_json` / `missing_json` JSON 数组 | 1NF | 拆为 `reminder_recipients` 子表 |
| `reminders.course_code` / `course_name` 用「、」拼接 | 1NF | 删除，课程经 `slot_id → exams → courses` 关联 |

### 4.2 各表范式判定（1NF / 2NF / 3NF / BCNF）

**teachers** — 主键 `id`（单属性）。`name`、`dingtalk_id`、`mobile`、`role`、`is_active` 及时间戳均完全函数依赖于 `id`，无部分依赖、无传递依赖。满足 1NF～BCNF。`name` 允许重名，不构成候选键；教师身份以非空的 `dingtalk_id` / `mobile` 为判定键，由部分唯一索引（`UNIQUE ... WHERE col <> ''`）在数据库层保证唯一。

**courses** — 主键 `id`，候选键 `(code, name)`（`UNIQUE`）。属性原子，`created_at`/`updated_at` 依赖主键。满足 1NF～BCNF。

**rooms** — 主键 `id`，候选键 `name`（`UNIQUE`，规范化考场名称）。`building`/`room_no` 为来源字段（原子字符串）。满足 1NF～BCNF。

**exam_slots** — 主键 `id`，候选键 `(exam_date, start_time, end_time)`（`UNIQUE`）。满足 1NF～BCNF；`end_time > start_time` 由 `CHECK` 保证。

**exams** — 主键 `id`，候选键 `(room_id, slot_id)`（`UNIQUE`）。`course_id` 完全函数依赖于 `(room_id, slot_id)`，无部分依赖、无传递依赖（课程自身信息在 `courses` 表中，不在 `exams` 内）。满足 1NF～BCNF。

**exam_invigilators** — 主键 `id`，候选键 `(exam_id, seat_label)`（`UNIQUE`）。`teacher_id` 为外键（空 = 名册外），`teacher_name` 为原始姓名快照。满足 1NF～BCNF。

**reminders** — 主键 `id`，候选键 `(reminder_type, slot_id)`（`UNIQUE`）。`scheduled_at`、`message`、`exception_message`、`status`、`attempts` 等均依赖主键；`message` / `exception_message` 是由主键**派生**的快照（发送内容必须冻结），不反向决定其他属性。满足 1NF～BCNF。

**reminder_recipients** — 主键 `id`，候选键 `(reminder_id, teacher_name)`（`UNIQUE`）。`teacher_id`、`mobile` 由 `(reminder_id, teacher_name)` 共同确定（同一姓名在不同提醒时刻可解析到不同手机号，属提醒时刻快照）。满足 1NF～BCNF。

**import_batches / import_errors** — 简单表，主键 `id`，无部分/传递依赖。满足 1NF～BCNF。

> 说明：本次重设计**删除**了旧表中的 `raw_course`、`raw_time`、`week_num` 等源文本/派生字段。它们是写多读少的源快照，`raw_course → course` 会构成函数依赖从而破坏 3NF/BCNF，故移出核心业务表。若后续需要逐行追溯源字符串，可在独立的「源数据」审计表中重建，而不污染规范化核心。

## 5. 表结构总览

| # | 表 | 用途 | 主键 | 业务唯一键 |
| --- | --- | --- | --- | --- |
| 1 | `teachers` | 教师名册 | `id` | 非空 `dingtalk_id` / `mobile`（部分唯一） |
| 2 | `courses` | 课程字典 | `id` | `(code, name)` |
| 3 | `rooms` | 考场字典 | `id` | `name` |
| 4 | `exam_slots` | 考试时间段 | `id` | `(exam_date, start_time, end_time)` |
| 5 | `exams` | 考试安排（一考场一场） | `id` | `(room_id, slot_id)` |
| 6 | `exam_invigilators` | 监考安排 | `id` | `(exam_id, seat_label)` |
| 7 | `reminders` | 提醒队列 | `id` | `(reminder_type, slot_id)` |
| 8 | `reminder_recipients` | 提醒收件人 | `id` | `(reminder_id, teacher_name)` |
| 9 | `import_batches` | 导入批次统计 | `id` | — |
| 10 | `import_errors` | 导入拒收明细 | `id` | — |

## 6. 关键设计决策

1. **UUID 主键（36 位字符串）**：主键只负责唯一标识，业务唯一键（`UNIQUE` 约束）负责去重。应用层 `get_or_create`：先按业务键查，命中复用，否则生成 UUID 插入。课程按 `(code, name)`、考场按 `name`、时间段按 `(date, start, end)`、考试按 `(room, slot)` 判重；教师姓名可重名，故按钉钉账号、手机号依次匹配判重。
2. **时间分类**：本地日历时刻（`exam_date`/`start_time`/`end_time`）用墙钟字符串；提醒触发时刻（`scheduled_at`）同为墙钟，便于与 `now_str()` 直接做字符串比较。系统只服务单一时区（上海），不引入 UTC 转换的复杂度。
3. **快照 vs 可重算**：`reminders.message` / `exception_message` / `reminder_recipients` 为提醒时刻快照，必须冻结（发送内容与收件人不随后续数据变更漂移）；`resolved` 等可重算字段不落库，运行时由 `teacher_id IS NOT NULL` 派生。
4. **名册外教师**：`exam_invigilators.teacher_id` 与 `reminder_recipients.teacher_id` 允许为空并 `ON DELETE SET NULL`，同时保留 `teacher_name` 原始姓名，保证教师从名册删除后历史监考安排与提醒仍可读。
5. **外键策略**：参考数据（courses/rooms/exam_slots）对 `exams` 用 `ON DELETE RESTRICT`，防止误删级联；`exam_invigilators`/`reminder_recipients` 随父记录 `CASCADE`，教师引用 `SET NULL`。
6. **外键与索引**：`PRAGMA foreign_keys = ON` 在每次连接时开启；SQLite 不自动为外键建索引，已为全部外键列补建索引，并对提醒队列加 `(status, scheduled_at)` 复合索引。
7. **结构版本迁移**：`PRAGMA user_version` 记录结构版本，连接时仅在版本低于当前值才重放 `schema.sql`；检测到旧版库结构时给出明确的重建指引，避免静默损坏。
8. **WAL 模式**：初始化时启用 `journal_mode = WAL`，降低常驻轮询服务读写并发时的锁竞争。

## 7. 视图与触发器

### 7.1 视图

- 用户查看类（`v_exam_details` / `v_invigilator_details`）：不暴露 `id` 等系统字段，供用户直接查看。
- 轮询服务类（`v_due_reminders`）：保留 `提醒ID`，供后台 claim 提醒使用。

| 视图 | 用途 | 关键字段 |
| --- | --- | --- |
| `v_exam_details` | 考试全貌（考试+课程+考场+时间段） | 课程名、考场名、日期起止 |
| `v_invigilator_details` | 监考人员全貌（监考安排+考试+名册） | 课程/考场/时间、座位、教师姓名、手机号、钉钉号、角色 |
| `v_due_reminders` | 轮询服务专用（待发提醒+收件人） | 提醒ID、提醒类型、计划触发时间、消息内容、异常消息、收件人姓名/手机号 |

查询示例：`sqlite3 data/monitor.db "SELECT * FROM v_invigilator_details;"`

### 7.2 触发器

| 触发器 | 时机 | 作用 |
| --- | --- | --- |
| `trg_teachers_updated_at` … `trg_reminders_updated_at`（6 个） | 对应表 `UPDATE` 后 | 自动刷新 `updated_at` 审计时间戳 |
| `trg_reminders_sent_at` | `reminders.status` 更新为 `sent` 且未填 `sent_at` | 兜底补记发送时刻 |

触发器时间统一用 `strftime('now', '+8 hours')` 折算 Asia/Shanghai（中国无夏令时），与 `config.tz()` 一致；`updated_at` 由触发器兜底维护，不依赖某条具体业务代码是否记得赋值。

### 7.3 教师身份唯一（部分唯一索引）

`teachers.dingtalk_id` / `mobile` 在非空时唯一（`UNIQUE ... WHERE col <> ''` 部分唯一索引），配合应用层 `upsert_teacher` 的「按钉钉账号 → 手机号依次匹配」实现同一教师不重复入库；姓名允许重名，不参与判重。

## 8. 建表脚本

- 表、约束、索引、视图、触发器：[src/db/sql/schema.sql](../src/db/sql/schema.sql)
- 数据库访问层（Python）：连接、实体 CRUD、提醒状态机、结构迁移
- 字段级定义见《数据字典》：[docs/data-dictionary.md](./data-dictionary.md)
