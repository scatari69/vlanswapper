"""vlanswapper — configure access VLANs on switch ports over Telnet.

The VLAN is assigned by the rule ``vlan = 100 + port_number``. D-Link, Eltex,
Huawei, BDCOM and Zyxel are supported, with automatic vendor detection.
"""

from __future__ import annotations

__version__ = "0.1.0"
