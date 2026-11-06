# 监考提醒系统（ExamMonitoringSystem）

学院情报系内部使用的监考提醒工具。读取教务系统导出的监考安排 `.xlsx`，按考试时间自动向钉钉群推送监考提醒并 `@` 到对应教师，避免教师因遗忘监考造成教学事故。

- **运行形态**：PyQt5 桌面应用（应用名 `ExamMonitor`，窗口标题「监考智能提醒系统」），内置常驻提醒服务线程；同时保留命令行入口，便于调试与自动化
- **推送渠道**：钉钉群「自定义机器人」Webhook + HMAC-SHA256 加签，纯文本消息
- **提醒时点**：考试**前一天**（默认 18:00，可在界面自定义）、开考**前 2 小时**

---

## 1. 目录结构

```
ExamMonitoringSystem/
├── monitor.py                 # 桌面界面入口（src.ui.app:run）
├── requirements.txt           # 运行依赖（含版本锁定）
├── user.example.json          # 教师名册导入模板
├── LICENSE / NOTICE           # Apache 2.0 许可与声明
├── src/
│   ├── ui/                    # PyQt5 桌面界面
│   ├── style/                 # QSS 主题
│   ├── cli/                   # 命令行入口
│   ├── core/                  # 核心业务逻辑
│   ├── db/                    # SQLite 访问层
│   └── utils/                 # 基础设施 / 工具
├── scripts/                   # 独立调试脚本
└── docs/                      # 设计文档
```

---

## 2. 整体架构

```
user.json（教师名册）          教务监考表 .xlsx
        │                          │
        ▼                          ▼
 ┌──────────────┐          ┌──────────────┐
 │ importers.py │          │ importers.py │
 │ load_user_json│         │ import_excel │
 └──────┬───────┘          └──────┬───────┘
        │                         │ parsing.py 解析
        ▼                         ▼
 ┌──────────────────────────────────────┐
 │  SQLite                              │
 │  data\monitor.db                     │
 │  teachers / courses / rooms          │
 │  exam_slots / exams / invigilators   │
 │  reminders（提醒队列 + 状态机）       │
 └───────────────┬──────────────────────┘
                 │ scheduler.queue_reminders()
                 │   按 (exam_date,start,end) 聚合时间段
                 │   每个考场一段 → 合并成 1 条消息
                 ▼
 ┌──────────────────────────────┐
 │ 桌面端 ServiceThread（QThread）│
 │ 命令行 service.run_loop       │
 │ 每 30s 轮询 process_due()     │
 │   expire / cancel 过期清理    │
 │   claim → send → mark        │
 └───────────────┬──────────────┘
                 ▼
        dingtalk.send_text()
        （加签 POST 到群机器人）
```

**核心设计**：**SQLite 本身就是队列**。不使用 APScheduler 之类的内存调度器，因此进程重启、机器休眠都不会丢失提醒——到点未发的记录仍在 `pending`，下一轮轮询会被捡起。`reminders` 表上的 `UNIQUE(reminder_type, slot_id)` 约束从数据库层面杜绝重复发送。数据库结构通过 `PRAGMA user_version` 做增量迁移，升级后首次启动自动完成，历史数据不丢失。

---

## 3. 数据模型

系统数据库共 10 张表，详见 [docs/database.md](./docs/database.md) 与 [docs/data-dictionary.md](./docs/data-dictionary.md)文件。

| 表                                     | 说明                                                        | 关键约束                                    |
| -------------------------------------- | ----------------------------------------------------------- | ------------------------------------------- |
| `teachers`                           | 教师名册：UUID 主键、姓名、钉钉账号、手机号、角色           | 姓名允许重名，按钉钉账号/手机号判重         |
| `courses`                            | 课程字典：课程号 + 课程名                                   | `UNIQUE(code, name)`                      |
| `rooms`                              | 考场字典：规范化名称 + 楼号/教室来源字段                    | `UNIQUE(name)`                            |
| `exam_slots`                         | 考试时间段：日期 + 起止时间                                 | `UNIQUE(exam_date, start_time, end_time)` |
| `exams`                              | 一场考试 = 课程 + 考场 + 时间段                             | `UNIQUE(room_id, slot_id)`                |
| `exam_invigilators`                  | 监考安排：座位标签 + 教师（名册外教师 `teacher_id` 为空） | `UNIQUE(exam_id, seat_label)`             |
| `reminders`                          | 提醒队列：一个时间段 + 提醒类型 = 一条待发消息              | `UNIQUE(reminder_type, slot_id)`          |
| `reminder_recipients`                | 提醒收件人快照                                              | `UNIQUE(reminder_id, teacher_name)`       |
| `import_batches` / `import_errors` | 导入批次统计 / 拒收明细（2 张表）                           | —                                          |

