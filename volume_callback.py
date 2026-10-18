"""系统音量实时跟随：COM 回调与 Qt 信号之间的跨线程桥接。

背景：
- Windows Core Audio 在系统音量变化（不论来源：任务栏喇叭、音量混音器、
  其它软件、快捷键）时，会通过 ``IAudioEndpointVolumeCallback.OnNotify``
  通知所有已注册者。
- 该回调运行在 **Windows 音频服务的工作线程** 上，而不是 Qt 主线程。
  在回调里直接操作 Qt 控件是不安全的（会引发竞态甚至崩溃）。
- 因此这里用一个 ``QObject`` 作为桥：回调线程只负责发射一个 Qt 信号，
  Qt 会以 QueuedConnection 把信号安全地投递到主线程的事件循环，
  由主线程的槽函数去更新滑块。

本模块是整个程序里**第二处**接触 COM（comtypes）的地方，与 audio_manager.py
分工：audio_manager 负责枚举/读写/切换，本模块只负责「被动接收通知」。
"""

from __future__ import annotations

import comtypes
from ctypes import byref

from pycaw.pycaw import IAudioEndpointVolumeCallback

from PySide6.QtCore import QObject, Signal


class VolumeChangeBridge(QObject):
    """跨线程桥：音频服务线程 -> Qt 主线程。

    信号参数：(device_id, level)
    - device_id: 发生音量变化的设备 ID（与 AudioManager 使用的 ID 一致）
    - level:     新的主音量标量，0.0~1.0
    """

    systemVolumeChanged = Signal(str, float)


class SystemVolumeCallback(comtypes.COMObject):
    """``IAudioEndpointVolumeCallback`` 的 comtypes 实现。

    每绑定一个设备就创建一个实例，并注册到该设备的 ``EndpointVolume``。
    系统音量变化时会调用 ``OnNotify``；这里提取 ``fMasterVolume`` 后把事件
    通过桥接对象发射到主线程。
    """

    _com_interfaces_ = [IAudioEndpointVolumeCallback]

    def __init__(self, bridge: VolumeChangeBridge, device_id: str) -> None:
        super().__init__()
        self._bridge = bridge
        self._device_id = device_id

    # comtypes 在收到通知时会调用此方法；pNotify 为指向
    # AUDIO_VOLUME_NOTIFICATION_DATA 的指针。
    def OnNotify(self, pNotify):  # noqa: N802  (COM 接口方法名固定为 OnNotify)
        try:
            data = pNotify.contents
            level = float(getattr(data, "fMasterVolume", 0.0))
            # 跨线程安全发射：Qt 自动以 QueuedConnection 投递到主线程槽函数。
            self._bridge.systemVolumeChanged.emit(self._device_id, level)
        except Exception:
            # 回调里任何异常都不应冒泡到音频服务线程
            pass
        return 0  # S_OK
