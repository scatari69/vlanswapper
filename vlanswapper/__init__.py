"""vlanswapper — настройка access-VLAN на портах свитчей по Telnet.

VLAN назначается по формуле ``vlan = 100 + номер_порта``. Поддерживаются
D-Link, Eltex, Huawei, BDCOM, Zyxel с автоопределением вендора.
"""

from __future__ import annotations

__version__ = "0.1.0"
