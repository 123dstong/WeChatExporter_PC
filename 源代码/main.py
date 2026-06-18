import os
import sys
import traceback

LOG_FILE = os.path.join(os.path.expanduser("~"), "WeChatChatExporter_crash.log")


def setup_logging():
    """Log exceptions to file for crash diagnosis."""
    class LogWriter:
        def __init__(self, log_path):
            self.terminal = sys.stderr
            try:
                self.log = open(log_path, "a", encoding="utf-8")
            except Exception:
                self.log = None

        def write(self, message):
            if self.terminal:
                self.terminal.write(message)
            if self.log:
                self.log.write(message)
                self.log.flush()

        def flush(self):
            if self.terminal:
                self.terminal.flush()
            if self.log:
                self.log.flush()

    try:
        sys.stderr = LogWriter(LOG_FILE)
    except Exception:
        pass


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def main():
    setup_logging()

    try:
        sys.path.insert(0, resource_path('.'))

        from PyQt5.QtWidgets import QApplication, QMessageBox
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont

        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        app = QApplication(sys.argv)
        font = QFont("Microsoft YaHei", 10)
        app.setFont(font)
        app.setStyle("Fusion")

        from gui.main_window import MainWindow
        window = MainWindow()
        window.show()

        sys.exit(app.exec_())

    except Exception as e:
        tb = traceback.format_exc()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"FATAL ERROR at startup: {e}\n")
                f.write(f"{tb}\n")
        except Exception:
            pass

        try:
            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            QMessageBox.critical(None, "启动失败",
                f"程序启动失败:\n{str(e)[:200]}\n\n日志文件: {LOG_FILE}")
        except Exception:
            pass


if __name__ == '__main__':
    main()
