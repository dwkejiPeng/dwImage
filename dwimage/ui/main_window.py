from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image
from PySide6.QtCore import QMimeData, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QAbstractScrollArea,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)
from PySide6.QtCore import QUrl

from ..models import (
    ApiProfile,
    AttachmentBatchMode,
    GenerationRequest,
    ImageGenerationApiMode,
    ImageOutputFormat,
    ImageQuality,
    ImageRecord,
    ImageRecordStatus,
    PromptOptimizationProfile,
    PromptOptimizationProtocol,
    SizePreset,
)
from ..services import MintImageService
from ..storage import APP_DIR, AppStorage
from ..prompt_opt import PromptOptimizationDirection


SIZE_PRESET_LABELS = {
    SizePreset.AUTO: "自动",
    SizePreset.SQUARE_1K: "方图 1K",
    SizePreset.POSTER_PORTRAIT: "海报竖版",
    SizePreset.POSTER_LANDSCAPE: "海报横版",
    SizePreset.STORY_916: "竖屏 9:16",
    SizePreset.VIDEO_169: "横屏 16:9",
    SizePreset.WIDE_2K: "宽屏 2K",
    SizePreset.PORTRAIT_2K: "竖图 2K",
    SizePreset.SQUARE_2K: "方图 2K",
    SizePreset.PORTRAIT_4K: "竖图 4K",
    SizePreset.WIDE_4K: "宽屏 4K",
    SizePreset.CUSTOM: "自定义",
}

QUALITY_LABELS = {
    ImageQuality.AUTO: "自动",
    ImageQuality.LOW: "低",
    ImageQuality.MEDIUM: "中",
    ImageQuality.HIGH: "高",
}

FORMAT_LABELS = {
    ImageOutputFormat.PNG: "PNG",
    ImageOutputFormat.JPEG: "JPEG",
    ImageOutputFormat.WEBP: "WEBP",
}

ATTACHMENT_MODE_LABELS = {
    AttachmentBatchMode.COMBINED: "合并参考图一起生成",
    AttachmentBatchMode.SPLIT_PER_IMAGE: "每张参考图单独生成",
}

API_MODE_LABELS = {
    ImageGenerationApiMode.IMAGES: "Images API",
    ImageGenerationApiMode.RESPONSES: "Responses API",
}

PROMPT_PROTOCOL_LABELS = {
    PromptOptimizationProtocol.OPENAI_CHAT: "OpenAI Chat",
    PromptOptimizationProtocol.OPENAI_RESPONSES: "OpenAI Responses",
    PromptOptimizationProtocol.CLAUDE: "Claude",
    PromptOptimizationProtocol.GEMINI: "Gemini",
}

PROMPT_DIRECTION_LABELS = {
    PromptOptimizationDirection.STRENGTHEN: "强化",
    PromptOptimizationDirection.EDGE_EXPLORE: "探索边界",
    PromptOptimizationDirection.STRENGTHEN_TO_ENGLISH: "强化后转英文",
    PromptOptimizationDirection.CLASSICAL_CHINESE: "转为文言文",
    PromptOptimizationDirection.POETIC: "诗意强化",
}

STATUS_META = {
    ImageRecordStatus.PENDING: ("排队中", "#64748b"),
    ImageRecordStatus.LOADING: ("生成中", "#0f766e"),
    ImageRecordStatus.DONE: ("已完成", "#15803d"),
    ImageRecordStatus.ERROR: ("失败", "#dc2626"),
    ImageRecordStatus.CANCELLED: ("已取消", "#7c3aed"),
}


def as_api_mode(value: object) -> ImageGenerationApiMode:
    return value if isinstance(value, ImageGenerationApiMode) else ImageGenerationApiMode(str(value))


def as_prompt_protocol(value: object) -> PromptOptimizationProtocol:
    return value if isinstance(value, PromptOptimizationProtocol) else PromptOptimizationProtocol(str(value))


def as_size_preset(value: object) -> SizePreset:
    return value if isinstance(value, SizePreset) else SizePreset(str(value))


def as_quality(value: object) -> ImageQuality:
    return value if isinstance(value, ImageQuality) else ImageQuality(str(value))


def as_output_format(value: object) -> ImageOutputFormat:
    return value if isinstance(value, ImageOutputFormat) else ImageOutputFormat(str(value))


