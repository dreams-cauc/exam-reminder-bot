# 监考提醒系统（ExamMonitoringSystem）

学院情报系内部使用的监考提醒工具。读取教务系统导出的监考安排 `.xlsx`，按考试时间自动向钉钉群推送监考提醒并 `@` 到对应教师，避免教师因遗忘监考造成教学事故。

- **用量规模**：学院系内使用
- **推送渠道**：钉钉群「自定义机器人」Webhook + HMAC-SHA256 加签，纯文本消息
- **提醒时点**：考试**前一天 18:00**、开考**前 2 小时**
- **运行形态**：无常驻 GUI，以命令行方式运行 `monitor.exe`

---

## 1. 目录结构

```
ExamMonitoringSystem/
├── monitor.py              # 打包入口，等价于 src.cli:cmd_main
├── requirements.txt        # 运行依赖（含版本锁定）
├── .env.example            # 钉钉凭证模板
├── user.example.json       # 教师名册导入模板
├── src/                    # 核心包
│   ├── cli/                # 命令行入口（cmd.py：hello / iptjson / iptxlsx / server）
│   ├── core/               # 核心业务逻辑
│   │   ├── importers.py    # 教务 Excel 与 user.json 导入
│   │   ├── scheduler.py    # 提醒时刻计算、按时间段合并成消息、发送队列
│   │   ├── messages.py     # 模板占位符填充与消息拼装
│   │   ├── greeting.py     # 机器人 @所有人 打招呼
│   │   └── service.py      # 常驻轮询主循环
│   ├── db/                 # SQLite 访问层（连接 / 实体 CRUD / 提醒状态机）
│   │   └── sql/schema.sql  # 建表语句（表、约束、索引、视图、触发器）
│   └── utils/              # 基础设施 / 工具
│       ├── config.py       # 路径、时区、.env 凭证、提醒时刻参数
│       ├── parsing.py      # 考试时间/课程/姓名/手机号/角色解析归一化
│       ├── prompt.py       # 全部消息模板
│       └── dingtalk.py     # 加签 + POST 发送
├── scripts/                # 与 CLI 子命令一一对应的独立脚本（开发调试用）
├── docs/                   # 设计文档
└── data/monitor.db         # 运行时生成，SQLite 单文件数据库
```

---

## 2. 整体架构

```
user.json（教师名册）          教务监考表 .xlsx
        │                          │
        ▼                          ▼
 ┌──────────────┐          ┌──────────────┐
 │ importers.py │          │ importers.py │
 │ import_user  │          │ load_excel   │
 └──────┬───────┘          └──────┬───────┘
        │                         │parsing.py 解析
        ▼                         ▼
 ┌──────────────────────────────────────┐
 │  SQLite  data/monitor.db             │
 │  teachers / courses / rooms          │
 │  exam_slots / exams / invigilators   │
 │  reminders（提醒队列 + 状态机）       │
 └───────────────┬──────────────────────┘
                 │ scheduler.queue_reminders()
                 │   按 (exam_date,start,end) 聚合成「时间段」
                 │   每个考场一段 → 合并成 1 条消息
                 ▼
 ┌──────────────────────────────┐
 │ service.run_loop 每 30s 轮询  │
 │ scheduler.process_due()      │
 │   expire / cancel 过期清理    │
 │   claim → send → mark        │
 └───────────────┬──────────────┘
                 ▼
        dingtalk.send_text()
        （加签 POST 到群机器人）
```

**核心设计**：**SQLite 本身就是队列**。不使用 APScheduler 之类的内存调度器，因此进程重启、机器休眠都不会丢失提醒——到点未发的记录仍在 `pending`，下一轮轮询会被捡起。`reminders` 表上的 `UNIQUE(reminder_type, slot_id)` 约束从数据库层面杜绝重复发送。

---

## 3. 数据模型

`data/monitor.db`，共 10 张表（详见 `docs/database.md` 与 `docs/data-dictionary.md`）：

