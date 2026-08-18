"""D-Link 1100 **/ME** driver (Metro Ethernet line: DGS-1100-06/ME, -10/ME, ...).

The /ME models are a separate firmware line from the plain DGS-1100 smart
switches, and their CLI is the **DES-1210's**, so this driver extends
:class:`DlinkDes1210Driver` directly: block-style ``show vlan`` (membership read
from ``Member Ports``), ``config vlan vlanid ... add untagged`` /
``add tagged``, and ``config port_vlan <port> pvid <vid>``.

Deliberately **not** built on :class:`Dlink1100Driver`: the only thing that one
adds is the pager workaround for firmware that keeps paging on for ``show fdb``
and ``show ports``, and the /ME line doesn't share that quirk. If a particular
/ME firmware turns out to page after all, the fix is one method here::

    def _run_view(self, command: str) -> str:
        return self.s.run_paged(command)

The driver exists as its own class mainly so the model is detected and reported
as itself instead of silently falling into the generic ``dgs-1100`` driver, and
so any further /ME-specific difference has one obvious place to live.
"""

from __future__ import annotations

from .dlink_des1210 import DlinkDes1210Driver


class Dlink1100MeDriver(DlinkDes1210Driver):
    name = "dlink_1100_me"
    # Each marker must be longer than the plain 'dgs-1100' (8 chars) of the
    # non-ME driver, because detect._match resolves ties by longest marker.
    # Written without the DES/DGS prefix so both spellings of a model match.
    detect_markers = (
        "1100-06/me", "1100-08/me", "1100-10/me",
        "1100-16/me", "1100-24/me", "1100-26/me",
    )
