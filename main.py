"""桌面悬浮音量控制器：程序入口。

负责初始化 COM、加载配置、创建并启动主窗口。
"""

from __future__ import annotations

import sys

from config import load_config
from audio_manager import AudioManager


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
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