| 表 | 说明 | 关键约束 |
| --- | --- | --- |
| `teachers` | 教师名册：UUID 主键、姓名、钉钉账号、手机号、角色 | 姓名允许重名，按钉钉账号/手机号判重 |
| `courses` | 课程字典：课程号 + 课程名 | `UNIQUE(code, name)` |
| `rooms` | 考场字典：规范化名称 + 校区/楼号/教室 | `UNIQUE(name)` |
| `exam_slots` | 考试时间段：日期 + 起止时间 | `UNIQUE(exam_date, start_time, end_time)` |
| `exams` | 一场考试 = 课程 + 考场 + 时间段 | `UNIQUE(room_id, slot_id)` |
| `exam_invigilators` | 监考安排：座位标签 + 教师（名册外教师 `teacher_id` 为空） | `UNIQUE(exam_id, seat_label)` |
| `reminders` | 提醒队列：一个时间段 + 提醒类型 = 一条待发消息 | `UNIQUE(reminder_type, slot_id)` |
| `reminder_recipients` | 提醒收件人快照 | `UNIQUE(reminder_id, teacher_name)` |
| `import_batches` / `import_errors` | 导入批次统计 / 拒收明细 | — |

所有主键均为 36 位 UUID 字符串（由 Python 应用层生成），满足 1NF～BCNF；建表语句独立维护在 `src/db/sql/schema.sql`，文件末尾另含 7 个视图（`v_*`，供查看数据，不含 UUID/时间戳等系统字段）与 7 个触发器（`trg_*`，自动维护 `updated_at` / `sent_at`）。

**提醒状态机**：

```
              queue_reminders()               发送成功
   (无) ──────────────────────▶ pending ──claim──▶ sending ──▶ sent
                                  │  ▲                          │
                       发送失败   │  └─── 重导入可重置 ──────────┘
                                  ▼
                               failed ──▶ 可重试
                                  │
   考试已结束 / 已进入当天 / 考试记录被删 ──▶ expired / cancelled
```

- `sent` / `sending` 的记录**不会被重导入改写**，保证不重复打扰教师
- `expired`：考试已结束仍未发出的记录，避免事后骚扰
- `cancelled`：孤儿提醒（对应考试已不存在），或考试已进入当天的「前一天提醒」
- `attempts` / `last_error`：失败排查用；`exception_message` 字段保存给管理员的异常通知正文

---

## 4. 关键业务逻辑

### 4.1 Excel 解析（`src/core/importers.py` + `src/utils/parsing.py`）

- **按表头名取列，不依赖列序号**。`COLUMN_ALIASES` 定义表头 → 内部字段映射；自动扫描前 10 行定位表头行（容忍首行是标题行）。
- **必需列**：`课程`、`考试时间`、`考试教室`；其余列（校区、楼号、选课人数、主监考学院等）可选。
- **考试时间格式**：`第8周,周5,(2026-10-23),13:30-15:30`，正则提取周次、星期、日期、起止时间。全角标点会先归一化为半角。
- **一致性校验**：星期与日期不符、结束时间不晚于开始、日期非法，一律抛 `ParseError` 并**记入拒收清单**（不静默丢弃），CLI 以非零退出码返回。
- **教师姓名**：归一化后查 `teachers` 表。命中则 `resolved=1` 参与提醒；未命中（名册外教师）**不提醒、不推送管理员**，只作为「同场监考教师」出现在消息名单里。

### 4.2 消息合并策略（`src/core/scheduler.py`）

这是控制钉钉发送频率的核心：

1. 按 `(exam_date, start_time, end_time)` 聚合成**时间段**（同一时间段可能含多门课、多个考场）
2. 该时间段内**每个考场生成一段正文**，段落合并成**同一条消息**，结尾追加 `warm_tip`
3. 一位教师若同时监考多个考场，每段各自 `@`，`atMobiles` 去重
4. **整条消息没有库内教师时直接跳过**，不产生空消息

### 4.3 两阶段话术

| 类型           | 触发时刻        | 模板键（setting.json）   | 话术                |
| ------------ | ----------- | -------------------- | ----------------- |
| `day_before` | 考试前一天 18:00 | `day_before_template` | 「您明天的监考信息如下」      |
| `t2h`        | 开考前 2 小时    | `t2h_template`        | 「您的监考不足两小时，请按时前往」 |

