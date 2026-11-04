-- =============================================================================
-- 监考提醒系统数据库结构（SQLite 3）
--
-- 约定：
--   1. 所有表主键均为 36 位 UUID 字符串，由 Python 应用层生成后写入。
--   2. 时间采用 Asia/Shanghai 本地墙钟：exam_date 为 'YYYY-MM-DD'，
--      start_time / end_time 为 'HH:MM'，*_at / scheduled_at / sent_at 为
--      'YYYY-MM-DD HH:MM:SS'。触发器统一用 UTC+8（中国无夏令时）折算本地时刻。
--   3. 外键约束由连接层执行 PRAGMA foreign_keys = ON 启用。
--   4. 文件末尾的视图（v_*）面向用户查看，不含 UUID / created_at / updated_at
--      等系统字段；触发器（trg_*）负责维护 updated_at / sent_at 等审计字段。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 教师名册：姓名、钉钉账号、手机号与角色
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teachers (
    id          TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    name        TEXT NOT NULL,
    dingtalk_id TEXT NOT NULL DEFAULT '',
    mobile      TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'teacher' CHECK (role IN ('teacher', 'admin', 'developer')),
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_teachers_name ON teachers (name);
CREATE INDEX IF NOT EXISTS idx_teachers_role ON teachers (role);
-- 教师身份唯一：非空的钉钉账号 / 手机号不得重复（姓名允许重名，不作唯一键）
CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_dingtalk ON teachers (dingtalk_id) WHERE dingtalk_id <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_mobile ON teachers (mobile) WHERE mobile <> '';

-- -----------------------------------------------------------------------------
-- 2. 课程字典：课程号 + 课程名（课程号可能为空）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    id         TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    code       TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (code, name)
);

-- -----------------------------------------------------------------------------
-- 3. 考场（教室）字典：规范化名称 + 来源字段
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rooms (
    id         TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    name       TEXT NOT NULL,               -- 规范化考场名称（如 '南教3-104'）
    campus     TEXT NOT NULL DEFAULT '',    -- 考场校区（来源字段）
    building   TEXT NOT NULL DEFAULT '',    -- 楼号（来源字段）
    room_no    TEXT NOT NULL DEFAULT '',    -- 考试教室（来源字段）
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (name)
);

