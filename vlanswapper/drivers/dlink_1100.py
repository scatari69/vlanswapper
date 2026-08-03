"""D-Link 1100-series driver (DES-1100 / DGS-1100, e.g. the DGS-1100-10).

The CLI is essentially the DES-1210's, so this driver inherits it
(:class:`DlinkDes1210Driver`) — block-style ``show vlan``, ``config vlan vlanid
... add untagged``, ``config port_vlan <port> pvid <vid>``.

The one real difference: **paging stays on for the listing views**. The firmware
either doesn't know ``disable clipaging`` or ignores it for ``show fdb`` and
``show ports``, so those stop on a pager line and wait for a keypress instead of
returning to the prompt. Reading them with the plain "send and wait for the
prompt" logic would just time out, so every listing goes through
:meth:`_run_view` → :meth:`Session.run_paged`, which answers the pager with a
space until the output ends. Commands that don't page are unaffected — with no
pager line, ``run_paged`` behaves exactly like ``run``.

Templates are **best-effort**; verify with ``--dry-run --vendor dlink_1100``.
"""

from __future__ import annotations

from ..session import MORE_RE
from .dlink_des1210 import DlinkDes1210Driver


class Dlink1100Driver(DlinkDes1210Driver):
    name = "dlink_1100"
    # 'des-1100'/'dgs-1100' are longer than the generic 'des-'/'dgs-'/'d-link',
    # so autodetect prefers this driver (longest marker wins).
    detect_markers = ("des-1100", "dgs-1100")
    #: pager line this firmware stops on; overridable per firmware revision.
    more_re = MORE_RE
    #: key sent to advance one page (space = next page on D-Link smart switches).
    page_key = " "

    def disable_paging(self) -> None:
        # Try anyway — on some revisions it trims the shorter outputs — but never
        # rely on it: the listing views are read through the pager-aware path.
        out = self._run("disable clipaging")
        if "fail" in out.lower() or "error" in out.lower():
            self.s.log("[1100] disable clipaging not supported — reading views page by page")

    def _run_view(self, command: str) -> str:
        return self.s.run_paged(command, self.more_re, page_key=self.page_key)
