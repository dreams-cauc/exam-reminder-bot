# 教师监考提醒
# 前一天
daybefore_invigilator_msg = """【📅 监考提醒】 {at_teacher} 

老师您好，您明天的监考信息如下
📚 科目：{course}
🕘 时间：{time}
📍 教室：{location}
👥 老师：{proctor_teacher}
======================

"""

# 两小时和两小时内
t2h_invigilator_msg = """【⏰ 监考提醒】 {at_teacher}

老师您好，您的监考已不足两小时
📚 科目：{course}
🕘 时间：{time}
📍 教室：{location}
👥 老师：{proctor_teacher}
======================

"""

# 监考提醒反馈
error_feedback_msg = """【⚠️ 提醒异常】

老师 {err_teacher} 信息异常，请管理员 {admin_teacher} 提醒老师按时监考
"""

# 机器人打招呼at_all
helloteacher = """{hi_all} 各位老师好，我是监考提醒助手。
监考前我会通过钉钉向您推送监考安排，很高兴为您服务！"""

warm_tip = "[温馨提示] 系统提醒功能仍在持续完善中，推送信息仅供参考，最终请以实际监考表为准。"
