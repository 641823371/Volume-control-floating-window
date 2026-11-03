"""自定义控件：设备选择框 DeviceSelector 与 自定义音量滑块 VolumeSlider。

两个控件都遵循极简黑白线条风格（白底、黑细线、圆角），
仅负责自身呈现与交互，不含具体业务逻辑（通过回调与上层通信）。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QPoint, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QFontMetrics,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QFrame, QListWidget, QListWidgetItem, QHBoxLayout,
    QSlider, QApplication,
)

from audio_manager import AudioDeviceInfo

# 回调类型别名，便于阅读与类型检查
GetDevicesCallback = Callable[[], List[AudioDeviceInfo]]
SelectCallback = Callable[[str], None]
RefreshCallback = Callable[[], None]
SetDefaultCallback = Callable[[str], None]


class DeviceSelector(QWidget):
    """圆角矩形设备选择框：显示当前设备名，点击弹出下拉列表，双击设为主输出。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # 开启真正背景透明，配合自绘半透明填充透出浮窗背景（蓝发女仆立绘）
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._current_id: str = ""
        self._current_name: str = ""
        self._unavailable: bool = False
        self._is_default: bool = False
        self._get_devices: Optional[GetDevicesCallback] = None
        self._on_select: Optional[SelectCallback] = None
        self._on_refresh: Optional[RefreshCallback] = None
        self._on_set_default: Optional[SetDefaultCallback] = None
        self._popup: Optional[QFrame] = None
        # 点击 vs 拖动手势区分所需状态
        self._press_global: Optional[QPoint] = None
        self._window_origin: Optional[QPoint] = None
        self._did_drag: bool = False
        self._drag_threshold: int = 4
        # 单击弹窗 / 双击设为主输出 的区分
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._open_popup)
        self._suppress_click: bool = False

        self._label = QLabel("选择设备", self)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chevron = QLabel("▾", self)
        self._chevron.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(4)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._chevron, 0)

        self.setMinimumHeight(28)
        self.setCursor(Qt.PointingHandCursor)

    # ---- 对外接口 ----
    def set_callbacks(
        self,
        get_devices: GetDevicesCallback,
        on_select: SelectCallback,
        on_refresh: RefreshCallback,
        on_set_default: Optional[SetDefaultCallback] = None,
    ) -> None:
        """注入下拉列表数据来源与选择/刷新/设主输出回调。"""
        self._get_devices = get_devices
        self._on_select = on_select
        self._on_refresh = on_refresh
        self._on_set_default = on_set_default

    def current_id(self) -> str:
        """返回当前绑定设备的 ID（空字符串表示未选择）。"""
        return self._current_id

    def set_device(self, device: Optional[AudioDeviceInfo], is_default: bool = False) -> None:
        """设置当前设备；device 为 None 表示回到「选择设备」。"""
        if device is None:
            self._current_id = ""
            self._current_name = ""
        else:
            self._current_id = device.device_id
            self._current_name = device.name
        self._unavailable = False
        self._is_default = is_default
        self._refresh_text()
        self.update()

    def set_default_mark(self, is_default: bool) -> None:
        """更新「是否系统主输出」标记（文字后加 ★）。"""
        if self._is_default != is_default:
            self._is_default = is_default
            self._refresh_text()
            self.update()

    def set_unavailable(self) -> None:
        """标记当前设备不可用（被拔出/禁用），UI 给出提示。"""
        self._unavailable = True
        self._refresh_text()
        self.update()

    def set_available(self) -> None:
        """清除不可用标记。"""
        self._unavailable = False
        self._refresh_text()
        self.update()

    # ---- 内部实现 ----
    def _refresh_text(self) -> None:
        if self._unavailable:
            text = "设备不可用"
        elif self._current_name:
            text = self._current_name + (" ★" if self._is_default else "")
        else:
            text = "选择设备"
        metrics = QFontMetrics(self._label.font())
        # 预留右侧箭头与内边距空间，名称过长时用省略号
        available = max(20, self.width() - 34)
        self._label.setText(metrics.elidedText(text, Qt.ElideRight, available))
        color = "#9aa7b5" if (self._unavailable or not self._current_name) else "#2b3a4a"
        self._label.setStyleSheet(f"color:{color};")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # 记录按下起点，暂不弹窗；随后根据是否移动判定为「拖动窗口」还是「点击弹出」
            self._press_global = event.globalPosition().toPoint()
            window = self.window()
            self._window_origin = (
                window.geometry().topLeft() if window is not None else None
            )
            self._did_drag = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is not None and (event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > self._drag_threshold:
                self._did_drag = True
                window = self.window()
                if window is not None and self._window_origin is not None:
                    window.move(self._window_origin + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if not self._did_drag:
                if self._suppress_click:
                    # 上一次是双击，抑制本次单击弹窗
                    self._suppress_click = False
                else:
                    # 未拖动：延迟弹窗，给「双击设为主输出」留判定窗口
                    interval = QApplication.instance().doubleClickInterval()
                    self._click_timer.start(interval)
            else:
                # 拖动结束：持久化窗口新位置
                window = self.window()
                if window is not None and hasattr(window, "_save_window_state"):
                    window._save_window_state()  # type: ignore[attr-defined]
            self._press_global = None
            self._window_origin = None
            self._did_drag = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_click = True
            if self._current_id and self._on_set_default is not None:
                self._on_set_default(self._current_id)
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
        # 半透明背景（alpha≈180），透出浮窗背景（蓝发女仆立绘）；边框深蓝半透明
        painter.setPen(QPen(QColor(91, 135, 196, 210), 1.5))
        painter.setBrush(QBrush(QColor(247, 250, 254, 180)))
        painter.drawPath(path)

    def _open_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None

        popup = _DevicePopup(self)
        popup.setStyleSheet(
            "QFrame{background:#ffffff;border:1.5px solid #5b87c4;border-radius:8px;}"
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{padding:6px 10px;color:#2b3a4a;}"
            "QListWidget::item:hover{background:#eaf2fb;}"
        )
        list_widget = QListWidget(popup)
        list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        device_count = len(self._get_devices() or [])
        list_widget.setFixedHeight(min(240, 30 * (device_count + 2)))

        refresh_item = QListWidgetItem("↻ 刷新设备")
        refresh_item.setData(Qt.UserRole, "__refresh__")
        list_widget.addItem(refresh_item)
        for dev in self._get_devices() or []:
            item = QListWidgetItem(dev.name)
            item.setData(Qt.UserRole, dev.device_id)
            list_widget.addItem(item)

        list_widget.itemClicked.connect(self._on_item_clicked)
        layout = QHBoxLayout(popup)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(list_widget)

        popup.setFixedWidth(self.width())
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        popup.show()
        self._popup = popup

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        if data == "__refresh__":
            if self._on_refresh is not None:
                self._on_refresh()
            return
        if isinstance(data, str) and data and self._on_select is not None:
            self._on_select(data)


class _TrackSlider(QSlider):
    """内部滑块子类：完全自定义绘制轨道与手柄，并支持静音态显示 X。

    重写 paintEvent 实现自定义绘制，**不调用 super().paintEvent**，
    以避免原生圆点被二次绘制。鼠标交互仍由 QSlider 默认逻辑处理，
    手柄中心 x 与 QSlider 默认交互几何一致（±7px 留白），桌面小组件可接受。

    toggleMuteRequested：在圆形手柄附近双击时发射，用于切换静音。
    """

    toggleMuteRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self._muted = False
        self.setStyleSheet("background:transparent;")

    def set_muted(self, muted: bool) -> None:
        """设置静音态标志并重绘（此方法不发射任何信号）。"""
        self._muted = bool(muted)
        self.update()

    def _handle_x(self) -> float:
        """计算当前手柄中心 x（与 paintEvent 一致）。"""
        rect = self.rect()
        padding = 7
        span = self.maximum() - self.minimum()
        value = self.value()
        if span <= 0:
            return float(rect.x()) + padding
        ratio = (value - self.minimum()) / span
        usable = rect.width() - 2 * padding
        return float(rect.x()) + padding + ratio * usable

    def mouseDoubleClickEvent(self, event) -> None:
        # 仅当双击落在圆形手柄附近时，发射静音切换请求
        if event.button() == Qt.LeftButton:
            if abs(event.position().x() - self._handle_x()) <= 16:
                self.toggleMuteRequested.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt 虚函数名固定)
        # 完全自定义绘制：不调用 super()，避免原生圆点二次绘制
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        mid_y = rect.height() / 2.0

        track_color = QColor(220, 230, 242)   # 浅蓝 #dce6f2
        fill_color = QColor(63, 116, 184)     # 深蓝 #3f74b8

        # 轨道：全长 2px 高圆角线
        track_rect = QRectF(rect.x(), mid_y - 1.0, rect.width(), 2.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track_rect, 1.0, 1.0)

        # 手柄中心 x：与 QSlider 默认交互几何一致（±7px 留白）
        handle_x = self._handle_x()

        # 已填充部分：左端到手柄中心，2px 高圆角线
        filled_width = handle_x - float(rect.x())
        if filled_width > 0:
            filled_rect = QRectF(float(rect.x()), mid_y - 1.0, filled_width, 2.0)
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(filled_rect, 1.0, 1.0)

        # 手柄
        if self._muted:
            # 静音：红色 X（#cf4f4f，约 2.2px 线宽，两条对角线长约 ±7）
            x_color = QColor(207, 79, 79)
            pen = QPen(x_color, 2.2)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            s = 7
            painter.drawLine(
                QPointF(handle_x - s, mid_y - s), QPointF(handle_x + s, mid_y + s)
            )
            painter.drawLine(
                QPointF(handle_x + s, mid_y - s), QPointF(handle_x - s, mid_y + s)
            )
        else:
            # 非静音：白底圆 + 深蓝 1.5px 描边
            painter.setPen(QPen(fill_color, 1.5))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawEllipse(QPointF(handle_x, mid_y), 7, 7)


class VolumeSlider(QWidget):
    """自定义水平音量滑块：细线轨道 + 圆点手柄 + 拖动时显示百分比。"""

    # 信号：滑块值变化（0~100），编辑结束（松开鼠标），双击手柄切换静音
    volumeChanged = Signal(int)
    editingFinished = Signal()
    muteToggleRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._slider = _TrackSlider(self)
        self._slider.setRange(0, 100)
        self._slider.setCursor(Qt.PointingHandCursor)
        self._is_dragging = False
        self._muted = False
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderPressed.connect(self._on_pressed)
        self._slider.sliderReleased.connect(self._on_released)
        self._slider.toggleMuteRequested.connect(self.muteToggleRequested.emit)

        self._label = QLabel("", self)
        self._label.setFixedWidth(34)
        self._label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._label.setStyleSheet("color:#8aa7c6; font-size:10px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0,0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._label, 0)
        self.setMinimumHeight(20)

    # ---- 信号回调 ----
    def _on_value_changed(self, value: int) -> None:
        if self._is_dragging:
            # 拖动中始终显示百分比
            self._label.setText(f"{value}%")
        else:
            # 程序化更新（系统跟随）：静音态显示"静音"，否则留空
            self._label.setText("静音" if self._muted else "")
        self.volumeChanged.emit(value)

    def _on_pressed(self) -> None:
        self._is_dragging = True
        self._label.setText(f"{self._slider.value()}%")

    def _on_released(self) -> None:
        self._is_dragging = False
        # 松开后：静音态显示"静音"，否则留空
        self._label.setText("静音" if self._muted else "")
        self.editingFinished.emit()

    # ---- 对外接口 ----
    def value(self) -> int:
        return self._slider.value()

    def set_value(self, value: int) -> None:
        """以编程方式设置滑块值（不会触发外部 volumeChanged 信号）。"""
        self._slider.blockSignals(True)
        self._slider.setValue(int(value))
        self._slider.blockSignals(False)

    def set_muted(self, muted: bool) -> None:
        """设置静音显示态（手柄画 X / 文字"静音"）。不发射任何信号。

        仅在非拖动时管理右侧文字标签：静音显示"静音"，否则留空；
        拖动中标签由 _on_pressed / _on_value_changed 负责显示百分比。
        """
        self._muted = bool(muted)
        self._slider.set_muted(self._muted)
        if not self._is_dragging:
            self._label.setText("静音" if self._muted else "")

    def set_enabled(self, enabled: bool) -> None:
        self._slider.setEnabled(enabled)


class _DevicePopup(QFrame):
    """设备下拉弹窗：不透明实色白底，文字清晰可读（由 stylesheet 绘制）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