所有主键均为 36 位 UUID 字符串（由 Python 应用层生成），满足 1NF～BCNF；建表语句独立维护在 `src/db/sql/schema.sql`（`src/db/schema.py` 为其内嵌副本）。文件末尾含 3 个视图（`v_exam_details` / `v_invigilator_details` / `v_due_reminders`，供界面查看与轮询服务使用，不含 UUID/时间戳等系统字段）与 7 个触发器（`trg_*`，自动维护 `updated_at` / `sent_at`）。

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
- **必需列**：`课程`、`考试时间`、`考试教室`；`楼号` 可选（用于拼装考场名）。其余列（校区、选课人数、学院等）一律忽略。
- **监考教师列**：`主监考` 在前，`副监考1`、`副监考2`……按序号识别，允许为空。
- **考试时间格式**：`第8周,周5,(2026-10-23),13:30-15:30`，正则提取周次、星期、日期、起止时间。全角标点会先归一化为半角。
- **一致性校验**：星期与日期不符、结束时间不晚于开始、日期非法，一律抛 `ParseError` 并**记入拒收清单**（不静默丢弃），界面以弹窗 + 日志提示。
- **教师姓名**：归一化后查 `teachers` 表。唯一命中则 `resolved` 参与提醒；未命中（名册外教师）**不提醒、不推送管理员**，只作为「同场监考教师」出现在消息名单里。

### 4.2 消息合并策略（`src/core/scheduler.py`）

这是控制钉钉发送频率的核心：

1. 按 `(exam_date, start_time, end_time)` 聚合成**时间段**（同一时间段可能含多门课、多个考场）
2. 该时间段内**每个考场生成一段正文**，段落合并成**同一条消息**，结尾追加 `warm_tip`
3. 一位教师若同时监考多个考场，每段各自 `@`，`atMobiles` 去重
4. **整条消息没有库内教师时直接跳过**，不产生空消息

### 4.3 两阶段话术

| 类型           | 触发时刻                         |
| -------------- | -------------------------------- |
| `day_before` | 考试前一天（默认 18:00，可配置） |
| `t2h`        | 开考前 2 小时                    |

> 提醒时刻、开关与话术模板均由用户在界面「提醒设置」中编辑，保存后写入 `setting.json`，运行期以 json 为准，代码不硬编码（仅首次运行写入默认值兜底）。

**边界处理**：若导入时考试已进入当天（含已不足两小时），只建 `t2h` 行——到点即发，不会漏掉临近监考，也不会再发误导性的「明天」话术。已存在的 `day_before` 待发记录会被 `cancel_stale_day_before()` 置为 `cancelled`。

### 4.4 `@` 人的实现与降级

钉钉**自定义机器人只能按手机号**（`atMobiles`）或用户 ID 真正高亮 `@`，而 Webhook 机器人拿不到通讯录里的 userId 映射，因此：

- 名册内且**有合法手机号** → 正文写 `@手机号`，同时填入 `atMobiles`，钉钉渲染为可点 `@姓名`
- 名册内但**无手机号** → 正文退化为纯姓名点名，同时记入 `missing` 清单
- `missing` 非空时，为管理员（`role='admin'` 或 `developer`）追加一条**【提醒异常】**消息，请管理员线下提醒
- 手机号格式非法（非 `1\d{10}`）按「未登记」处理，绝不把脏号塞进 `atMobiles` 导致整条消息发送失败

### 4.5 发送幂等与容错

- `claim_reminder()` 用 `UPDATE ... WHERE status='pending'` 的 `rowcount==1` **原子占用**，多进程/重启均不会重复发送
- 单轮处理异常被捕获并记录，**不拖垮常驻服务**
- 钉钉网络异常自动重试（默认 3 次，间隔 10s）；主消息发出后，附属的管理员异常通知即使失败也只写 `last_error`，不回滚主提醒状态