-- -----------------------------------------------------------------------------
-- 4. 考试时间段：同一日期 + 起止时间作为一个时间段
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam_slots (
    id         TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    exam_date  TEXT NOT NULL CHECK (exam_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    start_time TEXT NOT NULL CHECK (start_time GLOB '[0-9][0-9]:[0-9][0-9]'),
    end_time   TEXT NOT NULL CHECK (end_time GLOB '[0-9][0-9]:[0-9][0-9]'),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (exam_date, start_time, end_time),
    CHECK (end_time > start_time)
);

-- -----------------------------------------------------------------------------
-- 5. 考试安排：一个考场在某个时间段内的一场考试
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exams (
    id                  TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    course_id           TEXT NOT NULL REFERENCES courses (id) ON DELETE RESTRICT,
    room_id             TEXT NOT NULL REFERENCES rooms (id) ON DELETE RESTRICT,
    slot_id             TEXT NOT NULL REFERENCES exam_slots (id) ON DELETE RESTRICT,
    enrolled            INTEGER,
    required            INTEGER,
    chief_college       TEXT NOT NULL DEFAULT '',
    invigilator_college TEXT NOT NULL DEFAULT '',
    source_file         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (room_id, slot_id)
);

CREATE INDEX IF NOT EXISTS idx_exams_course ON exams (course_id);
CREATE INDEX IF NOT EXISTS idx_exams_room ON exams (room_id);
CREATE INDEX IF NOT EXISTS idx_exams_slot ON exams (slot_id);

-- -----------------------------------------------------------------------------
-- 6. 监考安排：一场考试的监考座位与教师
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam_invigilators (
    id           TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    exam_id      TEXT NOT NULL REFERENCES exams (id) ON DELETE CASCADE,
    teacher_id   TEXT REFERENCES teachers (id) ON DELETE SET NULL,  -- NULL = 名册外
    teacher_name TEXT NOT NULL,                                     -- 原始姓名快照
    seat_label   TEXT NOT NULL,                                     -- 主监考 / 副监考1 / 副监考2 ...
    created_at   TEXT NOT NULL,
    UNIQUE (exam_id, seat_label)
);

CREATE INDEX IF NOT EXISTS idx_exam_invigilators_exam ON exam_invigilators (exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_invigilators_teacher ON exam_invigilators (teacher_id);

-- -----------------------------------------------------------------------------
-- 7. 提醒队列：一个时间段 + 提醒类型 = 一条待发消息
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reminders (
    id                TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    reminder_type     TEXT NOT NULL CHECK (reminder_type IN ('day_before', 't2h')),
    slot_id           TEXT NOT NULL REFERENCES exam_slots (id) ON DELETE CASCADE,
    scheduled_at      TEXT NOT NULL,
    message           TEXT NOT NULL,
    exception_message TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'expired', 'cancelled')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT NOT NULL DEFAULT '',
    sent_at           TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (reminder_type, slot_id)
);

CREATE INDEX IF NOT EXISTS idx_reminders_slot ON reminders (slot_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, scheduled_at);

-- -----------------------------------------------------------------------------
-- 8. 提醒收件人：一条提醒所 @ 的教师（含名册外教师，仅用于展示）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reminder_recipients (
    id           TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    reminder_id  TEXT NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
    teacher_id   TEXT REFERENCES teachers (id) ON DELETE SET NULL,
    teacher_name TEXT NOT NULL,
    mobile       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE (reminder_id, teacher_name)
);

CREATE INDEX IF NOT EXISTS idx_reminder_recipients_reminder ON reminder_recipients (reminder_id);
CREATE INDEX IF NOT EXISTS idx_reminder_recipients_teacher ON reminder_recipients (teacher_id);

-- -----------------------------------------------------------------------------
-- 9. 导入批次：一次导入运行的统计
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_batches (
    id            TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    source_type   TEXT NOT NULL CHECK (source_type IN ('excel', 'json')),
    source_file   TEXT NOT NULL DEFAULT '',
    rows_seen     INTEGER NOT NULL DEFAULT 0,
    rows_imported INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- -----------------------------------------------------------------------------
-- 10. 导入拒收明细：批次内被拒收的行及原因
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_errors (
    id         TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36),
    batch_id   TEXT NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    row_no     INTEGER,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_errors_batch ON import_errors (batch_id);

-- =============================================================================
-- 视图
--   用户查看类（v_exam_details / v_invigilator_details）：不暴露 UUID 等系统字段。
--   轮询服务类（v_due_reminders）：保留 reminder_id，供后台 claim 提醒使用。
-- =============================================================================

-- 清理旧版视图（v1/v2 遗留），仅保留下列 3 个视图
DROP VIEW IF EXISTS v_teachers;
DROP VIEW IF EXISTS v_courses;
DROP VIEW IF EXISTS v_rooms;
DROP VIEW IF EXISTS v_exam_slots;
DROP VIEW IF EXISTS v_invigilation_schedule;
DROP VIEW IF EXISTS v_reminder_status;
DROP VIEW IF EXISTS v_import_history;

-- 考试全貌：考试 + 课程 + 考场 + 时间段
DROP VIEW IF EXISTS v_exam_details;
CREATE VIEW v_exam_details AS
SELECT
    c.name      AS course_name,
    r.name      AS room_name,
    s.exam_date,
    s.start_time,
    s.end_time,
    e.enrolled,
    e.required
FROM exams e
JOIN courses c    ON e.course_id = c.id
JOIN rooms r      ON e.room_id = r.id
JOIN exam_slots s ON e.slot_id = s.id;

-- 监考人员全貌：监考安排 + 考试信息 + 教师名册（名册外教师 mobile/dingtalk_id/role 为 NULL）
DROP VIEW IF EXISTS v_invigilator_details;
CREATE VIEW v_invigilator_details AS
SELECT
    c.name        AS course_name,
    r.name        AS room_name,
    s.exam_date,
    s.start_time,
    i.seat_label,
    i.teacher_name,
    t.mobile,
    t.dingtalk_id,
    t.role
FROM exam_invigilators i
JOIN exams e       ON e.id = i.exam_id
JOIN courses c     ON c.id = e.course_id
JOIN rooms r       ON r.id = e.room_id
JOIN exam_slots s  ON s.id = e.slot_id
LEFT JOIN teachers t ON t.id = i.teacher_id;

-- 轮询服务专用：待发提醒 + 收件人快照（一行一个收件人）
DROP VIEW IF EXISTS v_due_reminders;
CREATE VIEW v_due_reminders AS
SELECT
    r.id              AS reminder_id,
    r.reminder_type,
    r.scheduled_at,
    r.message,
    r.exception_message,
    rr.teacher_name,
    rr.mobile
FROM reminders r
JOIN reminder_recipients rr ON rr.reminder_id = r.id
WHERE r.status = 'pending';

-- =============================================================================
-- 触发器：维护 updated_at / sent_at 审计字段，保证不依赖具体业务代码路径
-- =============================================================================

DROP TRIGGER IF EXISTS trg_teachers_updated_at;
CREATE TRIGGER trg_teachers_updated_at
AFTER UPDATE ON teachers
BEGIN
    UPDATE teachers SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_courses_updated_at;
CREATE TRIGGER trg_courses_updated_at
AFTER UPDATE ON courses
BEGIN
    UPDATE courses SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_rooms_updated_at;
CREATE TRIGGER trg_rooms_updated_at
AFTER UPDATE ON rooms
BEGIN
    UPDATE rooms SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_exam_slots_updated_at;
CREATE TRIGGER trg_exam_slots_updated_at
AFTER UPDATE ON exam_slots
BEGIN
    UPDATE exam_slots SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_exams_updated_at;
CREATE TRIGGER trg_exams_updated_at
AFTER UPDATE ON exams
BEGIN
    UPDATE exams SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_reminders_updated_at;
CREATE TRIGGER trg_reminders_updated_at
AFTER UPDATE ON reminders
BEGIN
    UPDATE reminders SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;

-- 提醒状态进入 sent 时补记发送时刻（业务代码未显式填 sent_at 时兜底）
DROP TRIGGER IF EXISTS trg_reminders_sent_at;
CREATE TRIGGER trg_reminders_sent_at
AFTER UPDATE OF status ON reminders
WHEN NEW.status = 'sent' AND NEW.sent_at IS NULL
BEGIN
    UPDATE reminders SET sent_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')
    WHERE id = NEW.id;
END;