> 话术模板由用户在设置界面编辑，保存后写入 `setting.json`，运行期以 json 为准，代码不硬编码。

**边界处理**：若导入时考试已进入当天（含已不足两小时），只建 `t2h` 行——到点即发，不会漏掉临近监考，也不会再发误导性的「明天」话术。已存在的 `day_before` 待发记录会被 `cancel_stale_day_before()` 置为 `cancelled`。

### 4.4 `@` 人的实现与降级

钉钉**自定义机器人只能按手机号**（`atMobiles`）或用户 ID 真正高亮 `@`，而 Webhook 机器人拿不到通讯录里的 userId 映射，因此：

- 名册内且**有合法手机号** → 正文写 `@手机号`，同时填入 `atMobiles`，钉钉渲染为可点 `@姓名`
- 名册内但**无手机号** → 正文退化为纯姓名点名，同时记入 `missing` 清单
- `missing` 非空时，为管理员（`role='admin'`）追加一&#x6761;**【提醒异常】**&#x6D88;息，请管理员线下提醒
- 手机号格式非法（非 `1\d{10}`）按「未登记」处理，绝不把脏号塞进 `atMobiles` 导致整条消息发送失败

### 4.5 发送幂等与容错

- `claim_reminder()` 用 `UPDATE ... WHERE status='pending'` 的 `rowcount==1` **原子占用**，多进程/重启均不会重复发送
- 单轮处理异常被捕获并记录，**不拖垮常驻服务**
- 主消息发出后，附属的管理员异常通知即使失败也只写 `last_error`，不回滚主提醒状态

---

## 5. 消息模板

占位符统一用 `{name}` 形式，由 `messages.fill()` 按名替换（不用 `str.format`，教师姓名里的花括号不会破坏模板）。

- 前一天 / 考前 2 小时的**话术模板**（`day_before_template`、`t2h_template`）由用户在设置界面编辑，保存后写入 `setting.json`，运行期从 json 读取，代码不再硬编码。
- 异常通知、打招呼、温馨提示等固定文案仍在 **`src/utils/prompt.py`**。

| 配置项 / 变量                          | 用途          | 占位符                                                       |
| ---------------------------------- | ----------- | --------------------------------------------------------- |
| `day_before_template`（setting.json） | 前一天提醒       | `at_teacher` `course` `time` `location` `proctor_teacher` |
| `t2h_template`（setting.json）        | 考前 2 小时提醒   | 同上                                                        |
| `error_feedback_msg`               | 管理员异常通知     | `err_teacher` `admin_teacher`                             |
| `helloteacher`                     | 机器人打招呼      | `hi_all`                                                  |
| `warm_tip`                         | 每条提醒结尾的温馨提示 | —                                                         |

---

## 6. 安装与配置

### 6.1 依赖

Python ≥ 3.10
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 6.2 钉钉凭证

复制 `.env.example` 为 `.env`，填入钉钉群自定义机器人信息（安全设置选**加签**）：

### 6.3 教师名册 `user.json`

```json
{
  "教师1": { "dingtalkid": "manager0001", "mobile": "13800000001", "role": "admin" },
  "教师2": { "dingtalkid": "teacher0002", "mobile": "13800000002", "role": "teacher" }
}
```

- `mobile` 是 `@` 人的唯一依据，缺失则只能收群消息、无法被 `@`
- `role` 可选 `teacher` / `admin` / `developer`；`admin` 与 `developer` 会收到「提醒异常」通知
- 同一教师按 `dingtalkid` / `mobile` 判重（姓名允许重名），命中即复用主键、不重复入库
- `dingtalkid` / `mobile` 至少填一个

---

## 7. 使用方式

### 命令行

```bash
monitor hello                              # 机器人 @所有人 打招呼
monitor iptjson --json user.json           # 建库并导入教师名册
monitor iptxlsx 监考表.xlsx                 # 导入监考表（默认重建提醒队列）
monitor iptxlsx 监考表.xlsx --no-queue      # 只导数据，不重建队列
monitor server                             # 常驻，每 30s 扫描待发提醒
monitor server --once                      # 只跑一轮后退出
monitor server --dry-run                   # 只打印要发的内容，不真正调钉钉
monitor server --interval 60               # 自定义扫描间隔（秒）
monitor server --skip-queue                # 启动时不重建队列
```

