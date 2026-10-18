"""音频管理模块：封装 Windows Core Audio 设备枚举与音量读写。

本模块是整个程序中**唯一**接触 pycaw / comtypes 的地方，
其它模块只通过 AudioManager 的公开方法访问音频设备，
从而保证 COM 相关逻辑集中、可测试、且对设备热插拔等异常具备容错能力。

实现说明：
- 枚举播放（render）设备时直接调用
  ``AudioUtilities.GetAllDevices(data_flow=eRender, device_state=ACTIVE)``，
  等价于 Core Audio 的
  ``IMMDeviceEnumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)``。
- 设备名取自 pycaw 的 FriendlyName（底层来自 IPropertyStore 的
  PKEY_Device_FriendlyName）；设备 ID 取自 ``device.id``（底层为 GetId()），
  该 ID 稳定，适合作为持久化键。
- 音量通过每个设备对象的 EndpointVolume（IAudioEndpointVolume）读写，
  其值为 0.0~1.0 的标量。

注意：本机 pycaw 版本未提供 ``AudioUtilities.GetDevice(id)``，因此我们将
枚举得到的 AudioDevice 对象按 ID 缓存复用，避免重复 Activate。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from pycaw.constants import EDataFlow, DEVICE_STATE
from pycaw.pycaw import AudioUtilities

import ctypes
import comtypes
from ctypes import HRESULT, c_wchar_p, POINTER
from ctypes.wintypes import DWORD
from comtypes import GUID, IUnknown, COMMETHOD

# ---- Windows 默认音频端点切换（PolicyConfig，非文档化但广泛使用的 COM 接口） ----
_CLSID_POLICY_CONFIG = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
_IID_POLICY_CONFIG = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
_CLSID_MM_DEVICE_ENUM = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_MM_DEVICE_ENUM = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_MM_DEVICE = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")


class _IMMDevice(IUnknown):
    _iid_ = _IID_MM_DEVICE
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate"),
        COMMETHOD([], HRESULT, "OpenPropertyStore"),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState"),
    ]


class _IMMDeviceEnumerator(IUnknown):
    _iid_ = _IID_MM_DEVICE_ENUM
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints"),
        COMMETHOD(
            [], HRESULT, "GetDefaultAudioEndpoint",
            (["in"], ctypes.c_int, "dataFlow"),
            (["in"], ctypes.c_int, "role"),
            (["out"], POINTER(POINTER(_IMMDevice)), "ppEndpoint"),
        ),
    ]


class _IPolicyConfig(IUnknown):
    _iid_ = _IID_POLICY_CONFIG
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat"),
        COMMETHOD([], HRESULT, "GetDeviceFormat"),
        COMMETHOD([], HRESULT, "ResetDeviceFormat"),
        COMMETHOD([], HRESULT, "SetDeviceFormat"),
        COMMETHOD([], HRESULT, "GetProcessingPeriod"),
        COMMETHOD([], HRESULT, "SetProcessingPeriod"),
        COMMETHOD([], HRESULT, "GetShareMode"),
        COMMETHOD([], HRESULT, "SetShareMode"),
        COMMETHOD([], HRESULT, "GetPropertyValue"),
        COMMETHOD([], HRESULT, "SetPropertyValue"),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                  (["in"], c_wchar_p, "wszDeviceId"),
                  (["in"], DWORD, "eRole")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility"),
    ]


@dataclass(frozen=True)
class AudioDeviceInfo:
    """单个播放（输出）音频设备的轻量描述。"""

    device_id: str
    name: str


class AudioManager:
    """音频设备访问封装类。所有公开方法都对设备异常做了容错。"""

    def __init__(self) -> None:
        # 缓存「设备 ID -> AudioDevice 对象」，避免每次操作都重新枚举/Activate。
        self._device_cache: Dict[str, object] = {}

    def enumerate_playback_devices(self) -> List[AudioDeviceInfo]:
        """枚举当前所有处于 active 状态的播放（render）输出设备。

        同时刷新内部设备对象缓存。任何异常下返回空列表，绝不向外抛出。
        """
        result: List[AudioDeviceInfo] = []
        self._device_cache.clear()
        try:
            raw_devices = AudioUtilities.GetAllDevices(
                data_flow=EDataFlow.eRender.value,
                device_state=DEVICE_STATE.ACTIVE.value,
            )
        except Exception:
            return result

        for device in raw_devices:
            try:
                device_id = str(device.id)
                name = str(getattr(device, "FriendlyName", "") or "未知设备")
                if device_id:
                    self._device_cache[device_id] = device
                    result.append(AudioDeviceInfo(device_id=device_id, name=name))
            except Exception:
                # 单个设备解析失败不应影响其余设备
                continue
        return result

    def _get_device(self, device_id: str):
        """返回缓存的 AudioDevice 对象；缺失时重新枚举以重建缓存。"""
        if not device_id:
            return None
        cached = self._device_cache.get(device_id)
        if cached is not None:
            return cached
        # 缓存缺失（如设备刚插入）：重新枚举一次再查找。
        self.enumerate_playback_devices()
        return self._device_cache.get(device_id)

    def get_volume(self, device_id: str) -> Optional[float]:
        """读取设备主音量（0.0~1.0）。失败或设备不可用返回 None。"""
        device = self._get_device(device_id)
        if device is None:
            return None
        try:
            return float(device.EndpointVolume.GetMasterVolumeLevelScalar())
        except Exception:
            self._device_cache.pop(device_id, None)
            return None

    def set_volume(self, device_id: str, level: float) -> bool:
        """设置设备主音量（level 0.0~1.0）。成功返回 True。"""
        if not device_id:
            return False
        device = self._get_device(device_id)
        if device is None:
            return False
        try:
            clamped = max(0.0, min(1.0, float(level)))
            device.EndpointVolume.SetMasterVolumeLevelScalar(clamped, None)
            return True
        except Exception:
            self._device_cache.pop(device_id, None)
            return False

    def get_device_name(self, device_id: str) -> Optional[str]:
        """根据设备 ID 获取其友好名称；找不到返回 None。"""
        device = self._get_device(device_id)
        if device is None:
            return None
        try:
            return str(getattr(device, "FriendlyName", "") or "")
        except Exception:
            return None

    # ---- 系统默认（主）输出设备 ----
    def get_default_device_id(self) -> Optional[str]:
        """返回系统当前默认播放（eRender）设备 ID；失败返回 None。"""
        try:
            enumerator = comtypes.CoCreateInstance(
                _CLSID_MM_DEVICE_ENUM, _IMMDeviceEnumerator, comtypes.CLSCTX_ALL
            )
            endpoint = enumerator.GetDefaultAudioEndpoint(0, 0)  # eRender, eConsole
            if endpoint is None:
                return None
            pid = endpoint.GetId()
            if pid is None:
                return None
            # comtypes 版本差异：新版直接返回 str，旧版返回 POINTER(c_wchar_p)
            if isinstance(pid, str):
                return pid
            return pid.contents.value
        except Exception:
            return None

    def set_default_device(self, device_id: str) -> bool:
        """将指定设备设为系统默认（主）输出，覆盖三种角色。成功返回 True。"""
        if not device_id:
            return False
        try:
            policy = comtypes.CoCreateInstance(
                _CLSID_POLICY_CONFIG, _IPolicyConfig, comtypes.CLSCTX_ALL
            )
            for role in (0, 1, 2):  # eConsole, eMultimedia, eCommunications
                policy.SetDefaultEndpoint(device_id, role)
            return True
        except Exception:
            return False

    # ---- 系统音量变化通知（实时跟随） ----
    def register_volume_callback(self, device_id: str, callback) -> bool:
        """为指定设备注册音量变化回调（回调须实现 IAudioEndpointVolumeCallback）。"""
        device = self._get_device(device_id)
        if device is None:
            return False
        try:
            device.EndpointVolume.RegisterControlChangeNotify(callback)
            return True
        except Exception:
            return False

    def unregister_volume_callback(self, device_id: str, callback) -> bool:
        """注销指定设备的音量变化回调。"""
        device = self._get_device(device_id)
        if device is None:
            return False
        try:
            device.EndpointVolume.UnregisterControlChangeNotify(callback)
            return True
        except Exception:
            return False

    def invalidate_cache(self) -> None:
        """清空设备对象缓存，通常在刷新设备列表前调用。"""
        self._device_cache.clear()
