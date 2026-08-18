"""D-Link 1100 **/ME** driver (Metro Ethernet line: DGS-1100-06/ME, -10/ME, ...).

The /ME models are a separate firmware line from the plain DGS-1100 smart
switches, so they get their own driver even though the CLI families are related.
Without this, a ``DGS-1100-10/ME`` banner matches the generic ``dgs-1100`` marker
and silently runs :class:`Dlink1100Driver` — the operator can't even tell from
the reported vendor which command set was used.

Behavior is inherited from :class:`Dlink1100Driver` (block-style ``show vlan``,
``config vlan vlanid ... add untagged``, ``config port_vlan ... pvid``, and the
pager-aware listing views) as the starting point. **The /ME command templates are
not verified on hardware yet** — where this line differs, override the specific
method here rather than branching in the parent. Check with
``--dry-run --vendor dlink_1100_me`` before trusting it.
"""

from __future__ import annotations

from .dlink_1100 import Dlink1100Driver


class Dlink1100MeDriver(Dlink1100Driver):
    name = "dlink_1100_me"
    # Each marker must be longer than the plain 'dgs-1100' (8 chars) of the
    # non-ME driver, because detect._match resolves ties by longest marker.
    # Written without the DES/DGS prefix so both spellings of a model match.
    detect_markers = (
        "1100-06/me", "1100-08/me", "1100-10/me",
        "1100-16/me", "1100-24/me", "1100-26/me",
    )