### 对应脚本

| 脚本                                                    | 等价子命令                              |
| ----------------------------------------------------- | ---------------------------------- |
| `python scripts/hello.py`                             | `monitor hello`                    |
| `python scripts/import_json.py --json user.json`      | `monitor iptjson --json user.json` |
| `python scripts/import_excel.py table.xlsx`           | `monitor iptxlsx table.xlsx`       |
| `python scripts/run.py [--once/--dry-run/--interval]` | `monitor server [...]`             |

### 典型使用流程

```bash
# 1. 首次部署：配 .env，导名册
monitor iptjson --json user.json

# 2. 每学期/每次更新监考表：导入并重建队列
monitor iptxlsx 考试监考安排.xlsx
# → 检查输出的「拒收明细」「名册未命中」清单，按需补名册后重导

# 3. 上线前先验证文案
monitor server --once --dry-run

# 4. 常驻运行
monitor server
```

### 自检

```bash
.venv\Scripts\python.exe test\verify.py
```

覆盖时间解析、拒收规则、模板填充、按考场分段、`@` 降级、手机号归一化等边界用例，不联网、不发消息。**需在已安装依赖的虚拟环境中运行**（缺少 `python-dotenv` 时会因 `scheduler` 导入失败而报错）。


---

## 8. 运维要点

| 场景            | 处理                                                              |
| ------------- | --------------------------------------------------------------- |
| 机器休眠/断电错过触发时刻 | 无需处理，恢复后下一轮轮询自动补发（考试未结束时）                                       |
| 考试已结束的积压提醒    | 自动置 `expired`，不会事后骚扰                                            |
| 监考表变更         | 重新 `monitor iptxlsx`；已 `sent` 的提醒不受影响，未发的被更新                    |
| 教师无手机号        | 检查 `monitor iptxlsx` 输出的「名册未命中」与 `load_user_json` 的警告日志         |
| 发送失败排查        | 查日志 `钉钉发送失败：errcode=... errmsg=...`，或直接查 `reminders.last_error` |
| 钉钉限流          | 自定义机器人 20 条/分钟；按时间段合并后日均远低于此                                    |

---

## 9. 已知限制

- 自定义机器人**无法按姓名自动映射 userId**，`@` 依赖手机号；手机号错误或未登记会导致提醒降级为纯文本点名
- 监听对象是固定群：新增教师需先入群，否则消息可见但收不到通知
- 同一时间段的一位教师会在多个考场段落中被重复 `@`（`atMobiles` 已去重，高亮次数可能多于一次）
- 无 Web 界面与鉴权，依赖运行机器的本地环境与 `.env` 保护

---

## 10. 技术栈

| 层次        | 选型                                            |
| --------- | --------------------------------------------- |
| 语言        | Python 3.13                                   |
| Excel 解析  | `pandas` + `openpyxl`                         |
| 状态存储 / 队列 | SQLite（标准库 `sqlite3`）                         |
| 调度        | 自实现轮询（`service.run_loop`，默认 30s）              |
| 推送        | 钉钉自定义机器人 Webhook + HMAC-SHA256 加签（`requests`） |
| 时区        | `zoneinfo`，固定 `Asia/Shanghai`                 |
| 配置        | `python-dotenv`                               |

## 附件

### 监考表实例Excel格式

| 课程代码/课程名称 | 考试时间 | 校区 | 楼号 | 考试教室 | 主监考 | 副监考1 | 副监考2 | 选课人数 | 要求人数 | 学院 | 监考学院 |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| 02401503/概率论与数理统计(I) | 第8周,周1,(2026-10-26),13:30-15:30 | 东丽校区 | 南教3 | 南教3-104 | 教师1 | 教师2 | 教师3 | 36 | 55 | 理学院 | 空中交通管理学院 |
| 02401503/概率论与数理统计(I) | 第8周,周1,(2026-10-26),13:30-15:30 | 东丽校区 | 南教3 | 南教3-112 | 教室4 | 教室5 | 教师6 | 36 | 55 | 理学院 | 空中交通管理学院 |
