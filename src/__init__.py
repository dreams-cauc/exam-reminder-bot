# -*- coding: utf-8 -*-
"""监考提醒系统核心包。

模块划分：
    config     路径 / 时区 / 钉钉凭证 / 提醒时刻参数
    db         SQLite 数据库访问层（包）
    parsing    考试时间字符串、姓名、课程字段解析
    importers  教务 Excel 与 user.json 导入
    scheduler  提醒时刻计算、按场次合并、队列同步
    messages   prompt.py 模板填充
    dingtalk   自定义机器人加签发送
    greeting   机器人打招呼（@所有人 自我介绍）
    service    轮询发送主循环
"""

__author__ = "STAR-dreams"
__version__ = "0.1.0"
