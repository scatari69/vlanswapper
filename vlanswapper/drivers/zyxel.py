"""Zyxel driver (managed GS/XGS-series switches).

On Zyxel, a port's VLAN membership and PVID are set separately: first add the
port as untagged in the ``vlan <id>`` context, then set the PVID in the port
context.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver


class ZyxelDriver(BaseDriver):
    name = "zyxel"
    detect_markers = ("zyxel", "gs1", "gs2", "xgs")
    mac_table_cmd = "show mac address-table"
    port_status_cmd = "show interfaces status"  # TODO: verify the format per series

    def disable_paging(self) -> None:
        # TODO: not all Zyxel firmware supports disabling paging via the CLI.
        # If output is cut at --More--, increase the session idle/timeout.
        pass

    def enter_config(self) -> None:
        self._run("configure")

    def exit_config(self) -> None:
        self._run("exit")

    def iface(self, port_number: int) -> str:
        return str(port_number)

    def create_vlan(self, vlan_id: int) -> None:
        # On Zyxel, creating a VLAN is combined with adding the port (see set_access_vlan).
        pass

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Add the port untagged to the target VLAN (the VLAN is created if absent).
        self._run(f"vlan {vlan_id}")
        self._run(f"fixed {port_number}")
        self._run(f"untagged {port_number}")
        self._run("exit")
        # Set the port PVID — the previous access VLAN is replaced by the new PVID.
        self._run(f"interface port-channel {port_number}")
        self._run(f"pvid {vlan_id}")
        self._run("exit")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show mac address-table mac {macfmt.colon(mac)}")
        # Format: MAC  VID  Port  Type  →  take the Port column.
        m = re.search(r"[0-9a-fA-F:]{17}\s+\d+\s+(\d+)", out)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        self._run_confirm("write memory")
