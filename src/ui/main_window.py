# -*- coding: utf-8 -*-
"""主窗口：把界面组件与后端逻辑绑定。

examTable 统一展示三张视图，cboxSelectTable 切换视图；表格支持双击编辑、
鼠标拖拽调整行列；编辑结果由「保存编辑」统一落库，退出前未保存会提示。
"""

from __future__ import annotations

import logging

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
)

from ..utils import config
from ..utils.settings import settings
from .. import db
from ..core import importers, scheduler
from .main_ui import Ui_MainWindow
from .log_handler import LogBridge, QtLogHandler
from .service_thread import ServiceThread
from .table_model import TableModel

logger = logging.getLogger(__name__)

VIEW_EXAM = "exam"
VIEW_INVIGILATOR = "invigilator"
VIEW_DUE = "due"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._model = TableModel(self)
        self._service = ServiceThread(self)
        self._view_index = 0

        self._setup_table()
        self._setup_logging()
        self._setup_signals()
        self._load_settings_into_form()

        self._refresh_all()

    # -- 初始化 --------------------------------------------------
    def _setup_table(self) -> None:
        table = self.ui.examTable
        table.setModel(self._model)
        # 双击进入编辑，模拟 Excel 操作
        table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        # 行、列均可通过鼠标拖拽调整大小
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        vheader = table.verticalHeader()
        vheader.setSectionResizeMode(QHeaderView.Interactive)
        vheader.setDefaultSectionSize(28)
        vheader.setVisible(True)

    def _setup_logging(self) -> None:
        self._bridge = LogBridge()
        self._bridge.message.connect(self._append_log)
        self._log_handler = QtLogHandler(self._bridge)
        logging.getLogger().addHandler(self._log_handler)

    def _setup_signals(self) -> None:
        self.ui.cboxSelectTable.currentIndexChanged.connect(self._on_view_changed)
        self.ui.btnImportExcel.clicked.connect(self._on_import)
        self.ui.btnRefresh.clicked.connect(self._refresh_all)
        self.ui.btnDelete.clicked.connect(self._on_delete)
        self.ui.btnSaveEdit.clicked.connect(self._on_save_edit)
        self.ui.btnStartService.clicked.connect(self._on_start_service)
        self.ui.btnStopService.clicked.connect(self._on_stop_service)
        self.ui.btnSaveRemind.clicked.connect(self._on_save_reminder)
        self.ui.btnSaveDingtalk.clicked.connect(self._on_save_dingtalk)
        self.ui.btnCleanup.clicked.connect(self._on_cleanup)
        self._service.finished.connect(self._on_service_finished)

    # -- 视图切换与加载 ------------------------------------------
    def _current_view(self) -> str:
        idx = self.ui.cboxSelectTable.currentIndex()
        return (VIEW_EXAM, VIEW_INVIGILATOR, VIEW_DUE)[idx]

    def _view_meta(self, view: str) -> tuple[list[str], set[int], str]:
        if view == VIEW_EXAM:
            headers = ["课程名", "考场名", "考试日期", "开始时间", "结束时间"]
            return headers, {0, 1, 2, 3, 4}, "list_exam_rows"
        if view == VIEW_INVIGILATOR:
            headers = [
                "课程名", "考场名", "考试日期", "开始时间",
                "监考座位", "教师姓名", "手机号", "钉钉号", "角色",
            ]
            return headers, {0, 1, 2, 3, 4, 5, 6, 7, 8}, "list_invigilator_rows"
        headers = ["提醒ID", "提醒类型", "计划触发时间", "消息内容", "异常消息", "教师姓名", "手机号"]
        return headers, set(), "list_due_reminder_rows"

    def _on_view_changed(self, index: int) -> None:
        if self._model.is_dirty():
            ret = QMessageBox.question(
                self, "切换视图",
                "当前视图有未保存的编辑，切换将丢失修改。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                self.ui.cboxSelectTable.blockSignals(True)
                self.ui.cboxSelectTable.setCurrentIndex(self._view_index)
                self.ui.cboxSelectTable.blockSignals(False)
                return
        self._view_index = index
        self._refresh_table()

    def _refresh_all(self) -> None:
        self._refresh_table()
        self._refresh_stats()

    def _refresh_table(self) -> None:
        view = self._current_view()
        headers, editable, loader_name = self._view_meta(view)
        with db.session() as conn:
            rows = getattr(db, loader_name)(conn)

        checker = self._invig_checker if view == VIEW_INVIGILATOR else None
        self._model.load(headers, rows, editable, checker)

        # 轮询服务视图只读：导入/保存按钮不可用
        read_only = view == VIEW_DUE
        self.ui.btnImportExcel.setEnabled(not read_only)
        self.ui.btnSaveEdit.setEnabled(not read_only)
        self.ui.btnDelete.setEnabled(True)

        if view == VIEW_EXAM:
            self.ui.btnImportExcel.setText("导入监考表")
        elif view == VIEW_INVIGILATOR:
            self.ui.btnImportExcel.setText("导入教师名册")
        else:
            self.ui.btnImportExcel.setText("导入 Excel")

    def _refresh_stats(self) -> None:
        with db.session() as conn:
            stats = db.get_stats(conn)
        self.ui.lblStatTaskVal.setText(str(stats["task"]))
        self.ui.lblStatPendingVal.setText(str(stats["pending"]))
        self.ui.lblStatDoneVal.setText(str(stats["done"]))

    @staticmethod
    def _invig_checker(row: dict, col: int) -> bool:
        # 手机号/钉钉号/角色 属于名册（teachers 表），名册外教师（_teacher_id 为空）不可编辑
        if col in (6, 7, 8):
            return bool(row.get("_teacher_id"))
        return True

    # -- 导入 ----------------------------------------------------
    def _on_import(self) -> None:
        view = self._current_view()
        if view == VIEW_EXAM:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择监考表", "", "Excel 文件 (*.xlsx *.xls)"
            )
            if not path:
                return
            try:
                report = importers.import_excel(path)
            except Exception as exc:
                logger.error("导入监考表失败：%s", exc)
                QMessageBox.critical(self, "导入", f"导入失败：{exc}")
                return
            if report.rejected:
                QMessageBox.warning(
                    self, "导入", f"有 {len(report.rejected)} 行被拒收，明细见日志。"
                )
        elif view == VIEW_INVIGILATOR:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择教师名册", "", "JSON 文件 (*.json)"
            )
            if not path:
                return
            try:
                count = importers.load_user_json(path)
            except Exception as exc:
                logger.error("导入教师名册失败：%s", exc)
                QMessageBox.critical(self, "导入", f"导入失败：{exc}")
                return
            QMessageBox.information(self, "导入", f"已导入 {count} 名教师。")
        else:
            return

        self._rebuild_queue()
        self._refresh_all()

    # -- 编辑保存 -------------------------------------------------
    def _on_save_edit(self) -> None:
        if not self._model.is_dirty():
            QMessageBox.information(self, "保存编辑", "没有需要保存的修改。")
            return
        view = self._current_view()
        if view == VIEW_DUE:
            return
        try:
            with db.session() as conn:
                for row_idx, col in self._model.changed_cells():
                    row = self._model.row_dict(row_idx)
                    value = row[self._model.header(col)]
                    if not self._apply_edit(conn, view, row, col, value):
                        raise ValueError(f"无法保存该单元格：{self._model.header(col)}")
        except Exception as exc:
            logger.error("保存编辑失败：%s", exc)
            QMessageBox.critical(self, "保存编辑", f"保存失败：{exc}")
            return

        self._model.mark_clean()
        logger.info("编辑已保存。")
        self._rebuild_queue()
        self._refresh_all()

    def _apply_edit(
        self, conn, view: str, row: dict, col: int, value: str
    ) -> bool:
        header = self._model.header(col)
        if view == VIEW_EXAM:
            if header == "课程名":
                db.update_course_name(conn, row["_course_id"], value)
            elif header == "考场名":
                db.update_room_name(conn, row["_room_id"], value)
            elif header == "考试日期":
                db.update_slot(
                    conn, row["_slot_id"],
                    exam_date=value, start_time=row["开始时间"], end_time=row["结束时间"],
                )
            elif header == "开始时间":
                db.update_slot(
                    conn, row["_slot_id"],
                    exam_date=row["考试日期"], start_time=value, end_time=row["结束时间"],
                )
            elif header == "结束时间":
                db.update_slot(
                    conn, row["_slot_id"],
                    exam_date=row["考试日期"], start_time=row["开始时间"], end_time=value,
                )
            else:
                return False
            return True

        if view == VIEW_INVIGILATOR:
            if header == "课程名":
                db.update_course_name(conn, row["_course_id"], value)
            elif header == "考场名":
                db.update_room_name(conn, row["_room_id"], value)
            elif header == "考试日期":
                db.update_slot(
                    conn, row["_slot_id"],
                    exam_date=value, start_time=row["开始时间"], end_time=row["_end_time"],
                )
            elif header == "开始时间":
                db.update_slot(
                    conn, row["_slot_id"],
                    exam_date=row["考试日期"], start_time=value, end_time=row["_end_time"],
                )
            elif header == "监考座位":
                db.update_invigilator(conn, row["_invigilator_id"], seat_label=value)
            elif header == "教师姓名":
                db.update_invigilator(conn, row["_invigilator_id"], teacher_name=value)
            elif header == "手机号":
                db.update_teacher(conn, row["_teacher_id"], mobile=value)
            elif header == "钉钉号":
                db.update_teacher(conn, row["_teacher_id"], dingtalk_id=value)
            elif header == "角色":
                db.update_teacher(conn, row["_teacher_id"], role=value)
            else:
                return False
            return True

        return False

    # -- 删除 ----------------------------------------------------
    def _on_delete(self) -> None:
        index = self.ui.examTable.currentIndex()
        if not index.isValid():
            QMessageBox.information(self, "删除", "请先选中要删除的行。")
            return
        row = self._model.row_dict(index.row())
        view = self._current_view()
        rebuild = False

        if view == VIEW_EXAM:
            if self._confirm(f"确认删除该场考试（{row.get('课程名', '')}）？"):
                with db.session() as conn:
                    db.delete_exam(conn, row["_exam_id"])
                rebuild = True
            else:
                return
        elif view == VIEW_INVIGILATOR:
            if self._confirm(f"确认删除该监考安排（{row.get('教师姓名', '')}）？"):
                with db.session() as conn:
                    db.delete_invigilator(conn, row["_invigilator_id"])
                rebuild = True
            else:
                return
        elif view == VIEW_DUE:
            if self._confirm(f"确认取消该提醒（{row.get('提醒ID', '')}）？"):
                with db.session() as conn:
                    db.cancel_reminder(conn, row["_reminder_id"])
            else:
                return
        else:
            return

        if rebuild:
            self._rebuild_queue()
        self._refresh_all()

    def _confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "确认", text) == QMessageBox.Yes

    # -- 提醒队列 -------------------------------------------------
    def _rebuild_queue(self) -> None:
        try:
            scheduler.queue_reminders()
        except Exception:
            logger.exception("重建提醒队列失败")

    # -- 服务启停 -------------------------------------------------
    def _on_start_service(self) -> None:
        if self._service.isRunning():
            return
        # 未配置钉钉凭证时禁止启动，避免启动后每条都因缺少 webhook/secret 发送失败
        if not settings.webhook_url or not settings.secret:
            QMessageBox.warning(
                self,
                "未配置钉钉",
                "尚未配置钉钉机器人（webhook_url 或 secret 为空）。\n"
                "请先在「钉钉配置」中填写并保存，再启动服务。",
            )
            return
        self._service.start()
        self._set_service_state(True)

    def _on_stop_service(self) -> None:
        if self._service.isRunning():
            self._service.stop()
        self._set_service_state(False)

    def _on_service_finished(self) -> None:
        self._set_service_state(False)

    def _set_service_state(self, running: bool) -> None:
        label = self.ui.serviceState
        label.setText("● 服务运行中" if running else "● 服务已停止")
        label.setProperty("state", "online" if running else "error")
        # 动态属性变化后需重新抛光样式
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()
        self.ui.btnStartService.setEnabled(not running)
        self.ui.btnStopService.setEnabled(running)

    # -- 配置面板 -------------------------------------------------
    def _load_settings_into_form(self) -> None:
        self.ui.dbPathEdit.setText(str(config.DB_PATH))
        snap = settings.snapshot()
        dingtalk = snap.get("dingtalk", {})
        reminder = snap.get("reminder", {})

        self.ui.webhookEdit.setText(dingtalk.get("webhook_url", ""))
        self.ui.secretEdit.setText(dingtalk.get("secret", ""))

        self.ui.chkDayBefore.setChecked(bool(reminder.get("day_before_enabled", True)))
        h, m = self._parse_time(reminder.get("day_before_time", "18:00"))
        self.ui.timeDayBefore.setTime(QtCore.QTime(h, m))
        self.ui.msgTemplateDayBefore.setPlainText(reminder.get("day_before_template", ""))

        self.ui.chkTwoHours.setChecked(bool(reminder.get("t2h_enabled", True)))
        self.ui.msgTemplateTwoHours.setPlainText(reminder.get("t2h_template", ""))

    def _on_save_reminder(self) -> None:
        t = self.ui.timeDayBefore.time()
        reminder = {
            "day_before_enabled": self.ui.chkDayBefore.isChecked(),
            "day_before_time": f"{t.hour():02d}:{t.minute():02d}",
            "day_before_template": self.ui.msgTemplateDayBefore.toPlainText(),
            "t2h_enabled": self.ui.chkTwoHours.isChecked(),
            "t2h_template": self.ui.msgTemplateTwoHours.toPlainText(),
        }
        settings.update(reminder=reminder)
        settings.save()
        logger.info("提醒设置已保存。")
        # 配置实时生效：按新时刻/模板重建待发队列
        self._rebuild_queue()
        QMessageBox.information(self, "提醒设置", "提醒设置已保存，并已重建提醒队列。")

    def _on_save_dingtalk(self) -> None:
        settings.update(dingtalk={
            "webhook_url": self.ui.webhookEdit.text().strip(),
            "secret": self.ui.secretEdit.text().strip(),
        })
        settings.save()
        logger.info("钉钉配置已保存。")
        QMessageBox.information(self, "钉钉配置", "钉钉配置已保存。")

    def _on_cleanup(self) -> None:
        if not self._confirm("确认清理已过期/已取消的提醒？"):
            return
        with db.session() as conn:
            n = db.cleanup_expired(conn)
        logger.info("已清理 %d 条过期/取消的提醒。", n)
        self._refresh_all()

    @staticmethod
    def _parse_time(value: str) -> tuple[int, int]:
        try:
            h, m = str(value).strip().split(":", 1)
            return int(h), int(m)
        except (ValueError, AttributeError):
            return config.DAY_BEFORE_HOUR, config.DAY_BEFORE_MINUTE

    # -- 日志 / 退出 ---------------------------------------------
    def _append_log(self, text: str) -> None:
        self.ui.logView.appendPlainText(text.rstrip("\n"))

    def closeEvent(self, event) -> None:
        if self._model.is_dirty():
            ret = QMessageBox.question(
                self, "退出",
                "有未保存的编辑，是否放弃修改并退出？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
        if self._service.isRunning():
            self._service.stop()
        event.accept()
