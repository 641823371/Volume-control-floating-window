"""配置模块：音量控制器的窗口与设备绑定持久化（JSON，含容错）。

配置文件优先写入 %APPDATA%/DesktopVolumeController/config.json；
若系统目录不可写，则回退到本程序所在目录的 config.json。
所有读写操作均做了异常容错，损坏的配置文件会被忽略并使用默认值。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---- 默认配置常量 ----
DEFAULT_WIDTH: int = 360
DEFAULT_HEIGHT: int = 200
DEFAULT_OPACITY: float = 1.0
ROW_COUNT: int = 3
FALLBACK_DEVICE_ID: str = ""  # 空字符串表示「选择设备」
APP_DIR_NAME: str = "DesktopVolumeController"
VALID_OPACITY_LEVELS: List[float] = [1.0, 0.9, 0.8]


@dataclass
class AppConfig:
    """音量控制器配置；可被 JSON 安全地序列化与反序列化。"""

    window_x: int = 200
    window_y: int = 200
    window_width: int = DEFAULT_WIDTH
    window_height: int = DEFAULT_HEIGHT
    opacity: float = DEFAULT_OPACITY
    always_on_top: bool = True
    locked: bool = False
    device_ids: List[str] = field(
        default_factory=lambda: [FALLBACK_DEVICE_ID] * ROW_COUNT
    )

    def to_dict(self) -> Dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return {
            "window_x": self.window_x,
            "window_y": self.window_y,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "opacity": self.opacity,
            "always_on_top": self.always_on_top,
            "locked": self.locked,
            "device_ids": self.device_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """从字典构造配置；对缺失/非法字段回退默认值。"""
        raw_ids = data.get("device_ids", [])
        if not isinstance(raw_ids, list):
            raw_ids = []
        normalized: List[str] = []
        for i in range(ROW_COUNT):
            value = raw_ids[i] if i < len(raw_ids) else FALLBACK_DEVICE_ID
            normalized.append(str(value) if value else FALLBACK_DEVICE_ID)
        return cls(
            window_x=int(data.get("window_x", 200)),
            window_y=int(data.get("window_y", 200)),
            window_width=int(data.get("window_width", DEFAULT_WIDTH)),
            window_height=int(data.get("window_height", DEFAULT_HEIGHT)),
            opacity=float(data.get("opacity", DEFAULT_OPACITY)),
            always_on_top=bool(data.get("always_on_top", True)),
            locked=bool(data.get("locked", False)),
            device_ids=normalized,
        )


def get_config_path() -> str:
    """返回配置文件绝对路径（优先 APPDATA，失败回退到程序目录）。"""
    app_data = os.environ.get("APPDATA")
    if app_data:
        directory = os.path.join(app_data, APP_DIR_NAME)
        try:
            os.makedirs(directory, exist_ok=True)
            return os.path.join(directory, "config.json")
        except OSError:
            pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config(path: str | None = None) -> AppConfig:
    """读取配置；文件缺失或解析失败时返回默认配置。"""
    config_path = path or get_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            return AppConfig.from_dict(raw)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        # 配置文件损坏或不可读：忽略并使用默认值
        pass
    return AppConfig()


def save_config(config: AppConfig, path: str | None = None) -> bool:
    """将配置写入文件；成功返回 True，失败返回 False（不抛出）。"""
    config_path = path or get_config_path()
    try:
        directory = os.path.dirname(config_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(config.to_dict(), handle, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False
