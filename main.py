"""桌面悬浮音量控制器：程序入口。

负责初始化 COM、加载配置、创建并启动主窗口。
"""

from __future__ import annotations

import os
import sys
import traceback

from config import load_config
from audio_manager import AudioManager


def _show_crash(message: str) -> None:
    """在启动失败时尽量用可见方式告知用户（Qt 对话框优先，回退到 Win32 弹窗）。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "桌面音量控制器 - 启动失败", message)
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "桌面音量控制器 - 启动失败", 0x10)
    except Exception:
        pass


def _run_gui() -> int:
    """启动图形界面主循环。"""
    # 在主线程初始化 COM（Core Audio / comtypes 要求）
    try:
        from comtypes import CoInitialize

        CoInitialize()
    except Exception:
        pass

    from PySide6.QtWidgets import QApplication
    from volume_window import VolumeWindow

    app = QApplication(sys.argv)
    # 隐藏窗口（而非关闭）时不退出程序，由托盘常驻
    app.setQuitOnLastWindowClosed(False)

    config = load_config()
    audio = AudioManager()
    window = VolumeWindow(config, audio)
    window.show()

    return app.exec()


def main() -> int:
    try:
        return _run_gui()
    except Exception:
        tb = traceback.format_exc()
        log_path = os.path.join(
            os.environ.get("TEMP", "."), "volume_controller_crash.log"
        )
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            log_path = "(无法写入日志)"
        _show_crash(
            "程序启动时发生错误：\n\n" + tb + "\n\n（详细日志已写入 " + log_path + "）"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
