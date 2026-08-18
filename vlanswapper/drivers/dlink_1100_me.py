"""D-Link 1100 **/ME** driver (Metro Ethernet line: DGS-1100-06/ME, -10/ME, ...).

The CLI is the DES-1210's, and the listing views page exactly like the plain
1100's, so this extends :class:`Dlink1100Driver` (= 1210 command set + the
pager-aware read path).

Confirmed on a DGS-1100-10/ME: ``disable clipaging`` **exists and succeeds, yet
``show ports`` and ``show vlan`` still come back page by page**, stopping on::

    CTRL+C ESC q Quit SPACE n Next Page p Previous Page r Refresh

Reading those with the plain "wait for the prompt" path times out and leaves the
device sitting in the pager, which desynchronizes everything sent afterwards.
``show fdb`` is the exception — the MAC table arrives as one listing — but it
goes through the same paged path, which is harmless: with no pager line
``run_paged`` behaves exactly like ``run``.

The port table on this firmware wraps each port across two lines (the MDI
setting continues underneath); the inherited D-Link ``parse_port_status`` keys
off the leading port number, so the continuation lines are simply skipped.
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
