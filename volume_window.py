"""主窗口模块：无边框悬浮窗，负责布局、拖动、缩放、右键菜单与托盘。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QPoint, QRect, QEvent, QRectF
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QIcon, QPixmap,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QMenu, QSystemTrayIcon, QApplication, QToolTip,
)

from config import AppConfig, save_config
from audio_manager import AudioManager, AudioDeviceInfo
from widgets import DeviceSelector, VolumeSlider
from volume_callback import VolumeChangeBridge, SystemVolumeCallback


class _Row:
    """一行（设备选择框 + 滑块）的轻量容器。"""

    __slots__ = ("selector", "slider")

    def __init__(self, selector: DeviceSelector, slider: VolumeSlider) -> None:
        self.selector = selector
        self.slider = slider


class VolumeWindow(QWidget):
    """桌面悬浮音量控制器主窗口。"""

    ROW_COUNT = 3
    SHADOW_MARGIN = 14
    RADIUS = 16
    EDGE_TOLERANCE = 10
    MIN_WIDTH = 320
    MIN_HEIGHT = 170

    def __init__(self, config: AppConfig, audio: AudioManager) -> None:
        super().__init__()
        self._config = config
        self._audio = audio
        self._devices: List[AudioDeviceInfo] = []
        self._rows: List[_Row] = []

        self._dragging = False
        self._drag_offset = QPoint()
        self._resizing = False
        self._resize_edge: Optional[str] = None
        self._resize_start = QPoint()
        self._resize_start_geom = QRect()
        self._force_quit = False
        self._tray: Optional[QSystemTrayIcon] = None

        # 系统音量实时跟随：COM 回调 -> Qt 主线程 的桥接。
        # 每个绑定行各持有一个回调实例（device_id, callback），重绑或退出时注销。
        self._volume_bridge = VolumeChangeBridge()
        self._volume_bridge.systemVolumeChanged.connect(
            self._on_system_volume_changed
        )
        self._row_callbacks: List[Optional[tuple]] = [None] * self.ROW_COUNT

        self._init_window()
        self._build_ui()
        self._apply_config()
        self._init_bindings()
        self._setup_tray()

        # 全局事件过滤器：捕获任意子控件上的右键，统一弹出菜单
        QApplication.instance().installEventFilter(self)

    # ---- 初始化 ----
    def _init_window(self) -> None:
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.setContextMenuPolicy(Qt.PreventContextMenu)

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # 修改窗口标志后会隐藏，需要重新显示

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            self.SHADOW_MARGIN, self.SHADOW_MARGIN + 6,
            self.SHADOW_MARGIN, self.SHADOW_MARGIN,
        )
        main_layout.setSpacing(12)

        for i in range(self.ROW_COUNT):
            selector = DeviceSelector(self)
            slider = VolumeSlider(self)
            selector.set_callbacks(
                get_devices=lambda: self._devices,
                on_select=lambda dev_id, idx=i: self._on_device_selected(idx, dev_id),
                on_refresh=self.refresh_devices,
                on_set_default=lambda dev_id, idx=i: self._on_set_default(idx, dev_id),
            )
            slider.volumeChanged.connect(
                lambda v, idx=i: self._on_volume_changed(idx, v)
            )
            slider.editingFinished.connect(
                lambda idx=i: self._on_volume_released(idx)
            )

            row_widget = QWidget(self)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            row_layout.addWidget(selector, 4)  # 设备框约占 40% 宽度
            row_layout.addWidget(slider, 6)     # 滑块占剩余宽度
            main_layout.addWidget(row_widget, 1)  # 三行等分高度
            self._rows.append(_Row(selector, slider))

    def _apply_config(self) -> None:
        self._apply_window_flags()
        geom = QRect(
            self._config.window_x, self._config.window_y,
            self._config.window_width, self._config.window_height,
        )
        # 防止窗口被历史坐标存到屏幕外（多显示器/分辨率变化常见），
        # 导致“程序在跑但看不到”而被误判为打不开。若窗口完全不在任何屏幕内则拉回可见位置。
        screens = QApplication.screens()
        on_screen = any(s.availableGeometry().intersects(geom) for s in screens)
        if not on_screen:
            geom.moveTo(200, 200)
        self.setGeometry(geom)
        self.setWindowOpacity(self._config.opacity)

    def _init_bindings(self) -> None:
        """初始化每行绑定的设备：优先使用已保存配置，否则自动绑定前 N 个设备。"""
        self._devices = self._audio.enumerate_playback_devices()
        auto_index = 0
        for i in range(self.ROW_COUNT):
            saved_id = self._config.device_ids[i]
            if saved_id and self._audio.get_volume(saved_id) is not None:
                self._bind_row(i, saved_id)
            elif self._devices and auto_index < len(self._devices):
                self._bind_row(i, self._devices[auto_index].device_id)
                auto_index += 1
            else:
                self._bind_row(i, "")

    # ---- 绑定与同步 ----
    def _bind_row(self, index: int, device_id: str) -> None:
        # 重绑前，先注销该行旧设备的音量回调，避免重复注册与悬空引用
        self._unregister_row_callback(index)

        row = self._rows[index]
        default_id = self._audio.get_default_device_id()
        if device_id:
            row.selector.set_device(self._find_device(device_id), device_id == default_id)
            volume = self._audio.get_volume(device_id)
            if volume is None:
                row.selector.set_unavailable()
                row.slider.set_enabled(False)
                row.slider.set_value(0)
            else:
                row.selector.set_available()
                row.slider.set_enabled(True)
                row.slider.set_value(int(round(volume * 100)))
            # 注册系统音量变化回调，实现「系统调音量时浮窗实时跟随」
            callback = SystemVolumeCallback(self._volume_bridge, device_id)
            if self._audio.register_volume_callback(device_id, callback):
                self._row_callbacks[index] = (device_id, callback)
        else:
            row.selector.set_device(None)
            row.slider.set_enabled(False)
            row.slider.set_value(0)
            self._row_callbacks[index] = None
        self._config.device_ids[index] = device_id
        save_config(self._config)

    def _unregister_row_callback(self, index: int) -> None:
        """注销指定行已注册的系统音量回调（若存在）。"""
        entry = self._row_callbacks[index]
        if entry is None:
            return
        device_id, callback = entry
        try:
            self._audio.unregister_volume_callback(device_id, callback)
        except Exception:
            pass
        self._row_callbacks[index] = None

    def _on_system_volume_changed(self, device_id: str, level: float) -> None:
        """系统侧音量变化（任务栏/混音器/其它软件）的接收槽。

        由 COM 回调线程经 Qt 信号跨线程调用，运行在**主线程**，
        因此可安全更新滑块。使用 set_value（blockSignals）避免回环写系统。
        """
        value = int(round(max(0.0, min(1.0, float(level))) * 100))
        for row in self._rows:
            if row.selector.current_id() == device_id:
                row.slider.set_value(value)

    def _find_device(self, device_id: str) -> Optional[AudioDeviceInfo]:
        for dev in self._devices:
            if dev.device_id == device_id:
                return dev
        name = self._audio.get_device_name(device_id) or "未知设备"
        return AudioDeviceInfo(device_id=device_id, name=name)

    def _on_device_selected(self, index: int, device_id: str) -> None:
        self._bind_row(index, device_id)

    def _on_volume_changed(self, index: int, value: int) -> None:
        row = self._rows[index]
        device_id = row.selector.current_id()
        if not device_id:
            return
        # 实时写系统音量（Windows 音量设置开销可接受）
        self._audio.set_volume(device_id, value / 100.0)
        # 同一设备可能被多行绑定，需同步刷新其它行
        self._sync_same_device(device_id, value)

    def _on_volume_released(self, index: int) -> None:
        # 松开鼠标时持久化窗口/绑定状态
        self._save_window_state()

    def _sync_same_device(self, device_id: str, value: int) -> None:
        for row in self._rows:
            if row.selector.current_id() == device_id:
                row.slider.set_value(value)

    def _on_set_default(self, index: int, device_id: str) -> None:
        """双击设备框：将该设备切换为系统主输出，并更新 ★ 标记与提示。"""
        ok = self._audio.set_default_device(device_id)
        default_id = self._audio.get_default_device_id()
        for row in self._rows:
            row.selector.set_default_mark(row.selector.current_id() == default_id)
        name = self._audio.get_device_name(device_id) or "该设备"
        self._notify(
            f"已设为主输出：{name}" if ok else f"切换主输出失败：{name}"
        )

    def _notify(self, message: str) -> None:
        """托盘气泡提示；无托盘时用 tooltip 兜底。"""
        if self._tray is not None:
            self._tray.showMessage(
                "桌面音量控制器", message, QSystemTrayIcon.Information, 2500
            )
        else:
            QToolTip.showText(
                self.geometry().topLeft(), message, self, QRect(), 2500
            )

    def refresh_devices(self) -> None:
        """重新枚举设备并刷新所有行的可用状态、音量与主输出标记。"""
        # enumerate_playback_devices 内部已重建设备缓存，无需再 invalidate
        self._devices = self._audio.enumerate_playback_devices()
        default_id = self._audio.get_default_device_id()
        for row in self._rows:
            device_id = row.selector.current_id()
            if not device_id:
                continue
            volume = self._audio.get_volume(device_id)
            if volume is None:
                row.selector.set_unavailable()
                row.slider.set_enabled(False)
            else:
                row.selector.set_available()
                row.slider.set_enabled(True)
                row.slider.set_value(int(round(volume * 100)))
            row.selector.set_default_mark(device_id == default_id)

    # ---- 鼠标拖动 / 缩放 ----
    def _edge_at(self, pos: QPoint) -> Optional[str]:
        rect = self.rect()
        tol = self.EDGE_TOLERANCE
        x, y = pos.x(), pos.y()
        left = x <= tol
        right = x >= rect.width() - tol
        top = y <= tol
        bottom = y >= rect.height() - tol
        if left and top:
            return "top-left"
        if right and top:
            return "top-right"
        if left and bottom:
            return "bottom-left"
        if right and bottom:
            return "bottom-right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    @staticmethod
    def _cursor_for_edge(edge: Optional[str]) -> Qt.CursorShape:
        if edge in ("top-left", "bottom-right"):
            return Qt.SizeFDiagCursor
        if edge in ("top-right", "bottom-left"):
            return Qt.SizeBDiagCursor
        if edge in ("left", "right"):
            return Qt.SizeHorCursor
        if edge in ("top", "bottom"):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._config.locked:
            pos = event.position().toPoint()
            edge = self._edge_at(pos)
            if edge is not None:
                self._resizing = True
                self._resize_edge = edge
                self._resize_start = event.globalPosition().toPoint()
                self._resize_start_geom = self.geometry()
            else:
                self._dragging = True
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.geometry().topLeft()
                )

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_edge is not None:
            self._apply_resize(event.globalPosition().toPoint())
            return
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging or self._resizing:
            self._dragging = False
            self._resizing = False
            self._resize_edge = None
            self._save_window_state()

    def _apply_resize(self, global_pos: QPoint) -> None:
        edge = self._resize_edge
        if edge is None:
            return
        start = self._resize_start
        geom = self._resize_start_geom
        dx = global_pos.x() - start.x()
        dy = global_pos.y() - start.y()
        new_geom = QRect(geom)
        if "left" in edge:
            new_x = geom.x() + dx
            new_w = geom.width() - dx
            if new_w >= self.MIN_WIDTH:
                new_geom.setX(new_x)
                new_geom.setWidth(new_w)
        if "right" in edge:
            new_w = geom.width() + dx
            if new_w >= self.MIN_WIDTH:
                new_geom.setWidth(new_w)
        if "top" in edge:
            new_y = geom.y() + dy
            new_h = geom.height() - dy
            if new_h >= self.MIN_HEIGHT:
                new_geom.setY(new_y)
                new_geom.setHeight(new_h)
        if "bottom" in edge:
            new_h = geom.height() + dy
            if new_h >= self.MIN_HEIGHT:
                new_geom.setHeight(new_h)
        self.setGeometry(new_geom)

    # ---- 右键菜单 ----
    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        top_text = "置顶：开" if self._config.always_on_top else "置顶：关"
        top_action = menu.addAction(top_text)
        opacity_menu = menu.addMenu("透明度")
        a100 = opacity_menu.addAction("100%")
        a90 = opacity_menu.addAction("90%")
        a80 = opacity_menu.addAction("80%")
        lock_text = "解锁位置与大小" if self._config.locked else "锁定位置与大小"
        lock_action = menu.addAction(lock_text)
        reset_action = menu.addAction("重置窗口位置")
        refresh_action = menu.addAction("刷新设备列表")
        menu.addSeparator()
        quit_action = menu.addAction("退出")

        action = menu.exec(global_pos)
        if action is None:
            return
        if action == top_action:
            self._toggle_always_on_top()
        elif action in (a100, a90, a80):
            level = 1.0 if action == a100 else (0.9 if action == a90 else 0.8)
            self._set_opacity(level)
        elif action == lock_action:
            self._toggle_lock()
        elif action == reset_action:
            self._reset_window_position()
        elif action == refresh_action:
            self.refresh_devices()
        elif action == quit_action:
            self._quit_app()

    def _toggle_always_on_top(self) -> None:
        self._config.always_on_top = not self._config.always_on_top
        self._apply_window_flags()
        self._save_window_state()

    def _set_opacity(self, level: float) -> None:
        self.setWindowOpacity(level)
        self._config.opacity = level
        self._save_window_state()

    def _toggle_lock(self) -> None:
        self._config.locked = not self._config.locked
        self.setCursor(Qt.ArrowCursor)
        self._save_window_state()

    def _reset_window_position(self) -> None:
        self._config.window_x = 200
        self._config.window_y = 200
        self._config.window_width = 360
        self._config.window_height = 200
        self.setGeometry(200, 200, 360, 200)
        self._save_window_state()

    def _quit_app(self) -> None:
        # 注销全部系统音量回调，避免音频服务线程在退出后回调到已销毁对象
        for i in range(self.ROW_COUNT):
            self._unregister_row_callback(i)
        self._save_window_state()
        self._force_quit = True
        if self._tray is not None:
            self._tray.hide()
        QApplication.instance().quit()

    # ---- 托盘 ----
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self)
        tray.setIcon(self._create_tray_icon())
        tray.setToolTip("桌面音量控制器")
        menu = QMenu()
        show_action = menu.addAction("显示/隐藏")
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        show_action.triggered.connect(self._toggle_visibility)
        quit_action.triggered.connect(self._quit_app)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_visibility()

    @staticmethod
    def _create_tray_icon() -> QIcon:
        """绘制一个极简的扬声器图标作为托盘图标。"""
        size = 32
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(40, 40, 40), 2)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawRect(6, 12, 5, 8)
        path = QPainterPath()
        path.moveTo(11, 12)
        path.lineTo(18, 7)
        path.lineTo(18, 25)
        path.lineTo(11, 20)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(20, 11, 6, 10, -45 * 16, 90 * 16)
        painter.end()
        return QIcon(pixmap)

    # ---- 状态持久化 ----
    def _save_window_state(self) -> None:
        geom = self.geometry()
        self._config.window_x = geom.x()
        self._config.window_y = geom.y()
        self._config.window_width = geom.width()
        self._config.window_height = geom.height()
        save_config(self._config)

    def closeEvent(self, event) -> None:
        # 关闭时不退出，仅隐藏（由托盘负责退出）
        if self._force_quit:
            event.accept()
        else:
            event.ignore()
            self.hide()

    # ---- 全局事件过滤器（捕获任意子控件的右键） ----
    def eventFilter(self, obj, event) -> bool:
        if (
            event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.RightButton
            and self.isAncestorOf(obj)
        ):
            self._show_context_menu(event.globalPosition().toPoint())
            return True
        # 悬停在窗口边/角（含子控件上方）时显示对应缩放光标
        if (
            event.type() == QEvent.MouseMove
            and self.isAncestorOf(obj)
            and not self._dragging
            and not self._resizing
            and not self._config.locked
            and event.buttons() == Qt.NoButton
        ):
            local = event.globalPosition().toPoint() - self.geometry().topLeft()
            self.setCursor(self._cursor_for_edge(self._edge_at(local)))
        return super().eventFilter(obj, event)

    # ---- 绘制圆角主体与柔和阴影 ----
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = self.SHADOW_MARGIN
        radius = self.RADIUS
        # 手动绘制柔和阴影：多层圆角矩形叠加，越靠近主体越深
        for i in range(margin, 0, -1):
            rect = self.rect().adjusted(i, i, -i, -i)
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), radius, radius)
            alpha = int(28 * (1.0 - i / margin)) + 3
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawPath(path)
        # 主体：白色圆角矩形 + 深色细描边
        body = self.rect().adjusted(margin, margin, -margin, -margin)
        path = QPainterPath()
        path.addRoundedRect(QRectF(body), radius, radius)
        painter.setPen(QPen(QColor(40, 40, 40), 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawPath(path)
