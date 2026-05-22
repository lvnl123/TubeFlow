from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import webbrowser

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .downloader import DownloaderService
from .models import AVPlan, FormatOption, VideoMetadata

APP_STATE_PATH = Path(__file__).resolve().parents[2] / "app_state.json"


class Worker(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(float, str)
    status = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.finished.emit(self.fn())
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class FilterDialog(QDialog):
    def __init__(self, title: str, fields: list[tuple[str, str, list[str]]], current_values: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 0)
        self.inputs: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        for key, label, choices in fields:
            combo = QComboBox()
            combo.addItems(choices)
            combo.setCurrentText(current_values.get(key, "全部"))
            form.addRow(label, combo)
            self.inputs[key] = combo

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        reset_button = buttons.button(QDialogButtonBox.Reset)
        if reset_button is not None:
            reset_button.setText("重置")
            reset_button.clicked.connect(self.reset_values)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("应用筛选")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setText("取消")
        layout.addWidget(buttons)

    def reset_values(self) -> None:
        for combo in self.inputs.values():
            combo.setCurrentText("全部")

    def values(self) -> dict[str, str]:
        return {key: combo.currentText() for key, combo in self.inputs.items()}


class AdvancedSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, str], env_summary: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("高级设置 / 诊断")
        self.setModal(True)
        self.resize(620, 0)

        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        layout.addLayout(form)

        form.addWidget(QLabel("下载引擎"), 0, 0)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("自动（推荐）", "auto")
        self.engine_combo.addItem("CLI：yt-dlp.exe", "cli")
        self.engine_combo.addItem("Python / helper", "python")
        self._set_combo_by_data(self.engine_combo, settings.get("download_engine_mode", "auto"))
        form.addWidget(self.engine_combo, 0, 1)

        form.addWidget(QLabel("Cookie 来源"), 1, 0)
        self.cookie_mode_combo = QComboBox()
        self.cookie_mode_combo.addItem("不使用 Cookie", "none")
        self.cookie_mode_combo.addItem("cookies.txt 文件", "file")
        self.cookie_mode_combo.addItem("浏览器 Cookie", "browser")
        self._set_combo_by_data(self.cookie_mode_combo, settings.get("cookie_mode", "none"))
        self.cookie_mode_combo.currentIndexChanged.connect(self._update_cookie_inputs)
        form.addWidget(self.cookie_mode_combo, 1, 1)

        form.addWidget(QLabel("Cookie 文件"), 2, 0)
        cookie_file_row = QHBoxLayout()
        self.cookie_file_input = QLineEdit(settings.get("cookie_file", ""))
        self.cookie_file_input.setPlaceholderText("选择 cookies.txt 文件")
        cookie_file_row.addWidget(self.cookie_file_input, 1)
        browse_button = QPushButton("选择文件")
        browse_button.clicked.connect(self._choose_cookie_file)
        cookie_file_row.addWidget(browse_button)
        form.addLayout(cookie_file_row, 2, 1)

        form.addWidget(QLabel("浏览器"), 3, 0)
        self.cookie_browser_combo = QComboBox()
        self.cookie_browser_combo.addItem("Chrome", "chrome")
        self.cookie_browser_combo.addItem("Edge", "edge")
        self._set_combo_by_data(self.cookie_browser_combo, settings.get("cookie_browser", "chrome"))
        form.addWidget(self.cookie_browser_combo, 3, 1)

        self.env_label = QLabel()
        self.env_label.setWordWrap(True)
        self.env_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.env_label)
        self.set_environment_summary(env_summary)

        tools_row = QHBoxLayout()
        self.test_proxy_button = QPushButton("测试代理")
        self.test_cookie_button = QPushButton("检查 Cookie")
        tools_row.addWidget(self.test_proxy_button)
        tools_row.addWidget(self.test_cookie_button)
        tools_row.addStretch(1)
        layout.addLayout(tools_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("保存设置")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_cookie_inputs()

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _choose_cookie_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Cookie 文件",
            self.cookie_file_input.text().strip(),
            "Text Files (*.txt);;All Files (*)",
        )
        if path:
            self.cookie_file_input.setText(path)

    def _update_cookie_inputs(self) -> None:
        mode = self.cookie_mode_combo.currentData()
        self.cookie_file_input.setEnabled(mode == "file")
        self.cookie_browser_combo.setEnabled(mode == "browser")

    def set_environment_summary(self, env_summary: dict[str, str]) -> None:
        self.env_label.setText(
            "\n".join(
                [
                    f"当前模式：{env_summary.get('engine_mode', '自动')}  |  实际引擎：{env_summary.get('effective_engine', '未知')}",
                    f"yt-dlp：{env_summary.get('yt_dlp_path', '未检测到')}",
                    f"FFmpeg：{env_summary.get('ffmpeg_path', '未检测到')}",
                    f"代理：{env_summary.get('proxy', '未启用')}",
                    f"Cookie：{env_summary.get('cookie', '未启用')}",
                    f"JS 运行时：{env_summary.get('js_runtime', '未检测到')}",
                ]
            )
        )

    def values(self) -> dict[str, str]:
        return {
            "download_engine_mode": str(self.engine_combo.currentData()),
            "cookie_mode": str(self.cookie_mode_combo.currentData()),
            "cookie_file": self.cookie_file_input.text().strip(),
            "cookie_browser": str(self.cookie_browser_combo.currentData()),
        }


class DownloaderWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.service = DownloaderService()
        self.current_url = ""
        self.current_video: VideoMetadata | None = None
        self.video_options: list[FormatOption] = []
        self.audio_options: list[FormatOption] = []
        self.av_options: list[AVPlan] = []
        self.video_selection_hint: QLabel | None = None
        self.audio_selection_hint: QLabel | None = None
        self.av_selection_hint: QLabel | None = None
        self.video_download_status_label: QLabel | None = None
        self.audio_download_status_label: QLabel | None = None
        self.av_download_status_label: QLabel | None = None
        self.pause_resume_button: QPushButton | None = None
        self.cancel_download_button: QPushButton | None = None
        self.filter_button: QPushButton | None = None
        self.advanced_settings_button: QPushButton | None = None
        self.engine_context_label: QLabel | None = None
        self.download_context_label: QLabel | None = None
        self.worker_thread: QThread | None = None
        self.worker: Worker | None = None
        self.pending_stage: str | None = None
        self.recent_urls: list[str] = []
        self.state = self._load_state()
        self.table_filters: dict[str, dict[str, str]] = {"video": {}, "audio": {}, "av": {}}
        self.active_download_tab_index = 0
        self.close_after_cancel = False

        self.current_task_kind = ""
        self.current_task_started_at = 0.0
        self.last_heartbeat_second = -1
        self.download_engine_mode = str(self.state.get("download_engine_mode", "auto"))
        self.cookie_mode = str(self.state.get("cookie_mode", "none"))
        self.cookie_file = str(self.state.get("cookie_file", ""))
        self.cookie_browser = str(self.state.get("cookie_browser", "chrome"))
        self._base_width = 1760
        self._base_height = 1040
        self._ui_scale = 1.0
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(1000)
        self.activity_timer.timeout.connect(self._tick_activity)

        self.setWindowTitle("YouTube 下载器")
        self.resize(self._base_width, self._base_height)
        self.setMinimumSize(1420, 900)

        self._build_ui()
        self._apply_styles()
        self._restore_state()
        self._update_ui_scale(force=True)
        self._append_log("应用已启动。")
        if self.service.prefer_helper:
            self._append_log(
                f"当前运行环境为 Python {sys.version_info.major}.{sys.version_info.minor}，"
                "解析与下载会自动转交给兼容解释器。"
            )

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._build_progress_panel())

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)
        splitter.addWidget(top_container)

        log_panel = self._build_log_panel()
        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()
        top_layout.addWidget(left_panel, 0)
        top_layout.addWidget(right_panel, 1)
        top_layout.setStretch(0, 36)
        top_layout.setStretch(1, 64)

        splitter.addWidget(log_panel)
        splitter.setSizes([640, 180])

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("准备就绪")
        self.parse_button.setDefault(True)
        self.url_combo.lineEdit().returnPressed.connect(self.parse_url)
        self.filter_input.textChanged.connect(self._apply_filter)
        self.proxy_input.editingFinished.connect(self._handle_proxy_changed)
        self.tabs.currentChanged.connect(lambda _index: self._update_filter_count_label())
        self.video_table.itemSelectionChanged.connect(self._update_video_selection_hint)
        self.audio_table.itemSelectionChanged.connect(self._update_audio_selection_hint)
        self.av_table.itemSelectionChanged.connect(self._update_av_selection_hint)

    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        self.url_combo = QComboBox()
        self.url_combo.setEditable(True)
        self.url_combo.setInsertPolicy(QComboBox.NoInsert)
        self.url_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.url_combo.lineEdit().setPlaceholderText("粘贴 YouTube 单视频链接，比如 https://youtu.be/-HZqvwyyINM")
        layout.addWidget(self.url_combo, 1)

        self.parse_button = QPushButton("快速解析")
        self.parse_button.clicked.connect(self.parse_url)
        layout.addWidget(self.parse_button)

        self.refresh_button = QPushButton("重新解析")
        self.refresh_button.clicked.connect(self.parse_url)
        layout.addWidget(self.refresh_button)

        self.open_source_button = QPushButton("打开原视频")
        self.open_source_button.clicked.connect(self.open_source_page)
        layout.addWidget(self.open_source_button)

        self.copy_title_button = QPushButton("复制标题")
        self.copy_title_button.clicked.connect(self.copy_title)
        layout.addWidget(self.copy_title_button)
        return layout

    def _build_progress_panel(self) -> QWidget:
        box = QGroupBox("任务进度")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        row = QHBoxLayout()
        self.progress_note = QLabel("准备就绪")
        row.addWidget(self.progress_note, 1)
        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.elapsed_label)
        layout.addLayout(row)
        return box

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        target_group = QGroupBox("下载设置")
        target_layout = QGridLayout(target_group)
        target_layout.setHorizontalSpacing(8)
        target_layout.setVerticalSpacing(10)
        for row in range(5):
            target_layout.setRowMinimumHeight(row, 42)
        target_layout.setRowMinimumHeight(5, 54)

        target_layout.addWidget(QLabel("保存目录"), 0, 0)
        self.output_input = QLineEdit()
        target_layout.addWidget(self.output_input, 0, 1)

        browse_button = QPushButton("选择目录")
        browse_button.clicked.connect(self.choose_output_dir)
        target_layout.addWidget(browse_button, 0, 2)

        open_folder_button = QPushButton("打开目录")
        open_folder_button.clicked.connect(self.open_output_dir)
        target_layout.addWidget(open_folder_button, 0, 3)

        target_layout.addWidget(QLabel("格式筛选"), 1, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("输入 1080 / mp4 / m4a / 720p 之类的关键词快速筛选")
        target_layout.addWidget(self.filter_input, 1, 1, 1, 3)

        target_layout.addWidget(QLabel("代理地址"), 2, 0)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:10808")
        target_layout.addWidget(self.proxy_input, 2, 1, 1, 3)

        self.auto_open_checkbox = QCheckBox("下载完成后自动打开目录")
        target_layout.addWidget(self.auto_open_checkbox, 3, 0, 1, 2)

        self.clear_log_checkbox = QCheckBox("每次解析前清空日志")
        self.clear_log_checkbox.setChecked(True)
        target_layout.addWidget(self.clear_log_checkbox, 3, 2, 1, 2)

        self.show_all_formats_checkbox = QCheckBox("显示全部格式")
        self.show_all_formats_checkbox.toggled.connect(self._handle_show_all_toggled)
        target_layout.addWidget(self.show_all_formats_checkbox, 4, 0, 1, 2)
        self.advanced_settings_button = QPushButton("高级设置 / 诊断")
        self.advanced_settings_button.clicked.connect(self.open_advanced_settings)
        target_layout.addWidget(self.advanced_settings_button, 4, 2, 1, 2)
        self.engine_context_label = QLabel("下载链路：待检测")
        self.engine_context_label.setWordWrap(True)
        self.engine_context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        target_layout.addWidget(self.engine_context_label, 5, 0, 1, 4)

        layout.addWidget(target_group)

        info_group = QGroupBox("视频信息")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(10)

        self.title_label = QLabel("标题：")
        self.title_label.setWordWrap(True)
        self.title_label.setObjectName("titleLabel")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_layout.addWidget(self.title_label)

        self.meta_label = QLabel("作者：    时长：")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_layout.addWidget(self.meta_label)

        self.summary_box = QTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setMinimumHeight(250)
        self.summary_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        info_layout.addWidget(self.summary_box)

        stat_frame = QFrame()
        stat_layout = QGridLayout(stat_frame)
        stat_layout.setContentsMargins(0, 0, 0, 0)
        stat_layout.setHorizontalSpacing(18)
        stat_layout.setVerticalSpacing(6)
        self.video_count_label = QLabel("单独视频：0")
        self.audio_count_label = QLabel("单独音频：0")
        self.av_count_label = QLabel("音视频清晰度：0")
        self.runtime_label = QLabel("当前阶段：待解析")
        self.runtime_value_label = QLabel("待解析")
        self.runtime_value_label.setObjectName("runtimeValueLabel")
        for label in (self.video_count_label, self.audio_count_label, self.av_count_label, self.runtime_label, self.runtime_value_label):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stat_layout.addWidget(self.video_count_label, 0, 0)
        stat_layout.addWidget(self.audio_count_label, 0, 1)
        stat_layout.addWidget(self.av_count_label, 1, 0)
        runtime_row = QHBoxLayout()
        runtime_row.setContentsMargins(0, 0, 0, 0)
        runtime_row.setSpacing(6)
        runtime_row.addWidget(self.runtime_label)
        runtime_row.addWidget(self.runtime_value_label)
        runtime_row.addStretch(1)
        stat_layout.addLayout(runtime_row, 1, 1)
        info_layout.addWidget(stat_frame)
        layout.addWidget(info_group, 1)

        action_group = QGroupBox("快捷操作")
        action_layout = QVBoxLayout(action_group)
        action_layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.download_best_av_button = QPushButton("下载推荐音视频")
        self.download_best_av_button.clicked.connect(self.download_best_av)
        top_row.addWidget(self.download_best_av_button)

        self.download_best_audio_button = QPushButton("下载最佳音频")
        self.download_best_audio_button.clicked.connect(self.download_best_audio)
        top_row.addWidget(self.download_best_audio_button)
        action_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        clear_history_button = QPushButton("清空最近链接")
        clear_history_button.clicked.connect(self.clear_recent_urls)
        bottom_row.addWidget(clear_history_button)

        clear_logs_button = QPushButton("清空日志")
        clear_logs_button.clicked.connect(self.log_output.clear)
        bottom_row.addWidget(clear_logs_button)
        action_layout.addLayout(bottom_row)
        layout.addWidget(action_group)

        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.filter_button = QPushButton("筛选")
        self.filter_button.clicked.connect(self.open_current_filter_dialog)
        filter_corner = QWidget()
        filter_corner_layout = QHBoxLayout(filter_corner)
        filter_corner_layout.setContentsMargins(0, 0, 0, 0)
        filter_corner_layout.setSpacing(8)
        self.filter_count_label = QLabel("已启用 0 项")
        self.filter_count_label.setObjectName("filterCountLabel")
        filter_corner_layout.addWidget(self.filter_count_label)
        filter_corner_layout.addWidget(self.filter_button)
        self.tabs.setCornerWidget(filter_corner, Qt.TopRightCorner)
        self.video_table = self._create_table(["格式 ID", "容器", "常用纯视频"])
        self.audio_table = self._create_table(["格式 ID", "容器", "常用纯音频"])
        self.av_table = self._create_table(["视频格式", "音视频下载说明", "输出"])

        self.tabs.addTab(self._wrap_table_tab(self.video_table, "下载纯视频", self.download_selected_video), "单独视频")
        self.tabs.addTab(self._wrap_table_tab(self.audio_table, "下载纯音频", self.download_selected_audio), "单独音频")
        self.tabs.addTab(self._wrap_table_tab(self.av_table, "下载音视频", self.download_selected_av), "音视频")
        layout.addWidget(self.tabs)
        return panel

    def _build_log_panel(self) -> QWidget:
        box = QGroupBox("运行日志")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("这里会显示分阶段解析、下载进度和异常信息。")
        layout.addWidget(self.log_output)

        controls_row = QHBoxLayout()
        self.download_context_label = QLabel("当前链路：待检测")
        self.download_context_label.setWordWrap(True)
        self.download_context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        controls_row.addWidget(self.download_context_label, 1)
        controls_row.addStretch(1)
        self.pause_resume_button = QPushButton("暂停下载")
        self.pause_resume_button.setEnabled(False)
        self.pause_resume_button.clicked.connect(self.toggle_pause_download)
        controls_row.addWidget(self.pause_resume_button)
        self.cancel_download_button = QPushButton("取消下载")
        self.cancel_download_button.setEnabled(False)
        self.cancel_download_button.clicked.connect(self.cancel_active_download)
        controls_row.addWidget(self.cancel_download_button)
        layout.addLayout(controls_row)
        return box

    def _wrap_table_tab(self, table: QTableWidget, button_text: str, handler) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(table, 1)

        row = QHBoxLayout()
        hint = QLabel("当前选择：未选择")
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(hint, 1)
        status = QLabel("下载状态：待开始")
        status.setWordWrap(True)
        status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(status, 1)
        button = QPushButton(button_text)
        button.clicked.connect(handler)
        row.addWidget(button)
        layout.addLayout(row)
        if table is self.video_table:
            self.video_selection_hint = hint
            self.video_download_status_label = status
        elif table is self.audio_table:
            self.audio_selection_hint = hint
            self.audio_download_status_label = status
        elif table is self.av_table:
            self.av_selection_hint = hint
            self.av_download_status_label = status
        return page

    def _create_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for index in range(len(headers) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        return table

    def _apply_styles(self) -> None:
        scale = self._ui_scale
        font_size = self._scaled(14)
        group_radius = self._scaled(14)
        group_margin_top = self._scaled(12)
        group_padding_top = self._scaled(12)
        title_left = self._scaled(12)
        title_padding = self._scaled(6)
        control_radius = self._scaled(10)
        control_padding_y = self._scaled(8)
        control_padding_x = self._scaled(10)
        control_min_height = self._scaled(24)
        button_padding_y = self._scaled(10)
        button_padding_x = self._scaled(14)
        tab_padding_y = self._scaled(10)
        tab_padding_x = self._scaled(16)
        tab_margin_right = self._scaled(4)
        title_font_size = self._scaled(18)
        progress_height = self._scaled(22)
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: #f5f7fb;
                color: #1f2937;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: {font_size}px;
            }}
            QGroupBox {{
                border: 1px solid #d7dce5;
                border-radius: {group_radius}px;
                margin-top: {group_margin_top}px;
                padding-top: {group_padding_top}px;
                background: #ffffff;
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: {title_left}px;
                padding: 0 {title_padding}px;
            }}
            QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget {{
                border: 1px solid #cfd6e3;
                border-radius: {control_radius}px;
                padding: {control_padding_y}px {control_padding_x}px;
                background: #ffffff;
            }}
            QLineEdit, QComboBox {{
                min-height: {control_min_height}px;
            }}
            QTableWidget {{
                gridline-color: #e9edf5;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }}
            QPushButton {{
                border: none;
                border-radius: {control_radius}px;
                background: #1d4ed8;
                color: white;
                padding: {button_padding_y}px {button_padding_x}px;
                font-weight: 600;
                min-height: {control_min_height}px;
            }}
            QPushButton:hover {{ background: #1e40af; }}
            QPushButton:disabled {{ background: #9ca3af; }}
            QTabWidget::pane {{
                border: 1px solid #d7dce5;
                border-radius: {group_radius}px;
                background: white;
            }}
            QTabBar::tab {{
                padding: {tab_padding_y}px {tab_padding_x}px;
                margin-right: {tab_margin_right}px;
                border-top-left-radius: {control_radius}px;
                border-top-right-radius: {control_radius}px;
                background: #e8edf7;
            }}
            QTabBar::tab:selected {{
                background: white;
                color: #1d4ed8;
            }}
            QProgressBar {{
                border: 1px solid #d7dce5;
                border-radius: {control_radius}px;
                background: #eef2ff;
                text-align: center;
                min-height: {progress_height}px;
            }}
            QProgressBar::chunk {{
                border-radius: {control_radius}px;
                background: #2563eb;
            }}
            QLabel#titleLabel {{
                font-size: {title_font_size}px;
                font-weight: 700;
            }}
            QLabel#runtimeValueLabel {{
                font-weight: 700;
                color: #b45309;
            }}
            QLabel#selectionHintLabel {{
                color: #475569;
                background: #f8fafc;
                border: 1px solid #dbe3f0;
                border-radius: {control_radius}px;
                padding: {control_padding_y}px {control_padding_x}px;
            }}
            QLabel#filterCountLabel {{
                color: #475569;
                font-weight: 600;
            }}
            QLabel#downloadStatusLabel {{
                color: #334155;
                background: #f8fafc;
                border: 1px solid #dbe3f0;
                border-radius: {control_radius}px;
                padding: {control_padding_y}px {control_padding_x}px;
            }}
            """
        )
        self._update_filter_count_label()
        for label in (self.video_selection_hint, self.audio_selection_hint, self.av_selection_hint):
            if label is not None:
                label.setObjectName("selectionHintLabel")
        for label in (self.video_download_status_label, self.audio_download_status_label, self.av_download_status_label):
            if label is not None:
                label.setObjectName("downloadStatusLabel")

    def _scaled(self, value: int) -> int:
        return max(1, int(round(value * self._ui_scale)))

    def _update_ui_scale(self, force: bool = False) -> None:
        ratio = min(self.width() / self._base_width, self.height() / self._base_height)
        ratio = max(0.92, min(1.28, ratio))
        if not force and abs(ratio - self._ui_scale) < 0.03:
            return
        self._ui_scale = ratio
        self._apply_styles()
        if hasattr(self, "centralWidget") and self.centralWidget() is not None:
            layout = self.centralWidget().layout()
            if layout is not None:
                margin = self._scaled(16)
                spacing = self._scaled(12)
                layout.setContentsMargins(margin, margin, margin, margin)
                layout.setSpacing(spacing)
        if hasattr(self, "output_input"):
            target_group = self.output_input.parentWidget()
            if target_group is not None and target_group.layout() is not None:
                target_layout = target_group.layout()
                if isinstance(target_layout, QGridLayout):
                    target_layout.setHorizontalSpacing(self._scaled(8))
                    target_layout.setVerticalSpacing(self._scaled(10))
                    for row in range(5):
                        target_layout.setRowMinimumHeight(row, self._scaled(42))
                    target_layout.setRowMinimumHeight(5, self._scaled(54))

    def _load_state(self) -> dict:
        if not APP_STATE_PATH.exists():
            return {}
        try:
            return json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save_state(self) -> None:
        payload = {
            "output_dir": self.output_input.text().strip(),
            "proxy_url": self.proxy_input.text().strip(),
            "recent_urls": self.recent_urls[:12],
            "auto_open_dir": self.auto_open_checkbox.isChecked(),
            "clear_logs": self.clear_log_checkbox.isChecked(),
            "show_all_formats": self.show_all_formats_checkbox.isChecked(),
            "download_engine_mode": self.download_engine_mode,
            "cookie_mode": self.cookie_mode,
            "cookie_file": self.cookie_file,
            "cookie_browser": self.cookie_browser,
        }
        APP_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _restore_state(self) -> None:
        default_dir = str(Path.home() / "Downloads" / "YouTubeDownloads")
        self.output_input.setText(self.state.get("output_dir", default_dir))
        self.proxy_input.setText(self.state.get("proxy_url", ""))
        self.auto_open_checkbox.setChecked(bool(self.state.get("auto_open_dir", False)))
        self.clear_log_checkbox.setChecked(bool(self.state.get("clear_logs", True)))
        self.show_all_formats_checkbox.setChecked(bool(self.state.get("show_all_formats", False)))
        self.recent_urls = [item for item in self.state.get("recent_urls", []) if isinstance(item, str)]
        self.url_combo.addItems(self.recent_urls)
        self._apply_proxy_setting(log_change=False)
        self._apply_engine_and_cookie_settings(log_change=False)

    def _set_busy(self, busy: bool, text: str) -> None:
        for widget in (
            self.parse_button,
            self.refresh_button,
            self.download_best_av_button,
            self.download_best_audio_button,
            self.copy_title_button,
            self.open_source_button,
            self.advanced_settings_button,
        ):
            if widget is not None:
                widget.setEnabled(not busy)
        self.statusBar().showMessage(text)
        self.runtime_value_label.setText(text)
        lowered = text.lower()
        if "完成" in text:
            color = "#15803d"
        elif "失败" in text:
            color = "#b91c1c"
        elif "下载" in text:
            color = "#1d4ed8"
        else:
            color = "#b45309"
        self.runtime_value_label.setStyleSheet(f"color: {color}; font-weight: 700;")

    def _apply_proxy_setting(self, log_change: bool = True) -> None:
        proxy = self.proxy_input.text().strip()
        self.service.set_proxy(proxy)
        self._update_environment_summary()
        if not log_change:
            return
        if proxy:
            self._append_log(f"已启用代理：{proxy}")
            self.statusBar().showMessage("代理已更新")
        else:
            self._append_log("已关闭代理，后续解析与下载将直连。")
            self.statusBar().showMessage("代理已关闭")
        self._save_state()

    def _handle_proxy_changed(self) -> None:
        self._apply_proxy_setting(log_change=True)

    def _apply_engine_and_cookie_settings(self, log_change: bool = True) -> None:
        self.service.set_download_engine_mode(self.download_engine_mode)
        self.service.set_cookie_source(self.cookie_mode, self.cookie_file, self.cookie_browser)
        self._update_environment_summary()
        if not log_change:
            return
        summary = self.service.get_environment_summary()
        self._append_log(
            "下载链路已更新："
            f"模式={summary['engine_mode']}，实际引擎={summary['effective_engine']}，"
            f"Cookie={summary['cookie']}"
        )
        self._save_state()

    def _update_environment_summary(self) -> None:
        summary = self.service.get_environment_summary()
        short_text = (
            f"下载链路：{summary['effective_engine']} | 代理：{summary['proxy']} | Cookie：{summary['cookie']}"
        )
        if self.engine_context_label is not None:
            self.engine_context_label.setText(
                f"{short_text}\n"
                f"yt-dlp：{summary['yt_dlp_path']} | FFmpeg：{summary['ffmpeg_path']} | JS：{summary['js_runtime']}"
            )
        if self.download_context_label is not None:
            self.download_context_label.setText(short_text)

    def _active_download_status_label(self) -> QLabel | None:
        if self.active_download_tab_index == 0:
            return self.video_download_status_label
        if self.active_download_tab_index == 1:
            return self.audio_download_status_label
        return self.av_download_status_label

    def open_advanced_settings(self) -> None:
        dialog = AdvancedSettingsDialog(
            {
                "download_engine_mode": self.download_engine_mode,
                "cookie_mode": self.cookie_mode,
                "cookie_file": self.cookie_file,
                "cookie_browser": self.cookie_browser,
            },
            self.service.get_environment_summary(),
            self,
        )
        dialog.test_proxy_button.clicked.connect(lambda: self._run_proxy_diagnosis(dialog))
        dialog.test_cookie_button.clicked.connect(lambda: self._run_cookie_diagnosis(dialog))
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        self.download_engine_mode = values["download_engine_mode"]
        self.cookie_mode = values["cookie_mode"]
        self.cookie_file = values["cookie_file"]
        self.cookie_browser = values["cookie_browser"]
        self._apply_engine_and_cookie_settings(log_change=True)

    def _run_proxy_diagnosis(self, dialog: AdvancedSettingsDialog | None = None) -> None:
        self._apply_proxy_setting(log_change=False)
        result = self.service.diagnose_proxy()
        self._append_log(result["message"])
        self.statusBar().showMessage(result["message"])
        if dialog is not None:
            dialog.set_environment_summary(self.service.get_environment_summary())
        icon = QMessageBox.Information if result["ok"] else QMessageBox.Warning
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle("代理诊断")
        box.setText(str(result["message"]))
        box.exec()

    def _run_cookie_diagnosis(self, dialog: AdvancedSettingsDialog | None = None) -> None:
        if dialog is not None:
            values = dialog.values()
            self.service.set_cookie_source(values["cookie_mode"], values["cookie_file"], values["cookie_browser"])
        result = self.service.diagnose_cookie()
        self._append_log(result["message"])
        self.statusBar().showMessage(result["message"])
        if dialog is not None:
            dialog.set_environment_summary(self.service.get_environment_summary())
        icon = QMessageBox.Information if result["ok"] else QMessageBox.Warning
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle("Cookie 诊断")
        box.setText(str(result["message"]))
        box.exec()
        self.service.set_cookie_source(self.cookie_mode, self.cookie_file, self.cookie_browser)

    def _refresh_download_controls(self) -> None:
        active = self.service.has_active_download() or (
            self.current_task_kind == "download" and self.worker_thread is not None
        )
        paused = self.service.is_download_paused()
        if self.pause_resume_button is not None:
            self.pause_resume_button.setEnabled(active)
            self.pause_resume_button.setText("继续下载" if paused else "暂停下载")
        if self.cancel_download_button is not None:
            self.cancel_download_button.setEnabled(active)

    def toggle_pause_download(self) -> None:
        if not self.service.has_active_download():
            return
        label = self._active_download_status_label()
        if self.service.is_download_paused():
            if self.service.resume_download():
                self._set_download_status(label, "下载状态：继续下载中...", active=True)
        else:
            if self.service.pause_download():
                self._set_download_status(label, "下载状态：已暂停", active=True)
        self._refresh_download_controls()

    def cancel_active_download(self) -> None:
        if not self.service.has_active_download():
            return
        if QMessageBox.question(self, "取消下载", "当前下载任务还在进行，确定要取消吗？") != QMessageBox.Yes:
            return
        if self.service.cancel_download():
            self._set_download_status(self._active_download_status_label(), "下载状态：正在取消...", active=True)
        self._refresh_download_controls()

    def _append_log(self, text: str) -> None:
        self.log_output.appendPlainText(text)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def _format_elapsed(self, seconds: int) -> str:
        minutes, secs = divmod(max(0, seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _start_activity(self, kind: str) -> None:
        self.current_task_kind = kind
        self.current_task_started_at = time.monotonic()
        self.last_heartbeat_second = -1
        self.elapsed_label.setText("00:00")
        self.activity_timer.start()

    def _stop_activity(self) -> str:
        elapsed = int(time.monotonic() - self.current_task_started_at) if self.current_task_started_at else 0
        self.activity_timer.stop()
        self.current_task_kind = ""
        self.current_task_started_at = 0.0
        self.last_heartbeat_second = -1
        return self._format_elapsed(elapsed)

    def _tick_activity(self) -> None:
        if not self.current_task_started_at:
            return
        elapsed = int(time.monotonic() - self.current_task_started_at)
        elapsed_text = self._format_elapsed(elapsed)
        self.elapsed_label.setText(elapsed_text)

        if self.current_task_kind == "basic_parse":
            self.progress_note.setText(f"正在读取基础信息，已用时 {elapsed_text}")
        elif self.current_task_kind == "full_parse":
            self.progress_note.setText(f"正在加载常用格式，已用时 {elapsed_text}")
            if elapsed > 0 and elapsed % 2 == 0 and elapsed != self.last_heartbeat_second:
                self.last_heartbeat_second = elapsed
                self._append_log(f"常用格式仍在加载中，已用时 {elapsed_text} ...")
        elif self.current_task_kind == "download" and elapsed != self.last_heartbeat_second:
            self.last_heartbeat_second = elapsed
            self.statusBar().showMessage(f"正在下载，已用时 {elapsed_text}")

    def parse_url(self) -> None:
        self._apply_proxy_setting(log_change=False)
        self._apply_engine_and_cookie_settings(log_change=False)
        url = self.url_combo.currentText().strip()
        if not url:
            QMessageBox.warning(self, "缺少链接", "请先输入 YouTube 视频链接。")
            return
        if self.worker_thread is not None:
            return

        self.current_url = url
        self.current_video = None
        self.video_options = []
        self.audio_options = []
        self.av_options = []
        self._reset_tables()
        if self.clear_log_checkbox.isChecked():
            self.log_output.clear()

        mode_text = "全部格式" if self.show_all_formats_checkbox.isChecked() else "常用格式"
        self._append_log(f"开始解析：{url}")
        self._append_log(f"当前模式：{mode_text}")
        self._set_busy(True, "第 1 阶段：基础信息")
        self.progress_bar.setRange(0, 0)
        self.progress_note.setText("正在读取标题、作者和时长...")
        self._start_activity("basic_parse")
        self.pending_stage = "basic"
        self._start_worker(lambda: self.service.inspect_basic(url, self._emit_status), on_finished=self._handle_basic_parsed)

    def _start_worker(self, fn, *args, on_finished) -> None:
        self.worker_thread = QThread(self)
        self.worker = Worker(lambda: fn(*args))
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(on_finished)
        self.worker.finished.connect(self._cleanup_worker)
        self.worker.error.connect(self._handle_error)
        self.worker.error.connect(self._cleanup_worker)
        self.worker.progress.connect(self._handle_progress)
        self.worker.status.connect(self._handle_status)
        self.worker_thread.start()
        self._refresh_download_controls()

    def _cleanup_worker(self, *_args) -> None:
        if self.worker_thread is None:
            return
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.worker_thread = None
        self.worker = None
        self._refresh_download_controls()
        if self.close_after_cancel and not self.service.has_active_download():
            self.close_after_cancel = False
            QTimer.singleShot(0, self.close)

    def _handle_basic_parsed(self, data: object) -> None:
        info = data
        assert isinstance(info, VideoMetadata)
        self.current_video = info
        self._fill_basic_info(info)
        elapsed_text = self._stop_activity()
        self.elapsed_label.setText(elapsed_text)
        self._append_log(f"基础信息已返回，用时 {elapsed_text}。开始第 2 阶段：常用格式。")
        self._set_busy(True, "第 2 阶段：常用格式")
        self.progress_bar.setRange(0, 0)
        self.progress_note.setText("正在加载常用格式列表...")
        self._start_activity("full_parse")
        self.pending_stage = "full"
        QTimer.singleShot(
            0,
            lambda: self._start_worker(lambda: self.service.inspect(
                self.current_url,
                self.show_all_formats_checkbox.isChecked(),
                self._emit_status,
            ), on_finished=self._handle_full_parsed),
        )

    def _handle_full_parsed(self, data: object) -> None:
        info = data
        assert isinstance(info, VideoMetadata)
        self.current_video = info
        self.video_options = info.video_options
        self.audio_options = info.audio_options
        self.av_options = info.av_options
        self._push_recent_url(self.current_url)
        self._fill_basic_info(info)
        self.video_count_label.setText(f"单独视频：{len(self.video_options)}")
        self.audio_count_label.setText(f"单独音频：{len(self.audio_options)}")
        self.av_count_label.setText(f"音视频清晰度：{len(self.av_options)}")

        self._populate_video_table()
        self._populate_audio_table()
        self._populate_av_table()
        self._apply_filter()

        elapsed_text = self._stop_activity()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.elapsed_label.setText(elapsed_text)
        self.progress_note.setText(f"常用格式已加载，用时 {elapsed_text}")
        self._append_log(f"常用格式已加载完成，用时 {elapsed_text}。")
        self._set_busy(False, "解析完成")
        self._save_state()

    def _fill_basic_info(self, info: VideoMetadata) -> None:
        summary = self.service.get_environment_summary()
        self.title_label.setText(f"标题：{info.title}")
        self.meta_label.setText(f"作者：{info.uploader}    时长：{info.duration_text}")
        self.summary_box.setPlainText(
            "\n".join(
                [
                    f"视频标题：{info.title}",
                    f"发布者：{info.uploader}",
                    f"视频链接：{info.webpage_url or self.current_url}",
                    f"当前下载引擎：{summary['effective_engine']}",
                    f"当前代理：{summary['proxy']}",
                    f"当前 Cookie：{summary['cookie']}",
                    "当前界面可在“显示全部格式”打开后展示完整清单。",
                    "音视频标签页只展示可选视频清晰度，真正的音频匹配会在点击下载后自动完成。",
                ]
            )
        )

    def _handle_show_all_toggled(self, checked: bool) -> None:
        self._save_state()
        if not self.current_url or self.worker_thread is not None:
            return
        mode_text = "全部格式" if checked else "常用格式"
        self._append_log(f"已切换到{mode_text}模式。")
        self.parse_url()

    def _handle_error(self, message: str) -> None:
        message = self.service.explain_error(message)
        elapsed_text = self._stop_activity()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.elapsed_label.setText(elapsed_text)
        if "下载已取消" in message:
            self.progress_note.setText(f"下载已取消，用时 {elapsed_text}")
            self._append_log(f"下载已取消。用时 {elapsed_text}。")
            self._set_download_status(self._active_download_status_label(), "下载状态：已取消", active=False)
            self._set_busy(False, "下载已取消")
            self._refresh_download_controls()
            return
        self.progress_note.setText(f"执行失败，用时 {elapsed_text}")
        self._append_log(f"错误：{message}")
        self._set_busy(False, "执行失败")
        self._refresh_download_controls()
        QMessageBox.critical(self, "出错了", message)

    def _handle_status(self, text: str) -> None:
        if self.current_task_kind == "download":
            preparation_markers = ("准备", "检查", "启动", "创建", "发起请求", "连接", "等待", "响应")
            if any(marker in text for marker in preparation_markers):
                self.progress_bar.setRange(0, 0)
        self.statusBar().showMessage(text)
        self.progress_note.setText(text)
        self._append_log(text)

    def _handle_progress(self, percent: float, text: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(percent))
        self.progress_note.setText(text)
        self._append_log(text)
        self._set_download_status(self._active_download_status_label(), f"下载状态：{text}", active=True)

    def _reset_tables(self) -> None:
        for table in (self.video_table, self.audio_table, self.av_table):
            table.setRowCount(0)
        self.video_count_label.setText("单独视频：0")
        self.audio_count_label.setText("单独音频：0")
        self.av_count_label.setText("音视频清晰度：0")
        self._set_selection_hint(self.video_selection_hint, "当前选择：未选择")
        self._set_selection_hint(self.audio_selection_hint, "当前选择：未选择")
        self._set_selection_hint(self.av_selection_hint, "当前选择：未选择")
        self._set_download_status(self.video_download_status_label, "下载状态：待开始")
        self._set_download_status(self.audio_download_status_label, "下载状态：待开始")
        self._set_download_status(self.av_download_status_label, "下载状态：待开始")

    def _push_recent_url(self, url: str) -> None:
        self.recent_urls = [item for item in self.recent_urls if item != url]
        self.recent_urls.insert(0, url)
        self.recent_urls = self.recent_urls[:12]
        self.url_combo.clear()
        self.url_combo.addItems(self.recent_urls)
        self.url_combo.setCurrentText(url)

    def _populate_video_table(self) -> None:
        self.video_table.setRowCount(len(self.video_options))
        for row, item in enumerate(self.video_options):
            self._set_item(self.video_table, row, 0, item.format_id, item)
            self._set_item(self.video_table, row, 1, item.ext, item)
            self._set_item(self.video_table, row, 2, item.label, item)

    def _populate_audio_table(self) -> None:
        self.audio_table.setRowCount(len(self.audio_options))
        for row, item in enumerate(self.audio_options):
            self._set_item(self.audio_table, row, 0, item.format_id, item)
            self._set_item(self.audio_table, row, 1, item.ext, item)
            self._set_item(self.audio_table, row, 2, item.label, item)

    def _populate_av_table(self) -> None:
        self.av_table.setRowCount(len(self.av_options))
        for row, item in enumerate(self.av_options):
            match = next((option for option in self.video_options if option.format_id == item.video_format_id), None)
            self._set_item(self.av_table, row, 0, item.video_format_id, match)
            self._set_item(self.av_table, row, 1, item.label, match)
            self._set_item(self.av_table, row, 2, item.output_ext.upper(), match)

    def _highlight_brush(self, option: FormatOption | None) -> QBrush | None:
        if not self.show_all_formats_checkbox.isChecked() or option is None:
            return None

        is_hdr = "hdr" in (option.label or "").lower() or "hdr" in (option.format_note or "").lower()
        is_high_fps = bool(option.fps and option.fps >= 50)
        is_high_res = bool(option.height and option.height >= 1440)

        if is_hdr:
            return QBrush(QColor("#b91c1c"))
        if is_high_res:
            return QBrush(QColor("#7c3aed"))
        if is_high_fps:
            return QBrush(QColor("#0f766e"))
        return None

    def _set_item(self, table: QTableWidget, row: int, column: int, text: str, option: FormatOption | None = None) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() ^ Qt.ItemIsEditable)
        brush = self._highlight_brush(option)
        if brush is not None:
            item.setForeground(brush)
            item.setToolTip("高亮：HDR / 高分辨率 / 60fps 重点格式")
        table.setItem(row, column, item)

    def _apply_filter(self) -> None:
        keyword = self.filter_input.text().strip().lower()
        tables = [
            (self.video_table, self.video_options, "video"),
            (self.audio_table, self.audio_options, "audio"),
            (self.av_table, self.av_options, "av"),
        ]
        for table, items, filter_key in tables:
            for row in range(table.rowCount()):
                row_text = " ".join(
                    table.item(row, col).text().lower()
                    for col in range(table.columnCount())
                    if table.item(row, col) is not None
                )
                keyword_ok = not keyword or keyword in row_text
                filter_ok = row < len(items) and self._match_table_filter(filter_key, items[row])
                table.setRowHidden(row, not (keyword_ok and filter_ok))

    def _selected_row(self, table: QTableWidget) -> int | None:
        selected = table.selectionModel().selectedRows()
        if not selected:
            return None
        return selected[0].row()

    def _set_selection_hint(self, label: QLabel | None, text: str, color: str | None = None, active: bool = False) -> None:
        if label is not None:
            label.setText(text)
            if active:
                text_color = color or "#1d4ed8"
                label.setStyleSheet(
                    "color: %s; background: #dbeafe; border: 1px solid #93c5fd; "
                    "border-radius: 10px; padding: 8px 10px; font-weight: 700;" % text_color
                )
            else:
                label.setStyleSheet(
                    "color: #475569; background: #f8fafc; border: 1px solid #dbe3f0; "
                    "border-radius: 10px; padding: 8px 10px; font-weight: 400;"
                )

    def _set_download_status(self, label: QLabel | None, text: str, active: bool = False) -> None:
        if label is None:
            return
        label.setText(text)
        if active:
            label.setStyleSheet(
                "color: #1d4ed8; background: #dbeafe; border: 1px solid #93c5fd; "
                "border-radius: 10px; padding: 8px 10px; font-weight: 700;"
            )
        else:
            label.setStyleSheet(
                "color: #334155; background: #f8fafc; border: 1px solid #dbe3f0; "
                "border-radius: 10px; padding: 8px 10px; font-weight: 400;"
            )

    def _size_text(self, size: int | None) -> str:
        if not size:
            return "?"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        idx = 0
        while value >= 1024 and idx < len(units) - 1:
            value /= 1024
            idx += 1
        return f"{value:.1f}{units[idx]}"

    def _current_filter_key(self) -> str:
        index = self.tabs.currentIndex()
        return "video" if index == 0 else "audio" if index == 1 else "av"

    def _update_filter_count_label(self) -> None:
        current = self.table_filters.get(self._current_filter_key(), {})
        active_count = sum(1 for value in current.values() if value and value != "全部")
        self.filter_count_label.setText(f"已启用 {active_count} 项")

    def _option_for_av_plan(self, plan: AVPlan) -> FormatOption | None:
        return next((option for option in self.video_options if option.format_id == plan.video_format_id), None)

    def _current_filter_items(self) -> list:
        key = self._current_filter_key()
        if key == "video":
            return self.video_options
        if key == "audio":
            return self.audio_options
        return self.av_options

    def _unique_choice_strings(self, values: list[str]) -> list[str]:
        cleaned = [value for value in values if value and value != "?"]
        return ["全部"] + list(dict.fromkeys(cleaned))

    def _build_filter_fields(self) -> list[tuple[str, str, list[str]]]:
        key = self._current_filter_key()
        fields: list[tuple[str, str, list[str]]] = []
        if key in {"video", "av"}:
            options = self.video_options if key == "video" else [item for item in (self._option_for_av_plan(plan) for plan in self.av_options) if item]
            fields.append(("resolution", "分辨率", self._unique_choice_strings([f"{option.height}p" for option in options if option.height])))
            fields.append(("ext", "视频格式", self._unique_choice_strings([option.ext for option in options])))
            fields.append(("fps", "帧率", self._unique_choice_strings([f"{int(option.fps)}fps" for option in options if option.fps])))
            fields.append(("bitrate", "码率", self._unique_choice_strings([f"{int(option.tbr)}kbps" for option in options if option.tbr])))
            fields.append(("quality", "画质", self._unique_choice_strings([option.format_note or option.label.split(' | ')[0] for option in options])))
            fields.append(("size", "视频大小", self._unique_choice_strings([self._size_text(option.filesize) for option in options if option.filesize])))
        else:
            options = self.audio_options
            fields.append(("ext", "音频格式", self._unique_choice_strings([option.ext for option in options])))
            fields.append(("bitrate", "码率", self._unique_choice_strings([f"{int(option.abr or option.tbr)}kbps" for option in options if option.abr or option.tbr])))
            fields.append(("quality", "音质", self._unique_choice_strings([option.format_note or option.ext for option in options])))
            fields.append(("size", "文件大小", self._unique_choice_strings([self._size_text(option.filesize) for option in options if option.filesize])))
        return [field for field in fields if len(field[2]) > 1]

    def open_current_filter_dialog(self) -> None:
        fields = self._build_filter_fields()
        key = self._current_filter_key()
        if not fields:
            QMessageBox.information(self, "暂无筛选项", "当前列表还没有可筛选的数据，请先解析。")
            return
        title_map = {"video": "筛选单独视频", "audio": "筛选单独音频", "av": "筛选音视频清晰度"}
        dialog = FilterDialog(title_map[key], fields, self.table_filters.get(key, {}), self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.table_filters[key] = dialog.values()
        self._update_filter_count_label()
        active_text = "，".join(f"{label}={value}" for field_key, label, _choices in fields for value in [self.table_filters[key].get(field_key, "全部")] if value != "全部")
        if active_text:
            self._append_log(f"已应用筛选：{active_text}")
        else:
            self._append_log("已清空当前页筛选。")
        self._apply_filter()

    def _match_table_filter(self, filter_key: str, item) -> bool:
        rules = self.table_filters.get(filter_key, {})
        if not rules:
            return True

        option = item if isinstance(item, FormatOption) else self._option_for_av_plan(item)
        if option is None:
            return True

        checks = {
            "resolution": f"{option.height}p" if option.height else "全部",
            "ext": option.ext,
            "fps": f"{int(option.fps)}fps" if option.fps else "全部",
            "bitrate": f"{int(option.abr or option.tbr)}kbps" if (option.abr or option.tbr) else "全部",
            "quality": option.format_note or option.label.split(" | ")[0],
            "size": self._size_text(option.filesize),
        }
        for key, value in rules.items():
            if value and value != "全部" and checks.get(key) != value:
                return False
        return True

    def _shorten(self, text: str, limit: int = 88) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[:limit - 1]}..."

    def _update_video_selection_hint(self) -> None:
        row = self._selected_row(self.video_table)
        if row is None:
            self._set_selection_hint(self.video_selection_hint, "当前选择：未选择")
            return
        item = self.video_options[row]
        brush = self._highlight_brush(item)
        color = brush.color().name() if brush is not None else "#1d4ed8"
        self._set_selection_hint(
            self.video_selection_hint,
            f"当前选择：{item.format_id} | {self._shorten(item.label)}",
            color=color,
            active=True,
        )

    def _update_audio_selection_hint(self) -> None:
        row = self._selected_row(self.audio_table)
        if row is None:
            self._set_selection_hint(self.audio_selection_hint, "当前选择：未选择")
            return
        item = self.audio_options[row]
        self._set_selection_hint(
            self.audio_selection_hint,
            f"当前选择：{item.format_id} | {self._shorten(item.label)}",
            color="#1d4ed8",
            active=True,
        )

    def _update_av_selection_hint(self) -> None:
        row = self._selected_row(self.av_table)
        if row is None:
            self._set_selection_hint(self.av_selection_hint, "当前选择：未选择")
            return
        item = self.av_options[row]
        match = next((option for option in self.video_options if option.format_id == item.video_format_id), None)
        brush = self._highlight_brush(match)
        color = brush.color().name() if brush is not None else "#1d4ed8"
        self._set_selection_hint(
            self.av_selection_hint,
            f"当前选择：{item.video_format_id} | {self._shorten(item.label)}",
            color=color,
            active=True,
        )

    def _confirm_download(self, kind_text: str, selected_text: str, color: str | None = None) -> bool:
        if not self.current_video:
            return False
        accent = color or "#1d4ed8"
        summary = self.service.get_environment_summary()
        safe_title = self.current_video.title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_selected = selected_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_dir = self.output_input.text().strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_engine = summary["effective_engine"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_proxy = summary["proxy"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_cookie = summary["cookie"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        box = QMessageBox(self)
        box.setWindowTitle("确认下载")
        box.setIcon(QMessageBox.Question)
        box.setText(f"确定下载当前{kind_text}吗？")
        box.setInformativeText(
            f"""
            <div style="line-height:1.6">
              <div><b>视频标题：</b>{safe_title}</div>
              <div style="margin-top:8px;"><b>选择参数：</b></div>
              <div style="
                  margin-top:6px;
                  padding:10px 12px;
                  border-radius:10px;
                  border:1px solid #93c5fd;
                  background:#dbeafe;
                  color:{accent};
                  font-weight:700;
              ">{safe_selected}</div>
              <div style="margin-top:10px;"><b>保存目录：</b>{safe_dir}</div>
              <div style="margin-top:10px;"><b>下载引擎：</b>{safe_engine}</div>
              <div><b>代理：</b>{safe_proxy}</div>
              <div><b>Cookie：</b>{safe_cookie}</div>
            </div>
            """
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        yes_button = box.button(QMessageBox.Yes)
        cancel_button = box.button(QMessageBox.Cancel)
        if yes_button is not None:
            yes_button.setText("确定下载")
        if cancel_button is not None:
            cancel_button.setText("取消")
        box.setDefaultButton(QMessageBox.Yes)
        return box.exec() == QMessageBox.Yes

    def download_selected_video(self) -> None:
        row = self._selected_row(self.video_table)
        if row is None:
            QMessageBox.information(self, "请选择格式", "请先在“单独视频”里选择一个常用格式。")
            return
        option = self.video_options[row]
        confirm_text = f"{option.format_id} | {option.label}"
        brush = self._highlight_brush(option)
        color = brush.color().name() if brush is not None else "#1d4ed8"
        if not self._confirm_download("纯视频", confirm_text, color):
            return
        self._set_download_status(self.video_download_status_label, "下载状态：正在准备下载...", active=True)
        self._run_download(
            f"准备下载纯视频：{option.label}",
            lambda: self.service.download_video_only(
                self.current_url,
                option.format_id,
                self.output_input.text().strip(),
                self._emit_progress,
                self._emit_status,
            ),
        )

    def download_selected_audio(self) -> None:
        row = self._selected_row(self.audio_table)
        if row is None:
            QMessageBox.information(self, "请选择格式", "请先在“单独音频”里选择一个常用格式。")
            return
        option = self.audio_options[row]
        confirm_text = f"{option.format_id} | {option.label}"
        if not self._confirm_download("纯音频", confirm_text, "#1d4ed8"):
            return
        self._set_download_status(self.audio_download_status_label, "下载状态：正在准备下载...", active=True)
        self._run_download(
            f"准备下载纯音频：{option.label}",
            lambda: self.service.download_audio_only(
                self.current_url,
                option.format_id,
                self.output_input.text().strip(),
                self._emit_progress,
                self._emit_status,
            ),
        )

    def download_selected_av(self) -> None:
        row = self._selected_row(self.av_table)
        if row is None:
            QMessageBox.information(self, "请选择清晰度", "请先在“音视频”里选择一个视频清晰度。")
            return
        plan = self.av_options[row]
        match = next((option for option in self.video_options if option.format_id == plan.video_format_id), None)
        try:
            video_option, audio_option, merge_output_format = self.service.choose_av_download_plan(
                plan.video_format_id,
                self.video_options,
                self.audio_options,
            )
        except Exception as exc:
            QMessageBox.critical(self, "出错了", str(exc))
            return
        confirm_text = (
            f"视频 {video_option.format_id} ({video_option.ext}) + "
            f"音频 {audio_option.format_id} ({audio_option.ext}) -> {merge_output_format.upper()}\n"
            f"{plan.label}"
        )
        brush = self._highlight_brush(match)
        color = brush.color().name() if brush is not None else "#1d4ed8"
        if not self._confirm_download("音视频", confirm_text, color):
            return
        self._set_download_status(self.av_download_status_label, "下载状态：正在准备下载...", active=True)
        self._run_download(
            f"准备下载音视频：视频 {video_option.format_id} + 音频 {audio_option.format_id} -> {merge_output_format.upper()}",
            lambda: self.service.download_av(
                self.current_url,
                video_option.format_id,
                audio_option.format_id,
                merge_output_format,
                self.output_input.text().strip(),
                self._emit_progress,
                self._emit_status,
            ),
        )

    def download_best_av(self) -> None:
        if not self.av_options:
            QMessageBox.information(self, "没有方案", "请先解析一个视频。")
            return
        self.av_table.selectRow(0)
        self.download_selected_av()

    def download_best_audio(self) -> None:
        if not self.audio_options:
            QMessageBox.information(self, "没有音频", "请先解析一个视频。")
            return
        self.audio_table.selectRow(0)
        self.download_selected_audio()

    def _run_download(self, status_text: str, action) -> None:
        self._apply_proxy_setting(log_change=False)
        self._apply_engine_and_cookie_settings(log_change=False)
        if not self.current_url:
            QMessageBox.warning(self, "请先解析", "请先解析一个视频链接。")
            return
        if self.worker_thread is not None:
            return

        output_dir = self.output_input.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "缺少目录", "请先选择保存目录。")
            return

        self._append_log(status_text)
        summary = self.service.get_environment_summary()
        self._append_log(
            f"下载上下文：引擎={summary['effective_engine']} | 代理={summary['proxy']} | Cookie={summary['cookie']}"
        )
        self.active_download_tab_index = self.tabs.currentIndex()
        self._set_busy(True, "正在下载")
        self.progress_bar.setRange(0, 0)
        self.progress_note.setText(status_text)
        self._start_activity("download")
        self._save_state()
        self._start_worker(action, on_finished=self._handle_download_done)
        self._refresh_download_controls()

    def _handle_download_done(self, _data: object) -> None:
        elapsed_text = self._stop_activity()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.elapsed_label.setText(elapsed_text)
        self.progress_note.setText(f"下载完成，用时 {elapsed_text}")
        self._append_log(f"下载完成，文件已保存。用时 {elapsed_text}。")
        self._set_download_status(self._active_download_status_label(), f"下载状态：下载完成，用时 {elapsed_text}", active=False)
        self._set_busy(False, "下载完成")
        self._refresh_download_controls()
        if self.auto_open_checkbox.isChecked():
            self.open_output_dir()
        QMessageBox.information(self, "完成", "下载完成，文件已经保存到目标目录。")

    def _emit_progress(self, percent: float, text: str) -> None:
        if self.worker is not None:
            self.worker.progress.emit(percent, text)

    def _emit_status(self, text: str) -> None:
        if self.worker is not None:
            self.worker.status.emit(text)

    def choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.output_input.text().strip() or str(Path.home()))
        if directory:
            self.output_input.setText(directory)
            self._save_state()

    def open_output_dir(self) -> None:
        directory = self.output_input.text().strip()
        if not directory:
            return
        Path(directory).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(Path(directory).as_uri())

    def open_source_page(self) -> None:
        url = self.current_video.webpage_url if self.current_video else self.url_combo.currentText().strip()
        if url:
            webbrowser.open(url)

    def copy_title(self) -> None:
        if not self.current_video:
            QMessageBox.information(self, "暂无标题", "请先解析一个视频。")
            return
        QApplication.clipboard().setText(self.current_video.title)
        self.statusBar().showMessage("标题已复制到剪贴板")

    def clear_recent_urls(self) -> None:
        self.recent_urls.clear()
        self.url_combo.clear()
        self._save_state()
        self._append_log("最近链接记录已清空。")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.service.has_active_download():
            answer = QMessageBox.warning(
                self,
                "下载未完成",
                "视频还在下载中，是否取消当前下载并关闭窗口？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.close_after_cancel = True
            self.service.cancel_download()
            event.ignore()
            return
        self._save_state()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_ui_scale()


def main() -> None:
    app = QApplication(sys.argv)
    window = DownloaderWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