def as_attachment_mode(value: object) -> AttachmentBatchMode:
    return value if isinstance(value, AttachmentBatchMode) else AttachmentBatchMode(str(value))


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class TaskWorker(QRunnable):
    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            try:
                result = self.fn(*self.args, progress_callback=self.signals.progress.emit, **self.kwargs)
            except TypeError as exc:
                if "progress_callback" not in str(exc):
                    raise
                result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # pragma: no cover
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.storage = AppStorage()
        self.service = MintImageService(self.storage)
        self.settings = self.storage.load_settings()
        self.records = self.storage.load_records()
        self.thread_pool = QThreadPool.globalInstance()
        self.favorite_folders, self.favorite_memberships = self.storage.load_favorite_snapshot()
        self.current_generation_summary: dict[str, Any] | None = None
        self.last_generation_errors: list[str] = []
        self.loading_frame = 0
        self.loading_timer = QTimer(self)
        self.loading_timer.setInterval(380)
        self.loading_timer.timeout.connect(self.advance_loading_frame)
        self._build_ui()
        self._apply_window_style()
        self._load_state_to_ui()
        self.refresh_all_views()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        files = self._extract_image_files(event.mimeData())
        if files:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        files = self._extract_image_files(event.mimeData())
        if files:
            self.add_attachments(files)
            self.status_label.setText(f"已拖入 {len(files)} 张图片。")
            event.acceptProposedAction()
        else:
            event.ignore()

    def _build_ui(self) -> None:
        self.setWindowTitle("dwImage")
        self.resize(1360, 820)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        paste_action = QAction("粘贴图片", self)
        paste_action.triggered.connect(self.handle_paste)
        toolbar.addAction(paste_action)

        open_data_action = QAction("打开数据目录", self)
        open_data_action.triggered.connect(self.open_data_dir)
        toolbar.addAction(open_data_action)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.generate_tab = QWidget()
        self.history_tab = QWidget()
        self.favorites_tab = QWidget()
        self.logs_tab = QWidget()
        self.settings_tab = QWidget()

        self.tabs.addTab(self.generate_tab, "生成")
        self.tabs.addTab(self.history_tab, "历史")
        self.tabs.addTab(self.favorites_tab, "收藏")
        self.tabs.addTab(self.logs_tab, "日志")
        self.tabs.addTab(self.settings_tab, "设置")

        self._build_generate_tab()
        self._build_history_tab()
        self._build_favorites_tab()
        self._build_logs_tab()
        self._build_settings_tab()

    def _apply_window_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f4f7fb;
            }
            QGroupBox {
                background: white;
                border: 1px solid #d8e1eb;
                border-radius: 12px;
                margin-top: 10px;
                padding: 8px 10px 10px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: #0f172a;
            }
            QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QLineEdit, QComboBox, QSpinBox {
                background: white;
                border: 1px solid #d8e1eb;
                border-radius: 12px;
                padding: 4px 10px;
                selection-background-color: #dbeafe;
            }
            QComboBox, QSpinBox, QLineEdit {
                min-height: 30px;
            }
            QComboBox {
                padding-right: 30px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #475569;
                margin-right: 10px;
            }
            QComboBox QAbstractItemView {
                background: white;
                border: 1px solid #d8e1eb;
                border-radius: 10px;
                padding: 4px;
                selection-background-color: #ccfbf1;
            }
            QSpinBox {
                padding-right: 22px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 18px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow {
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #475569;
            }
            QSpinBox::down-arrow {
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #475569;
            }
            QPushButton {
                background: #e2e8f0;
                color: #0f172a;
                border: none;
                border-radius: 10px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #cbd5e1;
            }
            QPushButton:disabled {
                background: #e5e7eb;
                color: #94a3b8;
            }
            QProgressBar {
                border: 1px solid #d8e1eb;
                border-radius: 8px;
                background: #eef2f7;
                text-align: center;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background: #0f766e;
                border-radius: 7px;
            }
            QHeaderView::section {
                background: #eff6ff;
                border: none;
                border-bottom: 1px solid #d8e1eb;
                padding: 8px;
                font-weight: 600;
            }
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                background: #e2e8f0;
                border-radius: 10px;
                padding: 8px 14px;
                margin-right: 6px;
                color: #334155;
            }
            QTabBar::tab:selected {
                background: #0f766e;
                color: white;
            }
            QToolBar {
                background: transparent;
                border: none;
                spacing: 8px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QTableWidget {
                gridline-color: #e2e8f0;
                alternate-background-color: #f8fafc;
            }
            QScrollBar:vertical {
                width: 10px;
                background: transparent;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                border-radius: 5px;
                min-height: 24px;
            }
            """
        )

    def _build_generate_tab(self) -> None:
        layout = QHBoxLayout(self.generate_tab)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter)

        form_side = QWidget()
        form_layout = QVBoxLayout(form_side)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(8)

        prompt_box = QGroupBox("提示词")
        prompt_layout = QVBoxLayout(prompt_box)
        prompt_head = QLabel("把想法写清楚，系统会按当前配置直接生成；如果上传参考图，会自动切到编辑/重绘流程。")
        prompt_head.setWordWrap(True)
        prompt_head.setStyleSheet("QLabel { color: #475569; font-weight: 400; }")
        prompt_head.setMaximumHeight(24)
        prompt_layout.addWidget(prompt_head)
        prompt_mode_row = QHBoxLayout()
        self.prompt_mode_combo = QComboBox()
        self.prompt_mode_combo.addItem("单提示词", "single")
        self.prompt_mode_combo.addItem("批量提示词", "batch")
        self.prompt_mode_combo.currentIndexChanged.connect(self.on_prompt_mode_changed)
        self.prompt_count_hint = QLabel("批量模式下每行一个提示词。")
        self.prompt_count_hint.setStyleSheet("QLabel { color: #64748b; }")
        prompt_mode_row.addWidget(self.prompt_mode_combo, 0)
        prompt_mode_row.addWidget(self.prompt_count_hint, 1)
        prompt_layout.addLayout(prompt_mode_row)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("输入提示词。支持纯文本出图，也支持上传参考图后做编辑/重绘。")
        self.prompt_edit.setMinimumHeight(72)
        self.prompt_edit.setMaximumHeight(88)
        prompt_layout.addWidget(self.prompt_edit)

        optimize_row = QHBoxLayout()
        self.prompt_direction_combo = QComboBox()
        for direction, label in PROMPT_DIRECTION_LABELS.items():
            self.prompt_direction_combo.addItem(label, direction)
        self.optimize_button = QPushButton("优化提示词")
        self.optimize_button.clicked.connect(self.optimize_prompt)
        optimize_row.addWidget(self.prompt_direction_combo, 1)
        optimize_row.addWidget(self.optimize_button, 1)
        prompt_layout.addLayout(optimize_row)
        form_layout.addWidget(prompt_box)

        config_box = QGroupBox("生成配置")
        config_grid = QGridLayout(config_box)
        config_grid.setContentsMargins(10, 10, 10, 10)
        config_grid.setHorizontalSpacing(10)
        config_grid.setVerticalSpacing(8)

        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
        config_grid.addWidget(self._build_field_block("接口配置", self.profile_combo), 0, 0, 1, 4)

        self.size_combo = QComboBox()
        for item, label in SIZE_PRESET_LABELS.items():
            self.size_combo.addItem(label, item)
        self.size_combo.currentIndexChanged.connect(self.on_size_changed)
        self.size_combo.currentIndexChanged.connect(self.update_request_summary)
        config_grid.addWidget(self._build_field_block("尺寸", self.size_combo), 1, 0)

        custom_size_row = QHBoxLayout()
        custom_size_row.setContentsMargins(0, 0, 0, 0)
        custom_size_row.setSpacing(6)
        self.custom_width_spin = QSpinBox()
        self.custom_width_spin.setRange(1, 8192)
        self.custom_width_spin.setValue(1024)
        self.custom_width_spin.setMaximumWidth(110)
        self.custom_height_spin = QSpinBox()
        self.custom_height_spin.setRange(1, 8192)
        self.custom_height_spin.setValue(1024)
        self.custom_height_spin.setMaximumWidth(110)
        custom_size_row.addWidget(self.custom_width_spin)
        custom_size_row.addWidget(QLabel("x"))
        custom_size_row.addWidget(self.custom_height_spin)
        custom_size_row.addStretch(1)
        custom_size_widget = QWidget()
        custom_size_widget.setLayout(custom_size_row)
        config_grid.addWidget(self._build_field_block("自定义尺寸", custom_size_widget), 1, 1)

        self.quality_combo = QComboBox()
        for item, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, item)
        self.quality_combo.currentIndexChanged.connect(self.update_request_summary)
        config_grid.addWidget(self._build_field_block("质量", self.quality_combo), 1, 2)

        self.format_combo = QComboBox()
        for item, label in FORMAT_LABELS.items():
            self.format_combo.addItem(label, item)
        self.format_combo.currentIndexChanged.connect(self.update_request_summary)
        config_grid.addWidget(self._build_field_block("输出格式", self.format_combo), 1, 3)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 8)
        self.count_spin.setValue(1)
        self.count_spin.valueChanged.connect(self.update_request_summary)
        self.count_spin.setMaximumWidth(120)
        config_grid.addWidget(self._build_field_block("每次生成张数", self.count_spin), 2, 0)

        self.attachment_mode_combo = QComboBox()
        for item, label in ATTACHMENT_MODE_LABELS.items():
            self.attachment_mode_combo.addItem(label, item)
        self.attachment_mode_combo.currentIndexChanged.connect(self.update_request_summary)
        config_grid.addWidget(self._build_field_block("多图处理方式", self.attachment_mode_combo), 2, 1, 1, 3)
        form_layout.addWidget(config_box)

        self.request_summary_label = QLabel("当前任务：1 个请求，纯文生图。")
        self.request_summary_label.setWordWrap(True)
        self.request_summary_label.setStyleSheet("QLabel { color: #334155; background: #ecfeff; border-radius: 10px; padding: 7px 10px; }")
        form_layout.addWidget(self.request_summary_label)

        upload_box = QGroupBox("参考图")
        upload_layout = QVBoxLayout(upload_box)
        upload_layout.setSpacing(8)
        upload_tip = QLabel("图片可以直接拖入窗口任意位置，也可以用下面按钮选择或粘贴。")
        upload_tip.setStyleSheet("QLabel { color: #64748b; }")
        upload_layout.addWidget(upload_tip)

        attach_action_row = QHBoxLayout()
        add_button = QPushButton("选择图片")
        add_button.clicked.connect(self.choose_attachments)
        paste_button = QPushButton("粘贴")
        paste_button.clicked.connect(self.handle_paste)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self.clear_attachments)
        attach_action_row.addWidget(add_button, 1)
        attach_action_row.addWidget(paste_button, 1)
        attach_action_row.addWidget(clear_button, 1)
        upload_layout.addLayout(attach_action_row)

        self.attachment_summary_label = QLabel("当前没有参考图")
        self.attachment_summary_label.setStyleSheet("QLabel { color: #64748b; }")
        upload_layout.addWidget(self.attachment_summary_label)

        self.attachment_list = QListWidget()
        self.attachment_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.attachment_list.setMaximumHeight(42)
        upload_layout.addWidget(self.attachment_list)
        form_layout.addWidget(upload_box)

        action_row = QHBoxLayout()
        self.favorite_checkbox = QCheckBox("生成结果自动加入默认收藏")
        self.generate_button = QPushButton("开始生成")
        self.generate_button.clicked.connect(self.submit_generation)
        action_row.addWidget(self.favorite_checkbox)
        action_row.addStretch(1)
        action_row.addWidget(self.generate_button)
        form_layout.addLayout(action_row)

        self.status_label = QLabel("就绪")
        form_layout.addWidget(self.status_label)
        form_layout.addStretch(1)

        preview_side = QWidget()
        preview_layout = QVBoxLayout(preview_side)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        preview_layout.setSpacing(10)
        preview_box = QGroupBox("预览")
        preview_inner = QVBoxLayout(preview_box)
        self.preview_label = QLabel("暂无图片")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(240)
        self.preview_label.setStyleSheet("QLabel { background: #0f172a; color: white; border-radius: 12px; font-size: 15px; }")
        preview_inner.addWidget(self.preview_label)

        self.preview_meta = QTextEdit()
        self.preview_meta.setReadOnly(True)
        self.preview_meta.setPlaceholderText("生成结果的模型、尺寸、耗时和错误信息会显示在这里。")
        self.preview_meta.setMaximumHeight(118)
        preview_inner.addWidget(self.preview_meta)
        preview_layout.addWidget(preview_box, 1)

        status_box = QGroupBox("生成状态")
        status_layout = QVBoxLayout(status_box)

        self.generation_progress = QProgressBar()
        self.generation_progress.setRange(0, 1)
        self.generation_progress.setValue(0)
        self.generation_progress.setFormat("等待开始")
        status_layout.addWidget(self.generation_progress)

        self.generation_mode_label = QLabel("等待任务")
        self.generation_mode_label.setStyleSheet("QLabel { color: #0f172a; font-weight: 700; }")
        status_layout.addWidget(self.generation_mode_label)

        self.generation_stage_label = QLabel("尚未开始生成")
        self.generation_stage_label.setWordWrap(True)
        status_layout.addWidget(self.generation_stage_label)

        self.generation_error_box = QTextEdit()
        self.generation_error_box.setReadOnly(True)
        self.generation_error_box.setPlaceholderText("如果生成失败，错误原因会显示在这里。")
        self.generation_error_box.setMaximumHeight(74)
        status_layout.addWidget(self.generation_error_box)

        generation_action_row = QHBoxLayout()
        self.retry_failed_button = QPushButton("重试失败项")
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.clicked.connect(self.retry_failed_tasks)
        self.open_latest_button = QPushButton("打开最新结果")
        self.open_latest_button.setEnabled(False)
        self.open_latest_button.clicked.connect(self.open_latest_result)
        generation_action_row.addWidget(self.retry_failed_button)
        generation_action_row.addWidget(self.open_latest_button)
        generation_action_row.addStretch(1)
        status_layout.addLayout(generation_action_row)

        preview_layout.addWidget(status_box, 0)

        splitter.addWidget(form_side)
        splitter.addWidget(preview_side)
        splitter.setSizes([760, 700])

    def _build_history_tab(self) -> None:
        layout = QVBoxLayout(self.history_tab)
        self.history_table = QTableWidget(0, 8)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "状态", "模型", "尺寸", "格式", "来源图", "结果图", "提示词"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.history_table.itemSelectionChanged.connect(self.on_history_selection_changed)
        self.history_table.itemDoubleClicked.connect(self.on_history_double_clicked)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self.open_history_context_menu)
        layout.addWidget(self.history_table)

    def _build_favorites_tab(self) -> None:
        layout = QHBoxLayout(self.favorites_tab)

        left = QVBoxLayout()
        folder_row = QHBoxLayout()
        self.favorite_folder_combo = QComboBox()
        self.favorite_folder_combo.currentIndexChanged.connect(self.refresh_favorites_table)
        new_folder_button = QPushButton("新建收藏夹")
        new_folder_button.clicked.connect(self.create_favorite_folder)
        folder_row.addWidget(self.favorite_folder_combo)
        folder_row.addWidget(new_folder_button)
        left.addLayout(folder_row)

        self.favorites_table = QTableWidget(0, 5)
        self.favorites_table.setHorizontalHeaderLabels(["时间", "状态", "模型", "结果图", "提示词"])
        self.favorites_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.favorites_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.favorites_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.favorites_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.favorites_table.setAlternatingRowColors(True)
        self.favorites_table.itemSelectionChanged.connect(self.on_favorites_selection_changed)
        self.favorites_table.itemDoubleClicked.connect(self.on_favorites_double_clicked)
        self.favorites_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.favorites_table.customContextMenuRequested.connect(self.open_favorites_context_menu)
        left.addWidget(self.favorites_table)

        right = QVBoxLayout()
        self.favorite_preview = QLabel("暂无收藏预览")
        self.favorite_preview.setAlignment(Qt.AlignCenter)
        self.favorite_preview.setMinimumSize(360, 360)
        self.favorite_preview.setStyleSheet("QLabel { background: #0f172a; color: white; border-radius: 12px; }")
        right.addWidget(self.favorite_preview)
        self.favorite_meta = QTextEdit()
        self.favorite_meta.setReadOnly(True)
        right.addWidget(self.favorite_meta)

        left_widget = QWidget()
        left_widget.setLayout(left)
        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([900, 500])
        layout.addWidget(splitter)

    def _build_logs_tab(self) -> None:
        layout = QVBoxLayout(self.logs_tab)
        row = QHBoxLayout()
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_logs_view)
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(self.clear_logs)
        row.addWidget(refresh_button)
        row.addWidget(clear_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.logs_table = QTableWidget(0, 3)
        self.logs_table.setHorizontalHeaderLabels(["时间", "级别", "标题"])
        self.logs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.logs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.logs_table.setAlternatingRowColors(True)
        self.logs_table.itemSelectionChanged.connect(self.on_log_selection_changed)
        layout.addWidget(self.logs_table)

        self.log_details = QTextEdit()
        self.log_details.setReadOnly(True)
        layout.addWidget(self.log_details)

    def _build_settings_tab(self) -> None:
        layout = QHBoxLayout(self.settings_tab)

        left = QVBoxLayout()
        profile_box = QGroupBox("出图接口")
        profile_layout = QFormLayout(profile_box)
        self.settings_profile_selector = QComboBox()
        self.settings_profile_selector.currentIndexChanged.connect(self.on_settings_profile_changed)
        self.settings_profile_selector.setMinimumWidth(220)
        profile_selector_row = QHBoxLayout()
        add_profile_button = QPushButton("新增")
        add_profile_button.clicked.connect(self.create_api_profile)
        remove_profile_button = QPushButton("删除")
        remove_profile_button.clicked.connect(self.delete_api_profile)
        profile_selector_row.addWidget(self.settings_profile_selector, 1)
        profile_selector_row.addWidget(add_profile_button)
        profile_selector_row.addWidget(remove_profile_button)
        selector_widget = QWidget()
        selector_widget.setLayout(profile_selector_row)
        profile_layout.addRow("配置列表", selector_widget)
        self.profile_name_edit = QLineEdit()
        self.profile_base_url_edit = QLineEdit()
        self.profile_api_key_edit = QLineEdit()
        self.profile_api_key_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.profile_model_edit = QLineEdit()
        self.profile_mode_combo = QComboBox()
        for item, label in API_MODE_LABELS.items():
            self.profile_mode_combo.addItem(label, item)
        profile_layout.addRow("名称", self.profile_name_edit)
        profile_layout.addRow("Base URL", self.profile_base_url_edit)
        profile_layout.addRow("API Key", self.profile_api_key_edit)
        profile_layout.addRow("模型", self.profile_model_edit)
        profile_layout.addRow("接口模式", self.profile_mode_combo)
        left.addWidget(profile_box)

        prompt_box = QGroupBox("提示词优化")
        prompt_layout = QFormLayout(prompt_box)
        self.prompt_profile_name_edit = QLineEdit()
        self.prompt_profile_base_url_edit = QLineEdit()
        self.prompt_profile_api_key_edit = QLineEdit()
        self.prompt_profile_api_key_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.prompt_profile_model_edit = QLineEdit()
        self.prompt_protocol_combo = QComboBox()
        for item, label in PROMPT_PROTOCOL_LABELS.items():
            self.prompt_protocol_combo.addItem(label, item)
        prompt_layout.addRow("名称", self.prompt_profile_name_edit)
        prompt_layout.addRow("Base URL", self.prompt_profile_base_url_edit)
        prompt_layout.addRow("API Key", self.prompt_profile_api_key_edit)
        prompt_layout.addRow("模型", self.prompt_profile_model_edit)
        prompt_layout.addRow("协议", self.prompt_protocol_combo)
        left.addWidget(prompt_box)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(30, 3600)
        timeout_box = QGroupBox("全局")
        timeout_layout = QFormLayout(timeout_box)
        timeout_layout.addRow("请求超时秒数", self.timeout_spin)
        left.addWidget(timeout_box)

        save_button = QPushButton("保存设置")
        save_button.clicked.connect(self.save_settings)
        left.addWidget(save_button)
        left.addStretch(1)

        right = QVBoxLayout()
        note = QTextEdit()
        note.setReadOnly(True)
        note.setPlainText(
            "说明：\n"
            "1. 如果未上传参考图，则走文生图。\n"
            "2. 上传 1 张或多张参考图时，可切换合并模式或每图独立模式。\n"
            "3. 粘贴支持外部复制的图片以及资源管理器复制的图片文件。"
        )
        right.addWidget(note)

        left_widget = QWidget()
        left_widget.setLayout(left)
        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([760, 540])
        layout.addWidget(splitter)

    def _load_state_to_ui(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.settings.profiles:
            self.profile_combo.addItem(profile.name, profile.id)
        active_index = max(0, self.profile_combo.findData(self.settings.active_profile_id))
        self.profile_combo.setCurrentIndex(active_index)
        self.profile_combo.blockSignals(False)

        self.settings_profile_selector.blockSignals(True)
        self.settings_profile_selector.clear()
        for profile in self.settings.profiles:
            self.settings_profile_selector.addItem(profile.name, profile.id)
        settings_index = max(0, self.settings_profile_selector.findData(self.settings.active_profile_id))
        self.settings_profile_selector.setCurrentIndex(settings_index)
        self.settings_profile_selector.blockSignals(False)

        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(self.settings.last_size_preset)))
        self.custom_width_spin.setValue(max(1, self.settings.last_custom_width or 1024))
        self.custom_height_spin.setValue(max(1, self.settings.last_custom_height or 1024))
        self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(self.settings.last_quality)))
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(self.settings.last_output_format)))
        self.timeout_spin.setValue(self.settings.request_timeout_seconds)
        self._load_profile_details()
        self._load_prompt_profile_details()
        self.on_size_changed()
        self._update_attachment_summary()
        self.update_request_summary()

    def refresh_all_views(self) -> None:
        self.records = self.storage.load_records()
        self.favorite_folders, self.favorite_memberships = self.storage.load_favorite_snapshot()
        self.refresh_history_table()
        self.refresh_favorite_folders()
        self.refresh_favorites_table()
        self.refresh_logs_view()

    def choose_attachments(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        self.add_attachments(files)

    def add_attachments(self, paths: list[str]) -> None:
        normalized = self.service.normalize_image_files(paths)
        existing = {self.attachment_list.item(i).data(Qt.UserRole) for i in range(self.attachment_list.count())}
        added = 0
        for path in normalized:
            if path in existing:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            self.attachment_list.addItem(item)
            existing.add(path)
            added += 1
        self.status_label.setText(f"已添加 {added} 张参考图，当前共 {self.attachment_list.count()} 张。")
        self._update_attachment_summary()
        self.update_request_summary()

    def clear_attachments(self) -> None:
        self.attachment_list.clear()
        self.status_label.setText("参考图已清空。")
        self._update_attachment_summary()
        self.update_request_summary()

    def handle_paste(self) -> None:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        if mime.hasUrls():
            files = [url.toLocalFile() for url in mime.urls() if url.toLocalFile()]
            self.add_attachments(files)
            return

        image = clipboard.image()
        if not image.isNull():
            pil_image = self._qimage_to_pil(image)
            path = self.service.save_clipboard_image(pil_image)
            self.add_attachments([path])
            return

        QMessageBox.information(self, "提示", "剪贴板里没有可用的图片或图片文件。")

    def submit_generation(self) -> None:
        prompts = self._collect_prompts()
        if not prompts:
            QMessageBox.warning(self, "提示", "请先输入提示词。")
            return

        requests = self._collect_requests()
        primary_request = requests[0]
        self._persist_last_generation_settings(primary_request)
        self.generate_button.setEnabled(False)
        self.optimize_button.setEnabled(False)
        self._reset_generation_feedback()
        self._set_generation_mode_hint(primary_request, len(requests))
        total_expected_tasks = sum(item.expected_task_count for item in requests)
        self.status_label.setText(f"正在提交，预计任务数：{total_expected_tasks}")
        self.generation_stage_label.setText("正在创建任务...")
        self.generation_progress.setRange(0, max(1, total_expected_tasks))
        self.generation_progress.setValue(0)
        self.generation_progress.setFormat(f"0 / {total_expected_tasks}")

        worker = TaskWorker(self.service.submit_generation_batch, requests, self.settings)
        worker.signals.progress.connect(self.on_generation_progress)
        worker.signals.finished.connect(self.on_generation_finished)
        worker.signals.failed.connect(self.on_background_error)
        self.thread_pool.start(worker)

    def optimize_prompt(self) -> None:
        prompts = self._collect_prompts()
        if not prompts:
            QMessageBox.warning(self, "提示", "请先输入提示词。")
            return
        if len(prompts) > 1:
            QMessageBox.information(self, "提示", "批量提示词模式下，请先切回单提示词后再使用优化功能。")
            return
        prompt = prompts[0]
        if self.settings.active_prompt_optimization_profile is None:
            QMessageBox.warning(self, "提示", "请先在设置页配置提示词优化资料。")
            return

        self.optimize_button.setEnabled(False)
        self.status_label.setText("正在优化提示词...")
        direction = self.prompt_direction_combo.currentData()
        worker = TaskWorker(self.service.optimize_prompt, prompt, direction, self.settings)
        worker.signals.finished.connect(self.on_prompt_optimized)
        worker.signals.failed.connect(self.on_background_error)
        self.thread_pool.start(worker)

    def on_generation_progress(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type")
        if event_type == "batch_started":
            self.loading_timer.start()
            total_tasks = int(payload.get("total_tasks", 1))
            total_groups = int(payload.get("total_groups", 1))
            split_mode = bool(payload.get("split_mode"))
            if total_tasks <= 1:
                self.generation_mode_label.setText("单图生成中")
            elif split_mode and total_groups > 1:
                self.generation_mode_label.setText("多组生成中")
            else:
                self.generation_mode_label.setText("多张生成中")
            self.generation_stage_label.setText(f"共 {total_tasks} 个任务，{total_groups} 组，已经开始生成。")
            return

        if event_type == "task_started":
            task_index = int(payload.get("task_index", 1))
            total_tasks = int(payload.get("total_tasks", 1))
            group_index = int(payload.get("group_index", 1))
            total_groups = int(payload.get("total_groups", 1))
            copy_index = int(payload.get("copy_index", 1))
            copies_in_group = int(payload.get("copies_in_group", 1))
            source_image_path = payload.get("source_image_path")
            source_name = Path(source_image_path).name if source_image_path else "当前任务"
            if total_groups > 1:
                self.generation_stage_label.setText(
                    f"正在处理第 {group_index}/{total_groups} 组，第 {copy_index}/{copies_in_group} 张：{source_name}"
                )
            elif total_tasks > 1:
                self.generation_stage_label.setText(f"正在处理第 {task_index}/{total_tasks} 张：{source_name}")
            else:
                self.generation_stage_label.setText(f"正在生成：{source_name}")
            return

        if event_type == "task_finished":
            task_index = int(payload.get("task_index", 1))
            total_tasks = int(payload.get("total_tasks", 1))
            succeeded_tasks = int(payload.get("succeeded_tasks", 0))
            failed_tasks = int(payload.get("failed_tasks", 0))
            status = str(payload.get("status", ""))
            error_message = payload.get("error_message")
            self.generation_progress.setValue(task_index)
            self.generation_progress.setFormat(f"{task_index} / {total_tasks}")
            self.status_label.setText(f"生成中：已完成 {task_index}/{total_tasks}，成功 {succeeded_tasks}，失败 {failed_tasks}")
            if status == ImageRecordStatus.ERROR.value and error_message:
                self.last_generation_errors.append(str(error_message))
                self.generation_error_box.setPlainText("\n\n".join(self.last_generation_errors[-8:]))
            return

        if event_type == "batch_finished":
            total_tasks = int(payload.get("total_tasks", 0))
            self.generation_progress.setValue(total_tasks)
            self.generation_progress.setFormat(f"{total_tasks} / {total_tasks}")

    def on_generation_finished(self, result: dict[str, Any]) -> None:
        self.generate_button.setEnabled(True)
        self.optimize_button.setEnabled(True)
        self.loading_timer.stop()
        self.current_generation_summary = result
        records = list(result.get("records", []))
        total_tasks = int(result.get("total_tasks", len(records)))
        succeeded_tasks = int(result.get("succeeded_tasks", 0))
        failed_tasks = int(result.get("failed_tasks", 0))
        self.retry_failed_button.setEnabled(failed_tasks > 0)
        self.open_latest_button.setEnabled(any(record.result_image_path for record in records))
        self.status_label.setText(f"生成完成：成功 {succeeded_tasks}，失败 {failed_tasks}")
        if failed_tasks == 0:
            self.generation_stage_label.setText(f"{total_tasks} 个任务全部完成。")
            self.generation_error_box.clear()
        else:
            self.generation_stage_label.setText(f"共 {total_tasks} 个任务，成功 {succeeded_tasks} 个，失败 {failed_tasks} 个。")
            if not self.last_generation_errors:
                self.last_generation_errors = [record.error_message for record in records if record.error_message]
            self.generation_error_box.setPlainText("\n\n".join(self.last_generation_errors[-8:]))
        if self.favorite_checkbox.isChecked():
            for record in records:
                if record.status == ImageRecordStatus.DONE:
                    self.storage.add_record_to_folder("default", record.id)
        self.refresh_all_views()
        completed = next((record for record in reversed(records) if record.result_image_path), None)
        if completed:
            self.show_record_preview(completed)
        self.tabs.setCurrentWidget(self.history_tab)

    def on_prompt_optimized(self, prompt: str) -> None:
        self.optimize_button.setEnabled(True)
        self.prompt_edit.setPlainText(prompt)
        self.status_label.setText("提示词优化完成。")

    def on_background_error(self, message: str) -> None:
        self.generate_button.setEnabled(True)
        self.optimize_button.setEnabled(True)
        self.loading_timer.stop()
        self.retry_failed_button.setEnabled(False)
        self.status_label.setText("任务失败。")
        self.generation_stage_label.setText("后台任务已中断。")
        self.generation_error_box.setPlainText(message)
        QMessageBox.critical(self, "错误", message)
        self.refresh_all_views()

    def refresh_history_table(self) -> None:
        self.history_table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            status_text, status_color = STATUS_META.get(record.status, (record.status.value, "#475569"))
            values = [
                record.created_at.replace("T", " ")[:19],
                status_text,
                record.model,
                f"{record.width}x{record.height}" if record.width and record.height else "auto",
                record.output_format,
                str(len(record.source_attachment_paths)),
                Path(record.result_image_path).name if record.result_image_path else (record.result_image_url or ""),
                record.prompt,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, record.id)
                if column == 1:
                    item.setForeground(QColor("white"))
                    item.setBackground(QColor(status_color))
                    item.setTextAlignment(Qt.AlignCenter)
                if record.status == ImageRecordStatus.ERROR and column == 7:
                    item.setToolTip(record.error_message or "生成失败")
                self.history_table.setItem(row, column, item)

    def refresh_favorite_folders(self) -> None:
        current = self.favorite_folder_combo.currentData()
        self.favorite_folder_combo.blockSignals(True)
        self.favorite_folder_combo.clear()
        for folder in self.favorite_folders:
            self.favorite_folder_combo.addItem(folder.title, folder.id)
        if current is not None:
            index = self.favorite_folder_combo.findData(current)
            if index >= 0:
                self.favorite_folder_combo.setCurrentIndex(index)
        self.favorite_folder_combo.blockSignals(False)

    def refresh_favorites_table(self) -> None:
        folder_id = self.favorite_folder_combo.currentData()
        member_ids = {item.record_id for item in self.favorite_memberships if item.folder_id == folder_id}
        favorite_records = [record for record in self.records if record.id in member_ids]

        self.favorites_table.setRowCount(len(favorite_records))
        for row, record in enumerate(favorite_records):
            status_text, status_color = STATUS_META.get(record.status, (record.status.value, "#475569"))
            values = [
                record.created_at.replace("T", " ")[:19],
                status_text,
                record.model,
                Path(record.result_image_path).name if record.result_image_path else (record.result_image_url or ""),
                record.prompt,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, record.id)
                if column == 1:
                    item.setForeground(QColor("white"))
                    item.setBackground(QColor(status_color))
                    item.setTextAlignment(Qt.AlignCenter)
                self.favorites_table.setItem(row, column, item)

    def refresh_logs_view(self) -> None:
        logs = self.storage.load_logs()
        self.logs_table.setRowCount(len(logs))
        for row, entry in enumerate(logs):
            values = [entry.timestamp.replace("T", " ")[:19], entry.level.value, entry.title]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, entry.details)
                self.logs_table.setItem(row, column, item)
        if logs:
            self.logs_table.selectRow(len(logs) - 1)

    def clear_logs(self) -> None:
        self.storage.clear_logs()
        self.refresh_logs_view()
        self.log_details.clear()

    def on_history_selection_changed(self) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        record_id = rows[0].data(Qt.UserRole)
        record = self._find_record(record_id)
        if record:
            self.show_record_preview(record)

    def on_history_double_clicked(self) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        record = self._find_record(rows[0].data(Qt.UserRole))
        if record:
            self.open_record_image(record)

    def on_log_selection_changed(self) -> None:
        rows = self.logs_table.selectionModel().selectedRows()
        if not rows:
            return
        details = rows[0].data(Qt.UserRole)
        self.log_details.setPlainText(details or "")

    def on_favorites_selection_changed(self) -> None:
        rows = self.favorites_table.selectionModel().selectedRows()
        if not rows:
            return
        record_id = rows[0].data(Qt.UserRole)
        record = self._find_record(record_id)
        if record:
            self.show_record_preview(record, favorite_view=True)

    def on_favorites_double_clicked(self) -> None:
        rows = self.favorites_table.selectionModel().selectedRows()
        if not rows:
            return
        record = self._find_record(rows[0].data(Qt.UserRole))
        if record:
            self.open_record_image(record)

    def open_history_context_menu(self, pos) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        record_id = rows[0].data(Qt.UserRole)
        record = self._find_record(record_id)
        if not record:
            return

        menu = QMenu(self)
        open_image = menu.addAction("打开图片")
        open_folder = menu.addAction("打开所在目录")
        favorite = menu.addAction("加入默认收藏")
        retry_action = menu.addAction("重试此项")
        retry_action.setEnabled(record.can_retry)
        delete_action = menu.addAction("删除记录")
        action = menu.exec(self.history_table.viewport().mapToGlobal(pos))

        if action == open_image:
            self.open_record_image(record)
        elif action == open_folder:
            self.open_record_folder(record)
        elif action == favorite:
            self.storage.add_record_to_folder("default", record.id)
            self.refresh_all_views()
        elif action == retry_action:
            self._load_record_into_form(record)
        elif action == delete_action:
            self.storage.delete_record(record.id)
            self.refresh_all_views()

    def open_favorites_context_menu(self, pos) -> None:
        rows = self.favorites_table.selectionModel().selectedRows()
        if not rows:
            return
        record_id = rows[0].data(Qt.UserRole)
        folder_id = self.favorite_folder_combo.currentData()
        record = self._find_record(record_id)
        if not record or not folder_id:
            return

        menu = QMenu(self)
        open_image = menu.addAction("打开图片")
        remove_action = menu.addAction("移出当前收藏夹")
        action = menu.exec(self.favorites_table.viewport().mapToGlobal(pos))
        if action == open_image:
            self.open_record_image(record)
        elif action == remove_action:
            self.storage.remove_record_from_folder(folder_id, record.id)
            self.refresh_all_views()

    def show_record_preview(self, record: ImageRecord, favorite_view: bool = False) -> None:
        target_label = self.preview_label
        target_meta = self.preview_meta
        if favorite_view or self.tabs.currentWidget() == self.favorites_tab:
            target_label = self.favorite_preview
            target_meta = self.favorite_meta

        if record.result_image_path and os.path.exists(record.result_image_path):
            pixmap = QPixmap(record.result_image_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(target_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                target_label.setPixmap(scaled)
                target_label.setText("")
        else:
            target_label.setPixmap(QPixmap())
            target_label.setText("暂无本地图片")

        meta = [
            f"状态: {record.status.value}",
            f"模型: {record.model}",
            f"尺寸: {record.width} x {record.height}" if record.width and record.height else "尺寸: auto",
            f"输出: {record.output_format}",
            f"来源图: {len(record.source_attachment_paths)} 张",
            f"结果路径: {record.result_image_path or record.result_image_url or '-'}",
            f"耗时: {record.duration_ms or 0} ms",
            f"错误: {record.error_message or '-'}",
            "",
            record.prompt,
        ]
        target_meta.setPlainText("\n".join(meta))

    def open_record_image(self, record: ImageRecord) -> None:
        if record.result_image_path and os.path.exists(record.result_image_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(record.result_image_path))
            return
        if record.result_image_url:
            QDesktopServices.openUrl(QUrl(record.result_image_url))

    def open_record_folder(self, record: ImageRecord) -> None:
        target = record.result_image_path or APP_DIR
        if os.path.isfile(target):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))

    def create_favorite_folder(self) -> None:
        title, ok = QInputDialog.getText(self, "新建收藏夹", "收藏夹名称")
        if ok and title.strip():
            self.storage.create_folder(title.strip())
            self.refresh_all_views()

    def save_settings(self) -> None:
        current_profile = self.settings.active_profile
        updated_profile = ApiProfile(
            id=current_profile.id,
            name=self.profile_name_edit.text().strip() or current_profile.name,
            base_url=self.profile_base_url_edit.text().strip() or current_profile.base_url,
            api_key=self.profile_api_key_edit.text().strip(),
            model=self.profile_model_edit.text().strip() or current_profile.model,
            api_mode=as_api_mode(self.profile_mode_combo.currentData()),
        )
        self.settings.profiles = [updated_profile if item.id == current_profile.id else item for item in self.settings.profiles]
        self.settings.active_profile_id = updated_profile.id

        current_prompt_profile = self.settings.active_prompt_optimization_profile
        if current_prompt_profile is None:
            current_prompt_profile = PromptOptimizationProfile(
                id=str(uuid4()),
                name="Prompt Optimize",
                base_url="https://api.openai.com",
                api_key="",
                model="gpt-5.5",
                protocol=PromptOptimizationProtocol.OPENAI_RESPONSES,
            )
            self.settings.prompt_optimization_profiles = [current_prompt_profile]
            self.settings.active_prompt_optimization_profile_id = current_prompt_profile.id

        updated_prompt_profile = PromptOptimizationProfile(
            id=current_prompt_profile.id,
            name=self.prompt_profile_name_edit.text().strip() or current_prompt_profile.name,
            base_url=self.prompt_profile_base_url_edit.text().strip() or current_prompt_profile.base_url,
            api_key=self.prompt_profile_api_key_edit.text().strip(),
            model=self.prompt_profile_model_edit.text().strip() or current_prompt_profile.model,
            protocol=as_prompt_protocol(self.prompt_protocol_combo.currentData()),
        )
        self.settings.prompt_optimization_profiles = [
            updated_prompt_profile if item.id == current_prompt_profile.id else item
            for item in self.settings.prompt_optimization_profiles
        ]
        self.settings.active_prompt_optimization_profile_id = updated_prompt_profile.id
        self.settings.request_timeout_seconds = self.timeout_spin.value()
        self.storage.save_settings(self.settings)
        self._load_state_to_ui()
        QMessageBox.information(self, "提示", "设置已保存。")

    def on_settings_profile_changed(self) -> None:
        profile_id = self.settings_profile_selector.currentData()
        if profile_id:
            self.settings.active_profile_id = profile_id
            self._load_profile_details()
            index = self.profile_combo.findData(profile_id)
            if index >= 0:
                self.profile_combo.blockSignals(True)
                self.profile_combo.setCurrentIndex(index)
                self.profile_combo.blockSignals(False)

    def create_api_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "新增接口配置", "配置名称")
        if not ok or not name.strip():
            return
        profile = ApiProfile.initial()
        profile.name = name.strip()
        self.settings.profiles.append(profile)
        self.settings.active_profile_id = profile.id
        self.storage.save_settings(self.settings)
        self._load_state_to_ui()

    def delete_api_profile(self) -> None:
        if len(self.settings.profiles) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个接口配置。")
            return
        current_id = self.settings_profile_selector.currentData()
        current_name = self.settings_profile_selector.currentText()
        confirmed = QMessageBox.question(self, "删除接口配置", f"确定删除接口配置“{current_name}”吗？")
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.settings.profiles = [profile for profile in self.settings.profiles if profile.id != current_id]
        self.settings.active_profile_id = self.settings.profiles[0].id
        self.storage.save_settings(self.settings)
        self._load_state_to_ui()

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))

    def on_profile_changed(self) -> None:
        profile_id = self.profile_combo.currentData()
        if profile_id:
            self.settings.active_profile_id = profile_id
            self._load_profile_details()
            index = self.settings_profile_selector.findData(profile_id)
            if index >= 0:
                self.settings_profile_selector.blockSignals(True)
                self.settings_profile_selector.setCurrentIndex(index)
                self.settings_profile_selector.blockSignals(False)

    def on_size_changed(self) -> None:
        is_custom = as_size_preset(self.size_combo.currentData()) == SizePreset.CUSTOM
        self.custom_width_spin.setEnabled(is_custom)
        self.custom_height_spin.setEnabled(is_custom)

    def _load_profile_details(self) -> None:
        profile = self.settings.active_profile
        self.profile_name_edit.setText(profile.name)
        self.profile_base_url_edit.setText(profile.base_url)
        self.profile_api_key_edit.setText(profile.api_key)
        self.profile_model_edit.setText(profile.model)
        self.profile_mode_combo.setCurrentIndex(max(0, self.profile_mode_combo.findData(profile.api_mode)))

    def _load_prompt_profile_details(self) -> None:
        profile = self.settings.active_prompt_optimization_profile
        if profile is None:
            profile = PromptOptimizationProfile(
                id=str(uuid4()),
                name="Prompt Optimize",
                base_url="https://api.openai.com",
                api_key="",
                model="gpt-5.5",
                protocol=PromptOptimizationProtocol.OPENAI_RESPONSES,
            )
            self.settings.prompt_optimization_profiles = [profile]
            self.settings.active_prompt_optimization_profile_id = profile.id
        self.prompt_profile_name_edit.setText(profile.name)
        self.prompt_profile_base_url_edit.setText(profile.base_url)
        self.prompt_profile_api_key_edit.setText(profile.api_key)
        self.prompt_profile_model_edit.setText(profile.model)
        self.prompt_protocol_combo.setCurrentIndex(max(0, self.prompt_protocol_combo.findData(profile.protocol)))

    def _collect_prompts(self) -> list[str]:
        raw = self.prompt_edit.toPlainText()
        if self.prompt_mode_combo.currentData() == "batch":
            return [line.strip() for line in raw.splitlines() if line.strip()]
        prompt = raw.strip()
        return [prompt] if prompt else []

    def _collect_request(self, prompt: str | None = None) -> GenerationRequest:
        image_paths = [
            self.attachment_list.item(index).data(Qt.UserRole)
            for index in range(self.attachment_list.count())
        ]
        return GenerationRequest(
            prompt=(prompt if prompt is not None else self.prompt_edit.toPlainText().strip()),
            image_paths=image_paths,
            size_preset=as_size_preset(self.size_combo.currentData()),
            custom_width=self.custom_width_spin.value(),
            custom_height=self.custom_height_spin.value(),
            quality=as_quality(self.quality_combo.currentData()),
            output_format=as_output_format(self.format_combo.currentData()),
            count=self.count_spin.value(),
            api_profile_id=self.profile_combo.currentData(),
            attachment_batch_mode=as_attachment_mode(self.attachment_mode_combo.currentData()),
        )

    def _collect_requests(self) -> list[GenerationRequest]:
        return [self._collect_request(prompt=item) for item in self._collect_prompts()]

    def _persist_last_generation_settings(self, request: GenerationRequest) -> None:
        self.settings.last_size_preset = request.size_preset
        self.settings.last_custom_width = request.custom_width
        self.settings.last_custom_height = request.custom_height
        self.settings.last_quality = request.quality
        self.settings.last_output_format = request.output_format
        self.storage.save_settings(self.settings)

    def _build_field_block(self, label_text: str, field_widget: QWidget) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setStyleSheet("QLabel { color: #64748b; font-size: 12px; font-weight: 600; }")
        layout.addWidget(label)
        layout.addWidget(field_widget)
        return wrapper

    def _reset_generation_feedback(self) -> None:
        self.last_generation_errors = []
        self.current_generation_summary = None
        self.loading_timer.stop()
        self.loading_frame = 0
        self.generation_error_box.clear()
        self.generation_mode_label.setText("准备生成")
        self.generation_stage_label.setText("尚未开始生成")
        self.retry_failed_button.setEnabled(False)
        self.open_latest_button.setEnabled(False)
        self.generation_progress.setRange(0, 1)
        self.generation_progress.setValue(0)
        self.generation_progress.setFormat("等待开始")

    def _set_generation_mode_hint(self, request: GenerationRequest) -> None:
        if request.expected_task_count <= 1:
            text = "单图生成模式"
        elif request.should_split_attachments:
            text = f"多组生成模式：{len(request.image_paths)} 张参考图会分开生成"
        elif request.count > 1:
            text = f"多张生成模式：同一配置连续生成 {request.count} 张"
        else:
            text = f"批量生成模式：共 {request.expected_task_count} 个任务"
        self.generation_mode_label.setText(text)

    def advance_loading_frame(self) -> None:
        self.loading_frame = (self.loading_frame + 1) % 4
        dots = "." * (self.loading_frame + 1)
        if self.generate_button.isEnabled():
            return
        current = self.generation_stage_label.text().rstrip(".")
        if current:
            self.generation_stage_label.setText(f"{current}{dots}")

    def update_request_summary(self) -> None:
        requests = self._collect_requests()
        request = requests[0] if requests else self._collect_request(prompt="")
        image_count = len(request.image_paths)
        mode = "图生图/编辑" if image_count else "文生图"
        size_text = request.api_size or "自动尺寸"
        prompt_mode = "批量提示词" if self.prompt_mode_combo.currentData() == "batch" else "单提示词"
        prompt_count = len(requests) if requests else 0
        total_expected_tasks = sum(item.expected_task_count for item in requests) if requests else request.expected_task_count
        if request.should_split_attachments:
            batch_text = f"{image_count} 张参考图将拆成 {total_expected_tasks} 个任务"
        elif image_count:
            batch_text = f"{image_count} 张参考图将合并处理，共 {total_expected_tasks} 个任务"
        else:
            batch_text = f"共 {total_expected_tasks} 个任务"
        self.request_summary_label.setText(
            f"当前任务：{prompt_mode} {prompt_count} 条 | {mode} | {size_text} | {request.output_format.value.upper()} | {request.quality.value} | {batch_text}"
        )

    def _update_attachment_summary(self) -> None:
        count = self.attachment_list.count()
        if count == 0:
            self.attachment_summary_label.setText("当前没有参考图")
            return
        names = [
            Path(self.attachment_list.item(index).data(Qt.UserRole)).name
            for index in range(min(count, 2))
        ]
        suffix = "" if count <= 2 else f" 等 {count} 张"
        self.attachment_summary_label.setText("已添加：" + "、".join(names) + suffix)

    def on_prompt_mode_changed(self) -> None:
        is_batch = self.prompt_mode_combo.currentData() == "batch"
        if is_batch:
            self.prompt_edit.setPlaceholderText("批量模式：每行一个提示词。所有提示词共用当前配置和参考图。")
            self.prompt_count_hint.setText("批量模式下每行一个提示词。")
        else:
            self.prompt_edit.setPlaceholderText("输入提示词。支持纯文本出图，也支持上传参考图后做编辑/重绘。")
            self.prompt_count_hint.setText("单提示词模式。")
        self.update_request_summary()

    def open_latest_result(self) -> None:
        if not self.records:
            return
        latest = next((record for record in self.records if record.result_image_path), None)
        if latest:
            self.open_record_image(latest)

    def retry_failed_tasks(self) -> None:
        if not self.current_generation_summary:
            QMessageBox.information(self, "提示", "当前没有可重试的失败任务。")
            return
        failed_records = [record for record in self.current_generation_summary.get("records", []) if record.status == ImageRecordStatus.ERROR]
        if not failed_records:
            QMessageBox.information(self, "提示", "当前没有失败项。")
            return
        first = failed_records[0]
        request = GenerationRequest(
            prompt=first.prompt,
            image_paths=list(first.source_attachment_paths),
            size_preset=SizePreset.CUSTOM if first.width and first.height else SizePreset.AUTO,
            custom_width=max(1, first.width or 1024),
            custom_height=max(1, first.height or 1024),
            quality=as_quality(first.quality),
            output_format=as_output_format(first.output_format),
            count=len(failed_records),
            api_profile_id=first.api_profile_id,
            attachment_batch_mode=AttachmentBatchMode.COMBINED,
        )
        self._load_request_into_form(request)
        self.status_label.setText(f"已载入 {len(failed_records)} 个失败项的重试参数，请确认后重新生成。")

    def _load_request_into_form(self, request: GenerationRequest) -> None:
        self.prompt_edit.setPlainText(request.prompt)
        self.clear_attachments()
        if request.image_paths:
            self.add_attachments(request.image_paths)
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(request.size_preset)))
        self.custom_width_spin.setValue(request.custom_width)
        self.custom_height_spin.setValue(request.custom_height)
        self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(request.quality)))
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(request.output_format)))
        self.count_spin.setValue(request.count)
        self.attachment_mode_combo.setCurrentIndex(max(0, self.attachment_mode_combo.findData(request.attachment_batch_mode)))
        self.update_request_summary()
        self.tabs.setCurrentWidget(self.generate_tab)

    def _load_record_into_form(self, record: ImageRecord) -> None:
        request = GenerationRequest(
            prompt=record.prompt,
            image_paths=list(record.source_attachment_paths),
            size_preset=SizePreset.CUSTOM if record.width and record.height else SizePreset.AUTO,
            custom_width=max(1, record.width or 1024),
            custom_height=max(1, record.height or 1024),
            quality=as_quality(record.quality),
            output_format=as_output_format(record.output_format),
            count=1,
            api_profile_id=record.api_profile_id,
            attachment_batch_mode=AttachmentBatchMode.COMBINED,
        )
        self._load_request_into_form(request)

    def _find_record(self, record_id: str) -> ImageRecord | None:
        for record in self.records:
            if record.id == record_id:
                return record
        return None

    def _extract_image_files(self, mime: QMimeData) -> list[str]:
        files: list[str] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if local:
                files.append(local)
        return self.service.normalize_image_files(files)

    def _qimage_to_pil(self, image: QImage) -> Image.Image:
        converted = image.convertToFormat(QImage.Format_RGBA8888)
        width = converted.width()
        height = converted.height()
        bits = converted.bits()
        data = bits.tobytes(converted.sizeInBytes())
        return Image.frombytes("RGBA", (width, height), data)