---

## 5. 消息模板

占位符统一用 `{name}` 形式，由 `messages.fill()` 按名替换（不用 `str.format`，教师姓名里的花括号不会破坏模板）。

- 前一天 / 考前 2 小时的**话术模板**（`day_before_template`、`t2h_template`）由用户在「提醒设置」中编辑，保存后写入 `setting.json`，运行期从 json 读取。
- 异常通知、打招呼、温馨提示等固定文案仍在 **`src/utils/prompt.py`**。

| 配置项 / 变量                           | 用途                 | 占位符                                                              |
| --------------------------------------- | -------------------- | ------------------------------------------------------------------- |
| `day_before_template`（setting.json） | 前一天提醒           | `at_teacher` `course` `time` `location` `proctor_teacher` |
| `t2h_template`（setting.json）        | 考前 2 小时提醒      | 同上                                                                |
| `error_feedback_msg`                  | 管理员异常通知       | `err_teacher` `admin_teacher`                                   |
| `helloteacher`                        | 机器人打招呼         | `hi_all`                                                          |
| `warm_tip`                            | 每条提醒结尾温馨提示 | —                                                                  |

---

## 6. 安装与配置

### 6.1 依赖

Python ≥ 3.10（开发环境 3.13）。核心数据/推送依赖见 `requirements.txt`，桌面界面另需 PyQt5：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 6.2 首次配置

1. 打开「钉钉配置」页签，填写钉钉群自定义机器人的 **Webhook URL** 与**加签密钥 Secret**（安全设置选「加签」），保存。
2. 打开「提醒设置」页签，设置前一天提醒的**触发时刻**、两条提醒的**开关**与**话术模板**（支持占位符），保存。

### 6.3 教师名册 `user.json`

首次使用时通过「导入教师名册」（JSON 文件）导入。格式：

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

### 7.1 桌面端

```bash
python monitor.py
```

- **监考信息 / 监考人员 / 轮询服务**：三张视图切换查看；前两者支持双击编辑、拖拽调整行列，「保存编辑」统一落库
- **导入监考表**：选择教务导出的 `.xlsx`，导入后自动重建提醒队列；界面会提示「拒收明细」与「名册未命中」清单
- **启动服务 / 停止服务**：启动内置常驻线程，每 30s 扫描待发提醒；状态变绿后即可最小化
- **清理过期数据**：删除所有已结束考试的时间段/考试/监考安排/提醒（课程、考场字典保留）
- **服务器日志**：底部实时显示解析失败、发送失败等日志，便于排查

### 7.2 命令行（调试 / 自动化）

命令行入口为 `src/cli/cmd.py`：

```bash
python -m src.cli.cmd hello                       # 机器人 @所有人 打招呼
python -m src.cli.cmd iptjson --json user.json    # 建库并导入教师名册
python -m src.cli.cmd iptxlsx 监考表.xlsx          # 导入监考表（默认重建提醒队列）
python -m src.cli.cmd iptxlsx 监考表.xlsx --no-queue # 只导数据，不重建队列
python -m src.cli.cmd server                      # 常驻，每 30s 扫描待发提醒
python -m src.cli.cmd server --once               # 只跑一轮后退出
python -m src.cli.cmd server --dry-run            # 只打印要发的内容，不真正调钉钉
python -m src.cli.cmd server --interval 60        # 自定义扫描间隔（秒）
python -m src.cli.cmd server --skip-queue         # 启动时不重建队列
```

对应独立脚本（等价功能）：

| 脚本                                                    | 等价子命令                   |
| ------------------------------------------------------- | ---------------------------- |
| `python scripts/hello.py`                             | `hello`                    |
| `python scripts/import_json.py --json user.json`      | `iptjson --json user.json` |
| `python scripts/import_excel.py table.xlsx`           | `iptxlsx table.xlsx`       |
| `python scripts/run.py [--once/--dry-run/--interval]` | `server [...]`             |

---

## 8. 运维要点

