import os
import sys
import logging
import tempfile
from datetime import datetime
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QFileDialog, QLineEdit,
    QListWidget, QListWidgetItem, QTextEdit, QProgressBar,
    QGroupBox, QFormLayout, QCheckBox, QSplitter, QMessageBox,
    QApplication, QFrame, QScrollArea, QSizePolicy, QRadioButton,
    QButtonGroup, QAbstractItemView, QHeaderView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette, QPixmap, QPainter

from core.wechat_db import (
    WeChatDatabase, find_wechat_data_dirs
)
from core.exporter import ChatExporter, format_timestamp, get_msg_type_name, clean_content

LOG_FILE = os.path.join(os.path.expanduser("~"), "WeChatChatExporter_crash.log")


GREEN = "#07c160"
DARK_GREEN = "#06ad56"
LIGHT_GREEN = "#e8f8ef"
BG_COLOR = "#f7f7f8"
CARD_COLOR = "#ffffff"
TEXT_COLOR = "#333333"
SUB_TEXT = "#888888"
BORDER_COLOR = "#e5e5e5"


STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_COLOR};
}}
QTabWidget::pane {{
    border: none;
    background: {BG_COLOR};
}}
QTabBar::tab {{
    background: transparent;
    color: {SUB_TEXT};
    padding: 12px 30px;
    font-size: 14px;
    font-weight: 500;
    border: none;
    border-bottom: 2px solid transparent;
    margin: 0;
}}
QTabBar::tab:selected {{
    color: {GREEN};
    border-bottom: 2px solid {GREEN};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {DARK_GREEN};
}}
QGroupBox {{
    background: {CARD_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: 8px;
    margin-top: 14px;
    padding: 18px 16px 14px 16px;
    font-size: 13px;
    font-weight: 500;
    color: {TEXT_COLOR};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    top: 2px;
    padding: 0 6px;
    background: {CARD_COLOR};
    color: {TEXT_COLOR};
    font-size: 13px;
}}
QPushButton {{
    background: {GREEN};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 9px 22px;
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {DARK_GREEN};
}}
QPushButton:pressed {{
    background: #05a048;
}}
QPushButton:disabled {{
    background: #c0c0c0;
    color: #fff;
}}
QPushButton#secondaryBtn {{
    background: transparent;
    color: {GREEN};
    border: 1px solid {GREEN};
}}
QPushButton#secondaryBtn:hover {{
    background: {LIGHT_GREEN};
}}
QPushButton#dangerBtn {{
    background: #e74c3c;
}}
QLineEdit {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    background: {CARD_COLOR};
    color: {TEXT_COLOR};
    selection-background-color: {GREEN};
}}
QLineEdit:focus {{
    border: 1px solid {GREEN};
}}
QComboBox {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    background: {CARD_COLOR};
    color: {TEXT_COLOR};
    min-height: 20px;
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #999;
    margin-right: 8px;
}}
QComboBox:focus {{
    border: 1px solid {GREEN};
}}
QListWidget {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    background: {CARD_COLOR};
    outline: none;
    font-size: 13px;
}}
QListWidget::item {{
    padding: 10px 12px;
    border-bottom: 1px solid #f0f0f0;
}}
QListWidget::item:selected {{
    background: {LIGHT_GREEN};
    color: {DARK_GREEN};
}}
QListWidget::item:hover {{
    background: #f0f8f2;
}}
QTextEdit {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    background: {CARD_COLOR};
    font-size: 13px;
    padding: 8px;
    color: {TEXT_COLOR};
}}
QProgressBar {{
    border: none;
    border-radius: 4px;
    background: #e8e8e8;
    text-align: center;
    font-size: 11px;
    color: white;
    min-height: 8px;
    max-height: 8px;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {GREEN}, stop:1 {DARK_GREEN});
}}
QCheckBox {{
    spacing: 8px;
    font-size: 13px;
    color: {TEXT_COLOR};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #ccc;
    background: white;
}}
QCheckBox::indicator:checked {{
    background: {GREEN};
    border-color: {GREEN};
}}
QRadioButton {{
    spacing: 8px;
    font-size: 13px;
    color: {TEXT_COLOR};
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 1px solid #ccc;
    background: white;
}}
QRadioButton::indicator:checked {{
    background: {GREEN};
    border-color: {GREEN};
}}
QLabel#titleLabel {{
    font-size: 26px;
    font-weight: 700;
    color: {TEXT_COLOR};
}}
QLabel#subtitleLabel {{
    font-size: 13px;
    color: {SUB_TEXT};
}}
QLabel#sectionLabel {{
    font-size: 15px;
    font-weight: 600;
    color: {TEXT_COLOR};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
