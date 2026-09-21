from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppState:
    """
    \u5168\u5c40\u72b6\u6001\u5bb9\u5668\u3002

    \u9875\u9762\u4e4b\u95f4\u4e0d\u8981\u76f4\u63a5\u4e92\u76f8\u5f15\u7528\u53d8\u91cf\uff0c\u5c3d\u91cf\u901a\u8fc7 AppState \u6216 ApplicationContext \u5171\u4eab\u72b6\u6001\u3002
    """

    language: str = "zh"
    theme: str = "light"
    font_size: int = 12
    ui_scale: float = 1.0

    current_page: str = "home"
    current_device: str | None = None

    serial_connected: bool = False
    selected_port: str | None = None
    baudrate: int = 115200

    # AWP192X / AWP1921 / AWP1923 \u9ed8\u8ba4\u8bbe\u5907\u5730\u5740
    awp192x_device_addr: int = 0x44

    # \u8bb0\u5f55\u6700\u8fd1\u4e00\u6b21\u53f3\u4fa7/\u6a21\u5f0f\u9875\u8bbe\u7f6e\u7684\u6700\u5927\u8f93\u51fa\u7535\u538b\uff0c\u5199\u5165\u6ce2\u5f62\u540e\u53ef\u6062\u590d
    current_max_output_voltage: float = 95.04
    current_max_output_reg_value: int = 0x007E

    # Button Mode \u5355\u70b9\u64ad\u653e\u65f6\u957f\uff0c\u5355\u4f4d us
    single_point_duration_us: int = 1000

    # \u7ed9\u540e\u7eed\u529f\u80fd\u4e34\u65f6\u6302\u8f7d\u72b6\u6001\u7528\uff0c\u907f\u514d\u4e3a\u4e86\u4e00\u4e2a\u5c0f\u5b57\u6bb5\u9891\u7e41\u6539 dataclass
    extra: dict[str, Any] = field(default_factory=dict)