| 场景                      | 处理                                                                                                 |
| ------------------------- | ---------------------------------------------------------------------------------------------------- |
| 机器休眠/断电错过触发时刻 | 无需处理，恢复后下一轮轮询自动补发（考试未结束时）                                                   |
| 考试已结束的积压提醒      | 自动置 `expired`，不会事后骚扰                                                                     |
| 监考表变更                | 重新导入监考表；已 `sent` 的提醒不受影响，未发的被更新                                             |
| 教师无手机号              | 检查导入输出的「名册未命中」与 `load_user_json` 的警告日志                                         |
| 发送失败排查              | 查界面「服务器日志」中的 `钉钉发送失败：errcode=... errmsg=...`，或直接查 `reminders.last_error` |
| 钉钉限流                  | 自定义机器人 20 条/分钟；按时间段合并后日均远低于此                                                  |
| 结构升级                  | 升级后首次启动自动迁移 `PRAGMA user_version`，历史数据不丢失，无需手动删库                         |

---

## 9. 已知限制

- 自定义机器人**无法按姓名自动映射 userId**，`@` 依赖手机号；手机号错误或未登记会导致提醒降级为纯文本点名
- 监听对象是固定群：新增教师需先入群，否则消息可见但收不到通知
- 同一时间段的一位教师会在多个考场段落中被重复 `@`（`atMobiles` 已去重，高亮次数可能多于一次）
- 无 Web 界面与鉴权，依赖运行机器的本地环境与 `setting.json` 保护
- 系统只服务单一上海时区（`Asia/Shanghai`），不跨时区使用

---

## 10. 技术栈

| 层次            | 选型                                                                  |
| --------------- | --------------------------------------------------------------------- |
| 语言            | Python 3.13（≥ 3.10）                                                |
| 桌面界面        | PyQt5（`QMainWindow` + `QAbstractTableModel` + `QThread`）      |
| Excel 解析      | `pandas` + `openpyxl`                                             |
| 状态存储 / 队列 | SQLite（标准库 `sqlite3`，WAL 模式 + `PRAGMA user_version` 迁移） |
| 调度            | 自实现轮询（`ServiceThread` / `service.run_loop`，默认 30s）      |
| 推送            | 钉钉自定义机器人 Webhook + HMAC-SHA256 加签（`requests`）           |
| 时区            | `zoneinfo`，固定 `Asia/Shanghai`                                  |

## 11. 开源许可

本项目采用 [Apache 2.0](LICENSE) 开源许可。

## 12. 致谢

- 本项目在设计与开发过程中使用了 [Claude Code](https://claude.com/claude-code)（Anthropic 出品的 AI 编程助手）。Claude Code 在需求梳理、数据库规范化重构、PyQt 桌面组件函数绑定，以及文档的撰写中提供了重要帮助。特此向 Anthropic 团队与 Claude Code 致以诚挚的感谢。
- [Claude Code](https://claude.com/claude-code) 接入了[DeepSeek](https://platform.deepseek.com) 模型，是 Claude Code 思考的大脑，为该项目的落地提供了巨大的贡献，特此向 DeepSeek 团队致以诚挚的感谢。
- 个人开发的开源项目，经验不足，系统难免存在不足之处。如果您在体验中发现问题，或者有优化的想法，非常期待您通过 Issue 或 PR 参与共建。感谢您的支持与理解。

## 附件

### 监考表实例 Excel 格式

| 课程代码/课程名称            | 考试时间                           | 校区     | 楼号  | 考试教室  | 主监考 | 副监考1 | 副监考2 | 副监考3 ... | 选课人数 | 要求人数 | 学院   | 监考学院         |
| ---------------------------- | ---------------------------------- | -------- | ----- | --------- | ------ | ------- | ------- | ----------- | -------- | -------- | ------ | ---------------- |
| 02401503/概率论与数理统计(I) | 第8周,周1,(2026-10-26),13:30-15:30 | 东丽校区 | 南教3 | 南教3-104 | 教师1  | 教师2   | 教师3   | 教师...     | 36       | 55       | 理学院 | 空中交通管理学院 |
| 02401504/高等数学            | 第8周,周1,(2026-10-26),13:30-15:30 | 东丽校区 | 南教3 | 南教3-112 | 教室4  | 教室5   | 教师6   | 教师...     | 36       | 55       | 理学院 | 空中交通管理学院 |

> 系统只读取 `课程`、`考试时间`、`楼号`、`考试教室`、`主监考` 与 `副监考N` 列；其他列会被忽略。