"""


class WorkerThread(QThread):
    """Background worker thread."""
    progress = pyqtSignal(int, int, str, bool)
    finished = pyqtSignal(bool, object)
    log = pyqtSignal(str)

    def __init__(self, task, *args, **kwargs):
        super().__init__()
        self.task = task
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.task(*self.args, **self.kwargs)
            self.finished.emit(True, result)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\nWorker error: {e}\n{tb}\n")
            except Exception:
                pass
            self.finished.emit(False, str(e))


class SetupTab(QWidget):
    """Settings and decryption tab."""

    db_decrypted = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("设置与数据库解密")
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        desc = QLabel("选择微信数据目录，自动提取密钥并解密数据库")
        desc.setObjectName("subtitleLabel")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(5)

        dir_group = QGroupBox("微信数据目录")
        dir_layout = QHBoxLayout(dir_group)
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("选择微信数据存储目录...")
        dir_layout.addWidget(self.dir_input)

        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("secondaryBtn")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_dir)
        dir_layout.addWidget(browse_btn)

        auto_btn = QPushButton("自动查找")
        auto_btn.setObjectName("secondaryBtn")
        auto_btn.setFixedWidth(100)
        auto_btn.clicked.connect(self._auto_find)
        dir_layout.addWidget(auto_btn)

        layout.addWidget(dir_group)

        decrypt_group = QGroupBox("提取密钥并解密")
        decrypt_layout = QVBoxLayout(decrypt_group)

        tip = QLabel("确保微信已登录，点击下方按钮将自动从微信进程内存提取密钥并解密数据库")
        tip.setWordWrap(True)
        tip.setObjectName("subtitleLabel")
        decrypt_layout.addWidget(tip)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        decrypt_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("就绪")
        self.progress_label.setObjectName("subtitleLabel")
        decrypt_layout.addWidget(self.progress_label)

        btn_layout = QHBoxLayout()
        self.decrypt_btn = QPushButton("一键提取密钥并解密")
        self.decrypt_btn.setMinimumHeight(38)
        self.decrypt_btn.clicked.connect(self._extract_and_decrypt)
        btn_layout.addWidget(self.decrypt_btn)
        btn_layout.addStretch()
        decrypt_layout.addLayout(btn_layout)

        or_label = QLabel("—— 或手动输入密钥 ——")
        or_label.setAlignment(Qt.AlignCenter)
        or_label.setObjectName("subtitleLabel")
        decrypt_layout.addWidget(or_label)

        manual_layout = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("粘贴64位hex密钥（如通过wx_key等工具获取）...")
        manual_layout.addWidget(self.key_input)

        self.manual_decrypt_btn = QPushButton("手动解密")
        self.manual_decrypt_btn.setMinimumHeight(36)
        self.manual_decrypt_btn.clicked.connect(self._manual_decrypt)
        manual_layout.addWidget(self.manual_decrypt_btn)
        decrypt_layout.addLayout(manual_layout)

        layout.addWidget(decrypt_group)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(160)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        layout.addStretch()

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择微信数据目录")
        if d:
            self.dir_input.setText(d)
            self._log(f"选择目录: {d}")

    def _auto_find(self):
        try:
            dirs = find_wechat_data_dirs()
            if dirs:
                self.dir_input.setText(dirs[0])
                self._log(f"自动找到: {dirs[0]}")
                if len(dirs) > 1:
                    for d in dirs[1:]:
                        self._log(f"  备选: {d}")
            else:
                self._log("未找到微信数据目录，请手动选择")
                QMessageBox.information(self, "提示", "未自动找到微信数据目录，请手动选择。")
        except Exception as e:
            self._log(f"自动查找失败: {e}")

    def _extract_and_decrypt(self):
        data_dir = self.dir_input.text().strip()
        if not data_dir or not os.path.isdir(data_dir):
            QMessageBox.warning(self, "提示", "请先选择有效的微信数据目录")
            return

        self.decrypt_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self._log("开始从微信进程提取密钥...")

        def do_work():
            def progress_cb(current, total, msg, success):
                pass
            db = WeChatDatabase(data_dir)
            db.extract_keys(progress_callback=progress_cb)
            db.decrypt_all(progress_callback=progress_cb)
            return db

        self._worker = WorkerThread(do_work)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _manual_decrypt(self):
        data_dir = self.dir_input.text().strip()
        if not data_dir or not os.path.isdir(data_dir):
            QMessageBox.warning(self, "提示", "请先选择有效的微信数据目录")
            return

        key_hex = self.key_input.text().strip()
        if not key_hex:
            QMessageBox.warning(self, "提示", "请输入密钥")
            return

        if len(key_hex) != 64:
            QMessageBox.warning(self, "提示", "密钥应为64位hex字符")
            return

        try:
            bytes.fromhex(key_hex)
        except ValueError:
            QMessageBox.warning(self, "提示", "密钥格式不正确，请输入有效的hex字符串")
            return

        self.decrypt_btn.setEnabled(False)
        self.manual_decrypt_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self._log(f"使用手动密钥解密: {key_hex[:16]}...")

        def do_work():
            db = WeChatDatabase(data_dir)
            db.key_map = {"manual": key_hex}

            db.temp_dir = tempfile.mkdtemp(prefix="wechat_decrypted_")
            search_dir = os.path.dirname(db.msg_dir) if db.msg_dir else data_dir

            all_db_files = []
            for root, dirs, files in os.walk(search_dir):
                for name in files:
                    if name.endswith('.db') and not name.endswith('-wal') and not name.endswith('-shm'):
                        all_db_files.append(os.path.join(root, name))

            total = len(all_db_files)
            success_count = 0

            for i, db_path in enumerate(all_db_files):
                db_name = os.path.relpath(db_path, search_dir)
                dst = os.path.join(db.temp_dir, db_name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)

                success = False
                try:
                    from core.wechat_db import decrypt_db
                    success = decrypt_db(db_path, key_hex, dst)
                except Exception:
                    pass

                if success:
                    success_count += 1

            db._decrypted = True
            return db, success_count, total

        self._worker = WorkerThread(do_work)
        self._worker.finished.connect(self._on_manual_done)
        self._worker.start()

    def _on_manual_done(self, success, result):
        self.decrypt_btn.setEnabled(True)
        self.manual_decrypt_btn.setEnabled(True)
        if success:
            db, success_count, total = result
            self.db = db
            pct = int(success_count / total * 100) if total > 0 else 0
            self.progress_bar.setValue(pct)
            self.progress_label.setText(f"解密完成! {success_count}/{total} 个数据库")
            self._log(f"解密完成: {success_count}/{total} 个数据库成功")
            self.db_decrypted.emit(self.db)
            QMessageBox.information(self, "成功",
                f"解密完成！成功解密 {success_count}/{total} 个数据库\n请切换到「联系人」选项卡选择要导出的好友。")
        else:
            self.progress_bar.setValue(0)
            self.progress_label.setText("操作失败")
            self._log(f"失败: {result}")
            QMessageBox.critical(self, "失败", f"操作失败:\n{str(result)[:300]}")

    def _on_done(self, success, result):
        self.decrypt_btn.setEnabled(True)
        if success:
            self.db = result
            self.progress_bar.setValue(100)
            self.progress_label.setText("解密完成!")
            key_count = len(self.db.key_map) if self.db.key_map else 0
            self._log(f"数据库解密完成! 提取到 {key_count} 个密钥")
            self.db_decrypted.emit(self.db)
            QMessageBox.information(self, "成功",
                "数据库解密完成！\n请切换到「联系人」选项卡选择要导出的好友。")
        else:
            self.progress_bar.setValue(0)
            self.progress_label.setText("操作失败")
            self._log(f"失败: {result}")
            QMessageBox.critical(self, "失败",
                f"操作失败:\n{str(result)[:300]}\n\n请确保：\n"
                "1. 微信已登录并正在运行\n"
                "2. 本程序以管理员权限运行\n"
                "3. 在微信中打开一个聊天窗口")


class ContactsTab(QWidget):
    """Contacts selection tab."""

    contact_selected = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.contacts = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("选择联系人")
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        desc = QLabel("选择要导出聊天记录的好友或群聊")
        desc.setObjectName("subtitleLabel")
        layout.addWidget(desc)
        layout.addSpacing(5)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索联系人...")
        self.search_input.textChanged.connect(self._filter_contacts)
        search_layout.addWidget(self.search_input)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._load_contacts)
        search_layout.addWidget(refresh_btn)
        layout.addLayout(search_layout)

        self.contacts_list = QListWidget()
        self.contacts_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.contacts_list.itemClicked.connect(self._on_contact_click)
        layout.addWidget(self.contacts_list)

        info_layout = QHBoxLayout()
        self.count_label = QLabel("共 0 个联系人")
        self.count_label.setObjectName("subtitleLabel")
        info_layout.addWidget(self.count_label)
        info_layout.addStretch()

        self.selected_label = QLabel("未选择")
        self.selected_label.setObjectName("subtitleLabel")
        info_layout.addWidget(self.selected_label)
        layout.addLayout(info_layout)

        layout.addStretch()

    def set_db(self, db):
        self.db = db
        self._load_contacts()

    def _load_contacts(self):
        if not self.db:
            QMessageBox.information(self, "提示", "请先在「设置」选项卡中解密数据库")
            return

        self.contacts_list.clear()
        self.contacts = self.db.get_contacts()
        self.count_label.setText(f"共 {len(self.contacts)} 个联系人")

        for c in self.contacts:
            name = c["display_name"]
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, c)
            item.setToolTip(f"昵称: {c['nickname']}\n备注: {c['conremark']}")
            self.contacts_list.addItem(item)

    def _filter_contacts(self, text):
        for i in range(self.contacts_list.count()):
            item = self.contacts_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def _on_contact_click(self, item):
        contact = item.data(Qt.UserRole)
        if contact:
            self.selected_label.setText(f"已选择: {contact['display_name']}")
            self.contact_selected.emit(contact)


class ExportTab(QWidget):
    """Export options tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.contact = None
        self.messages = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("导出聊天记录")
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        contact_group = QGroupBox("当前联系人")
        contact_layout = QVBoxLayout(contact_group)
        self.contact_label = QLabel("请先在「联系人」选项卡中选择好友")
        self.contact_label.setObjectName("subtitleLabel")
        contact_layout.addWidget(self.contact_label)
        layout.addWidget(contact_group)

        format_group = QGroupBox("导出格式")
        format_layout = QVBoxLayout(format_group)

        self.format_group = QButtonGroup(self)
        formats = [
            ("pdf", "PDF - 适合打印和分享，排版精美"),
            ("docx", "Word (DOCX) - 可编辑文档"),
            ("html", "HTML - 可在浏览器中查看，还原微信界面"),
            ("csv", "CSV - 表格格式，适合数据分析"),
            ("json", "JSON - 结构化数据，适合程序处理"),
        ]

        for fmt_id, fmt_desc in formats:
            radio = QRadioButton(fmt_desc)
            radio.setProperty("format", fmt_id)
            self.format_group.addButton(radio)
            format_layout.addWidget(radio)
            if fmt_id == "html":
                radio.setChecked(True)

        layout.addWidget(format_group)

        output_group = QGroupBox("输出目录")
        output_layout = QHBoxLayout(output_group)
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("选择导出文件保存目录...")
        default_output = os.path.join(os.path.expanduser("~"), "Desktop", "微信聊天记录导出")
        self.output_input.setText(default_output)
        output_layout.addWidget(self.output_input)

        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("secondaryBtn")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_output)
        output_layout.addWidget(browse_btn)
        layout.addWidget(output_group)

        export_group = QGroupBox("导出进度")
        export_layout = QVBoxLayout(export_group)

        self.export_progress = QProgressBar()
        self.export_progress.setValue(0)
        export_layout.addWidget(self.export_progress)

        self.export_status = QLabel("就绪")
        self.export_status.setObjectName("subtitleLabel")
        export_layout.addWidget(self.export_status)

        btn_layout = QHBoxLayout()
        self.export_btn = QPushButton("开始导出")
        self.export_btn.clicked.connect(self._do_export)
        btn_layout.addWidget(self.export_btn)

        self.open_folder_btn = QPushButton("打开导出目录")
        self.open_folder_btn.setObjectName("secondaryBtn")
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_folder)
        btn_layout.addWidget(self.open_folder_btn)
        btn_layout.addStretch()
        export_layout.addLayout(btn_layout)

        layout.addWidget(export_group)

        log_group = QGroupBox("导出日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        layout.addStretch()

    def set_contact(self, contact, db):
        self.contact = contact
        self.db = db
        self.contact_label.setText(f"当前联系人: {contact['display_name']}")
        self._load_messages()

    def _load_messages(self):
        if not self.db or not self.contact:
            return
        self.messages = self.db.get_messages(self.contact["username"])
        self.contact_label.setText(
            f"{self.contact['display_name']} - 共 {len(self.messages)} 条消息"
        )

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d:
            self.output_input.setText(d)

    def _open_folder(self):
        path = self.output_input.text().strip()
        if os.path.isdir(path):
            os.startfile(path)

    def _do_export(self):
        if not self.contact:
            QMessageBox.warning(self, "提示", "请先选择要导出的联系人")
            return

        if not self.messages:
            QMessageBox.warning(self, "提示", "该联系人没有聊天记录")
            return

        fmt_btn = self.format_group.checkedButton()
        if not fmt_btn:
            QMessageBox.warning(self, "提示", "请选择导出格式")
            return

        fmt = fmt_btn.property("format")
        output_dir = self.output_input.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择导出目录")
            return

        self.export_btn.setEnabled(False)
        self.export_progress.setValue(0)
        self.export_status.setText("正在导出...")

        def do_export():
            exporter = ChatExporter(
                self.messages,
                self.contact["display_name"],
                output_dir
            )
            filepath = exporter.export(fmt)
            return filepath

        self._worker = WorkerThread(do_export)
        self._worker.finished.connect(self._on_export_done)
        self._worker.start()

    def _on_export_done(self, success, result):
        self.export_btn.setEnabled(True)
        if success:
            self.export_progress.setValue(100)
            self.export_status.setText(f"导出成功!")
            self.open_folder_btn.setEnabled(True)
            self.log_text.append(f"导出成功: {result}")
            QMessageBox.information(self, "成功", f"聊天记录已导出到:\n{result}")
        else:
            self.export_status.setText("导出失败")
            self.log_text.append(f"导出失败: {result}")
            QMessageBox.critical(self, "失败", f"导出失败:\n{result}")


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WeChatChatExporter - 微信聊天记录导出工具")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)
        self.setStyleSheet(STYLESHEET)

        self._setup_ui()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(f"""
            background: {CARD_COLOR};
            border-bottom: 1px solid {BORDER_COLOR};
            padding: 14px 24px;
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 10, 24, 10)

        app_title = QLabel("WeChatChatExporter - 微信聊天记录导出工具")
        app_title.setStyleSheet(f"""
            font-size: 18px;
            font-weight: 700;
            color: {GREEN};
        """)
        header_layout.addWidget(app_title)

        header_layout.addStretch()

        version_label = QLabel("v2.0.0 | Copyright © 泪无痕")
        version_label.setStyleSheet(f"font-size: 12px; color: {SUB_TEXT};")
        header_layout.addWidget(version_label)

        main_layout.addWidget(header)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self.setup_tab = SetupTab()
        self.contacts_tab = ContactsTab()
        self.export_tab = ExportTab()

        tabs.addTab(self.setup_tab, "  设置  ")
        tabs.addTab(self.contacts_tab, "  联系人  ")
        tabs.addTab(self.export_tab, "  导出  ")

        self.setup_tab.db_decrypted.connect(self._on_db_decrypted)
        self.contacts_tab.contact_selected.connect(self._on_contact_selected)

        main_layout.addWidget(tabs)

        footer = QWidget()
        footer.setStyleSheet(f"""
            background: {CARD_COLOR};
            border-top: 1px solid {BORDER_COLOR};
            padding: 8px;
        """)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 6, 24, 6)

        footer_text = QLabel("仅用于个人数据备份，请勿用于非法用途 | 所有操作均在本地完成 | Copyright © 泪无痕")
        footer_text.setStyleSheet(f"font-size: 11px; color: {SUB_TEXT};")
        footer_layout.addWidget(footer_text)
        footer_layout.addStretch()

        main_layout.addWidget(footer)

    def _on_db_decrypted(self, db):
        self.contacts_tab.set_db(db)

    def _on_contact_selected(self, contact):
        db = self.contacts_tab.db
        self.export_tab.set_contact(contact, db)
