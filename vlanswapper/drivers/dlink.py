"""D-Link driver (traditional CLI: DES-3200/DGS-3xxx and similar).

D-Link's quirk: there is no configuration mode and no ``switchport access
vlan`` — VLAN membership is configured with one-line ``config vlan ... add
untagged`` commands. So "removing the old VLAN" must be done explicitly: find
the port's current untagged VLAN and delete it from there.

No PVID command is issued anywhere in this family: untagged membership is what
moves the port. The syntax otherwise depends on the series/firmware — verify on
the specific model.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from ..session import MORE_RE
from .base import BaseDriver, DriverError


class DlinkDriver(BaseDriver):
    name = "dlink"
    detect_markers = ("d-link", "des-", "dgs-", "dlink")
    mac_table_cmd = "show fdb"
    port_status_cmd = "show ports"
    #: pager line this family stops on; overridable per firmware revision.
    more_re = MORE_RE
    #: key that advances one page (space on every D-Link CLI seen so far).
    page_key = " "
    #: key that leaves an interactive pager — 'show ports' is a live screen on
    #: some firmware and redraws instead of ever returning to the prompt.
    quit_key = "q"

    def disable_paging(self) -> None:
        # Best effort only. Across the D-Link range this command may be missing,
        # or succeed and still leave 'show ports'/'show vlan' paging (confirmed on
        # DES-1210-28/ME and DGS-1100-10/ME), so the listing views never rely on
        # it — see _run_view.
        out = self._run("disable clipaging")
        if "fail" in out.lower() or "error" in out.lower():
            self.s.log(f"[{self.name}] disable clipaging not supported — ignoring")

    def _run_view(self, command: str) -> str:
        # Listings go through the pager-aware read: harmless when nothing pages
        # (run_paged then behaves exactly like run), and the difference between
        # a working listing and a timeout when something does.
        return self.s.run_paged(command, self.more_re, page_key=self.page_key,
                                quit_key=self.quit_key)

    # D-Link has no separate configuration mode.
    def enter_config(self) -> None:
        pass

    def exit_config(self) -> None:
        pass

    def iface(self, port_number: int) -> str:
        return str(port_number)

    def create_vlan(self, vlan_id: int) -> None:
        # If the VLAN already exists the command returns an error — not fatal.
        out = self._run(f"create vlan vlan{vlan_id} tag {vlan_id}")
        if "fail" in out.lower() and "already" not in out.lower():
            self.s.log(f"[dlink] create vlan returned: {out.strip()}")

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """Find the VIDs where the port is currently an untagged member (usually one)."""
        out = self._run(f"show vlan ports {port_number}")
        vids: list[int] = []
        for line in out.splitlines():
            # Format: <port> <VID> <Untagged X/-> <Tagged> <Forbidden>
            m = re.match(r"\s*\d+\s+(\d+)\s+(\S+)", line)
            if m and m.group(2).upper() in ("X", "E", "UNTAGGED"):
                vids.append(int(m.group(1)))
        return vids

    def port_vlans(self, port_number: int) -> set[int] | None:
        # 'show vlan ports <n>': <port> <VID> <Untagged X/-> <Tagged X/-> ...
        # For the foolproof guard, count membership both untagged and tagged (trunk).
        out = self._run(f"show vlan ports {port_number}")
        if not out.strip():
            return None
        vlans: set[int] = set()
        for line in out.splitlines():
            m = re.match(r"\s*\d+\s+(\d+)\s+(\S+)\s+(\S+)", line)
            if not m:
                continue
            marks = (m.group(2).upper(), m.group(3).upper())
            if any(x in ("X", "E", "UNTAGGED", "TAGGED") for x in marks):
                vlans.add(int(m.group(1)))
        return vlans

    def add_tagged_vlan(self, port_number: int, vlan_id: int) -> None:
        # D-Link: no trunk allowed-list, membership is a one-line command.
        self._run(f"config vlan vlanid {vlan_id} add tagged {port_number}")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        # No PVID command on purpose: adding the port as an untagged member is
        # what moves it, and the separate PVID step is not wanted here.
        out = self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        self._check_untagged_accepted(out, port_number, vlan_id)

    def _check_untagged_accepted(self, out: str, port_number: int, vlan_id: int) -> None:
        """Fail loudly when the switch refuses the untagged membership.

        A port can be untagged in exactly one VLAN, so if the old one was not
        removed first the switch rejects this command — and reporting success
        anyway would leave the operator believing the port was moved.
        """
        low = out.lower()
        if "fail" in low or "error" in low:
            raise DriverError(
                f"switch refused to make port {port_number} untagged in VLAN "
                f"{vlan_id}: {out.strip()} — the port is most likely still "
                f"untagged in another VLAN that was not detected")

    def parse_port_status(self, output: str) -> list[tuple[int, str]]:
        # 'show ports': "1  Enabled/Auto  Auto/Disabled  Link Down  Enabled".
        # 2nd column is the admin state (Enabled/Disabled); the Connection column
        # shows 'Link Down' for a down link or a speed (100M/Full etc.) for an up one.
        rows: list[tuple[int, str]] = []
        for line in output.splitlines():
            m = re.match(r"\s*(\d+)", line)
            if not m:
                continue
            port = int(m.group(1))
            toks = line.split()
            admin = toks[1].lower() if len(toks) > 1 else ""
            low = line.lower()
            if admin.startswith("disabled"):
                rows.append((port, "disabled"))
            elif "link down" in low or "linkdown" in low:
                rows.append((port, "down"))
            else:
                rows.append((port, "up"))
        return rows

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show fdb mac_address {macfmt.dash(mac)}")
        # Format: VID  VLAN_Name  MAC_Address  Port  Type
        m = re.search(r"([0-9a-fA-F-]{17})\s+(\d+)\s+(?:Dynamic|Static|Self)",
                      out, re.IGNORECASE)
        if m:
            return int(m.group(2))
        return None

    def save(self) -> None:
        out = self._run_confirm("save", confirm_re=r"\(y/n\)|\[y/n\]")
        if "fail" in out.lower():
            raise DriverError(f"save failed: {out.strip()}")
