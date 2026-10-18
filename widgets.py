"""自定义控件：设备选择框 DeviceSelector 与 自定义音量滑块 VolumeSlider。

两个控件都遵循极简黑白线条风格（白底、黑细线、圆角），
仅负责自身呈现与交互，不含具体业务逻辑（通过回调与上层通信）。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
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
        color = "#999999" if (self._unavailable or not self._current_name) else "#222222"
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
        painter.setPen(QPen(QColor(70, 70, 70), 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawPath(path)

    def _open_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None

        popup = QFrame(self, Qt.Popup | Qt.FramelessWindowHint)
        popup.setStyleSheet(
            "QFrame{background:#ffffff;border:1.5px solid #464646;border-radius:8px;}"
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{padding:6px 10px;color:#222222;}"
            "QListWidget::item:hover{background:#f0f0f0;}"
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


class VolumeSlider(QWidget):
    """自定义水平音量滑块：细线轨道 + 圆点手柄 + 拖动时显示百分比。"""

    # 信号：滑块值变化（0~100），以及编辑结束（松开鼠标）
    volumeChanged = Signal(int)
    editingFinished = Signal()

    _SLIDER_QSS = """
    QSlider::groove:horizontal {
        height: 2px;
        background: #d8d8d8;
        border-radius: 1px;
    }
    QSlider::sub-page:horizontal {
        height: 2px;
        background: #222222;
        border-radius: 1px;
    }
    QSlider::handle:horizontal {
        width: 14px;
        height: 14px;
        margin: -7px 0px;
        background: #ffffff;
        border: 1.5px solid #222222;
        border-radius: 7px;
    }
    QSlider::handle:horizontal:hover {
        background: #f5f5f5;
    }
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setRange(0, 100)
        self._slider.setStyleSheet(self._SLIDER_QSS)
        self._slider.setCursor(Qt.PointingHandCursor)
        self._is_dragging = False
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderPressed.connect(self._on_pressed)
        self._slider.sliderReleased.connect(self._on_released)

        self._label = QLabel("", self)
        self._label.setFixedWidth(34)
        self._label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._label.setStyleSheet("color:#b8b8b8; font-size:10px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0,0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._label, 0)
        self.setMinimumHeight(20)

    # ---- 信号回调 ----
    def _on_value_changed(self, value: int) -> None:
        if self._is_dragging:
            self._label.setText(f"{value}%")
        self.volumeChanged.emit(value)

    def _on_pressed(self) -> None:
        self._is_dragging = True
        self._label.setText(f"{self._slider.value()}%")

    def _on_released(self) -> None:
        self._is_dragging = False
        self._label.setText("")
        self.editingFinished.emit()

    # ---- 对外接口 ----
    def value(self) -> int:
        return self._slider.value()

    def set_value(self, value: int) -> None:
        """以编程方式设置滑块值（不会触发外部 volumeChanged 信号）。"""
        self._slider.blockSignals(True)
        self._slider.setValue(int(value))
        self._slider.blockSignals(False)

    def set_enabled(self, enabled: bool) -> None:
        self._slider.setEnabled(enabled)
