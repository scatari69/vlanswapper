"""D-Link 1100-series driver (DES-1100 / DGS-1100, e.g. the DGS-1100-10).

The CLI is the DES-1210's, so this driver inherits it
(:class:`DlinkDes1210Driver`) — block-style ``show vlan``, ``config vlan vlanid
... add untagged`` (no PVID command).

The class exists to identify and report the model, and to hold any 1100-only
difference that turns up. Paging is *not* one of them any more: the whole D-Link
family reads its listing views through the pager-aware path in
:class:`DlinkDriver`, because this firmware is not alone in accepting
``disable clipaging`` and then paging ``show ports``/``show vlan`` anyway.

Templates are **best-effort**; verify with ``--dry-run --vendor dlink_1100``.
"""

from __future__ import annotations

from .dlink_des1210 import DlinkDes1210Driver


class Dlink1100Driver(DlinkDes1210Driver):
    name = "dlink_1100"
    # 'des-1100'/'dgs-1100' are longer than the generic 'des-'/'dgs-'/'d-link',
    # so autodetect prefers this driver (longest marker wins).
    detect_markers = ("des-1100", "dgs-1100")
