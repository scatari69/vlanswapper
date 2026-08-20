"""D-Link DES-1210 driver tests: detection priority and VLAN commands.

Uses a recording session double (no network): it returns preset responses
matched by command substring and remembers everything sent.
"""

import types
import unittest

from tests.mock_switch import MockSwitch
from vlanswapper.detect import detect_vendor
from vlanswapper.drivers import DriverError, get_driver
from vlanswapper.drivers.dlink_des1210 import DlinkDes1210Driver
from vlanswapper.session import Session
from vlanswapper.telnet import TelnetClient


class FakeSession:
    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.sent: list[str] = []
        self.prompt_re = r"[>#]\s*$"
        self.c = types.SimpleNamespace(timeout=3.0)

    def _lookup(self, command: str) -> str:
        for key, val in self.responses.items():
            if key in command:
                return val
        return ""

    def run(self, command, **kw):
        self.sent.append(command)
        return self._lookup(command)

    def run_paged(self, command, more_re=None, page_key=" ", **kw):
        self.sent.append(command)
        return self._lookup(command)

    def run_expect(self, command, expect, **kw):
        self.sent.append(command)
        return self._lookup(command), 1  # 1 = prompt immediately, no confirmation

    def log(self, msg):
        pass


class DetectTests(unittest.TestCase):
    def test_des1210_beats_generic_dlink(self):
        # The banner has both the generic 'D-Link' and the specific model — DES-1210 must win.
        sess = FakeSession({"show switch": "Device Type : DES-1210-28 D-Link Smart Switch"})
        self.assertEqual(detect_vendor(sess), "dlink_des1210")

    def test_generic_dlink_for_other_models(self):
        sess = FakeSession({"show switch": "Device Type : DES-3200-28 D-Link"})
        self.assertEqual(detect_vendor(sess), "dlink")

    def test_registry_has_model(self):
        self.assertIs(get_driver("dlink_des1210"), DlinkDes1210Driver)


class VlanCommandTests(unittest.TestCase):
    # The DES-1210-28 doesn't support 'show vlan ports <n>' — only the full 'show vlan'.
    # Format matches real firmware ('Member/Untagged/Forbidden Ports' labels; the
    # uplink ports 25-26 sit in Member for VLAN 253 with an empty Untagged).
    SHOW_VLAN = (
        "VID                : 1         VLAN NAME      : default\n"
        "VLAN Type          : Static\n"
        "Member Ports       : 1-28\n"
        "Untagged Ports     : 1-28\n"
        "Forbidden Ports    : \n"
        "\n"
        "VID                : 105       VLAN NAME      : vlan105\n"
        "VLAN Type          : Static\n"
        "Member Ports       : \n"
        "Untagged Ports     : \n"
        "Forbidden Ports    : \n"
        "\n"
        "VID                : 253       VLAN NAME      : mangement\n"
        "VLAN Type          : Static\n"
        "Member Ports       : 25-26\n"
        "Untagged Ports     : \n"
        "Forbidden Ports    : \n"
    )

    def test_set_access_vlan_moves_port(self):
        # Port 5 is currently untagged in VLAN 1 -> it should move to VLAN 105.
        sess = FakeSession({"show vlan": self.SHOW_VLAN})
        DlinkDes1210Driver(sess).set_access_vlan(5, 105)
        self.assertIn("config vlan vlanid 1 delete 5", sess.sent)
        self.assertIn("config vlan vlanid 105 add untagged 5", sess.sent)
        # No PVID step at all — membership alone moves the port.
        self.assertFalse(any("pvid" in c for c in sess.sent))
        self.assertFalse(any("gvrp" in c for c in sess.sent))
        # Exactly 'show vlan' without 'ports' — 'show vlan ports N' doesn't work on the 1210.
        self.assertIn("show vlan", sess.sent)
        self.assertFalse(any("show vlan ports" in c for c in sess.sent))

    def test_current_untagged_vlans_parses_ranges(self):
        # Port 24 is in the 1-28 range (VLAN 1) but not in the empty list of VLAN 105.
        sess = FakeSession({"show vlan": self.SHOW_VLAN})
        vids = DlinkDes1210Driver(sess)._current_untagged_vlans(24)
        self.assertEqual(vids, [1])

    def test_uplink_port_detected_via_member_ports(self):
        # Port 25 is in Member Ports of VLAN 253 (empty untagged = tagged trunk)
        # — the guard must recognize the uplink via 'Member Ports'.
        sess = FakeSession({"show vlan": self.SHOW_VLAN})
        drv = DlinkDes1210Driver(sess)
        self.assertIn(253, drv.port_vlans(25))
        self.assertTrue(drv.is_uplink_port(25, 253))
        # A regular access port 24 is not treated as an uplink.
        self.assertFalse(drv.is_uplink_port(24, 253))

    def test_find_uplink_ports(self):
        # VLAN 253 has member ports 25-26 → those are the uplink trunks.
        sess = FakeSession({"show vlan": self.SHOW_VLAN})
        self.assertEqual(DlinkDes1210Driver(sess).find_uplink_ports(253), [25, 26])

    def test_swap_tags_new_vlan_on_uplinks(self):
        # Configuring port 5 (→ VLAN 105) must also tag 105 on uplink ports 25/26.
        sess = FakeSession({"show vlan": self.SHOW_VLAN})
        drv = DlinkDes1210Driver(sess)
        drv.swap(5, 105, save=False, uplink_ports=drv.find_uplink_ports(253))
        self.assertIn("config vlan vlanid 105 add tagged 25", sess.sent)
        self.assertIn("config vlan vlanid 105 add tagged 26", sess.sent)
        # The access port itself is still set untagged, not tagged.
        self.assertIn("config vlan vlanid 105 add untagged 5", sess.sent)
        self.assertNotIn("config vlan vlanid 105 add tagged 5", sess.sent)

    def test_rejected_untagged_add_is_reported(self):
        # The switch refuses when the port is still untagged elsewhere; the
        # driver must raise instead of letting the caller print "Done".
        sess = FakeSession({"show vlan": self.SHOW_VLAN,
                            "add untagged": "Fail! The port is a member of another VLAN."})
        with self.assertRaises(DriverError) as cm:
            DlinkDes1210Driver(sess).set_access_vlan(11, 111)
        self.assertIn("111", str(cm.exception))
        self.assertIn("untagged in another VLAN", str(cm.exception))

    def test_accepted_untagged_add_is_silent(self):
        sess = FakeSession({"show vlan": self.SHOW_VLAN})   # empty reply = success
        DlinkDes1210Driver(sess).set_access_vlan(11, 111)   # must not raise

    def test_find_port_by_mac(self):
        fdb = "100  vlan100  AA-BB-CC-DD-EE-FF  7  Dynamic"
        sess = FakeSession({"show fdb mac_address": fdb})
        port = DlinkDes1210Driver(sess).find_port_by_mac("aa:bb:cc:dd:ee:ff")
        self.assertEqual(port, 7)


# Verbatim 'show ports' from a DES-1210-28/ME: two lines per port, and the
# listing pages even though 'disable clipaging' was accepted.
DES1210_SHOW_PORTS = "\r\n".join([
    "Port  State/     Settings                 Connection               Address",
    "Type  MDI        Speed/Duplex/FlowCtrl    Speed/Duplex/FlowCtrl    Learning",
    "----- -------    ---------------------    ---------------------    --------",
] + [
    line
    for port in range(1, 29)
    for line in (
        f"{port}     Enabled    Auto/Disabled            "
        + ("Link Down                Enabled" if port % 4 == 0 else
           "100M/Full/Disabled       Enabled"),
        "      Auto",
    )
])


class PagedListingTests(unittest.TestCase):
    """The /ME variant pages 'show ports'; the 1210 driver must cope."""

    class Des1210MeSwitch(MockSwitch):
        # Legend printed by this firmware (a listing pager, not a live screen).
        PAGER = b"CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a ALL"

        def _reply(self, line, prompt):
            if line.lower().startswith("show ports"):
                return DES1210_SHOW_PORTS.encode(), prompt
            return super()._reply(line, prompt)

    def test_port_list_read_across_pages(self):
        sw = self.Des1210MeSwitch(page_size=21).start()   # ~3 pages of output
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        try:
            session = Session(client)
            session.login("admin", "secret")
            driver = get_driver("dlink_des1210")(session)
            ports = driver.list_port_status()
            follow_up = session.run("show mac address-table")
        finally:
            client.close()

        self.assertEqual(len(ports), 28)
        expected = [(p, "down" if p % 4 == 0 else "up") for p in range(1, 29)]
        self.assertEqual(ports, expected)
        # The device is back at its prompt, not stranded in the pager.
        self.assertIn("aabb.ccdd.eeff", follow_up)


# Verbatim 'show vlan' from a DES-1210-28/ME (172.17.2.112). Port 11 is untagged
# in 315; the uplink VLAN 253 has member port 28 only. Both matter: an earlier
# build misread this as "port 11 is in 109" and "253 lives on 27-28".
REAL_VLAN_BLOCKS = [
    ("1",   "default", "",                 ""),
    ("109", "vlan109", "9,27-28",          "9"),
    ("105", "vlan105", "5,27-28",          "5"),
    ("118", "vlan118", "",                 ""),
    ("111", "vlan111", "27-28",            ""),
    ("253", "VLAN253", "28",               ""),
    ("104", "vlan104", "4,28",             "4"),
    ("315", "VLAN315", "1-3,6-8,10-26,28", "1-3,6-8,10-26"),
]


def real_show_vlan(blocks=REAL_VLAN_BLOCKS) -> str:
    lines = [""]
    for vid, name, member, untagged in blocks:
        lines += [
            f"VID                : {vid:<9} VLAN NAME      : {name}",
            "VLAN Type          : Static",
            "VLAN Advertisement : Disabled",
            f"Member Ports       : {member}",
            f"Untagged Ports     : {untagged}",
            "Forbidden Ports    : ",
            "",
        ]
    return "\n".join(lines)


class RealVlanDumpTests(unittest.TestCase):
    """Parsing the real dump, and refusing to act on a damaged one."""

    def _drv(self, text):
        return DlinkDes1210Driver(FakeSession({"show vlan": text}))

    def test_intact_dump_reads_the_right_vlans(self):
        drv = self._drv(real_show_vlan())
        self.assertEqual(drv._current_untagged_vlans(11), [315])
        self.assertEqual(drv._current_untagged_vlans(9), [109])
        # The uplink VLAN's members, not some neighbouring block's 27-28.
        self.assertEqual(drv.find_uplink_ports(253), [28])
        self.assertEqual(drv.port_vlans(11), {315})

    def test_dump_cut_at_a_page_seam_is_refused(self):
        # Cut where the real first page ended: mid-block, after 111's Member line.
        cut = "\n".join(real_show_vlan().split("\n")[:33])
        with self.assertRaises(DriverError):
            self._drv(cut)._current_untagged_vlans(11)
        # The guard degrades to "don't know" rather than a wrong answer...
        self.assertIsNone(self._drv(cut).port_vlans(11))
        # ...and nothing gets tagged on a guessed uplink.
        self.assertEqual(self._drv(cut).find_uplink_ports(253), [])

    def test_refusal_names_the_defect(self):
        # The message has to be actionable on its own: it says which VLAN the
        # listing stops at, so the operator doesn't need to re-run with -v.
        cut = "\n".join(real_show_vlan().split("\n")[:33])
        with self.assertRaises(DriverError) as cm:
            self._drv(cut)._current_untagged_vlans(11)
        msg = str(cm.exception)
        self.assertIn("VLAN 111", msg)
        self.assertIn("untagged ports", msg)
        self.assertIn("5 blocks read", msg)

    def test_empty_output_is_refused(self):
        with self.assertRaises(DriverError) as cm:
            self._drv("")._current_untagged_vlans(11)
        self.assertIn("no VLAN blocks", str(cm.exception))

    def test_lost_vid_header_is_refused(self):
        # A dropped VID line would otherwise attach 315's ports to the block above.
        damaged = "\n".join(l for l in real_show_vlan().split("\n")
                            if "VID                : 315" not in l)
        with self.assertRaises(DriverError):
            self._drv(damaged)._current_untagged_vlans(11)

    def test_fused_lines_are_refused(self):
        # A page seam can also glue two records onto one line.
        fused = real_show_vlan().replace(
            "Member Ports       : 27-28\nUntagged Ports     : ",
            "Member Ports       : 27-28VID                : 253  Untagged Ports     : ")
        with self.assertRaises(DriverError):
            self._drv(fused)._current_untagged_vlans(11)


class SeamDamageTests(unittest.TestCase):
    """A page seam like the one seen on 172.17.2.112 must not lose a VLAN."""

    LEGEND = "CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a ALL"

    def _collected(self):
        from vlanswapper.session import MORE_RE, _strip_pager
        text = real_show_vlan()
        # Glue the legend in front of VLAN 118's header, as the device does when
        # it carries on across the seam without a line break.
        damaged = text.replace("VID                : 118",
                               self.LEGEND + "VID                : 118")
        return _strip_pager(damaged, MORE_RE)

    def test_seam_does_not_lose_a_vlan(self):
        drv = DlinkDes1210Driver(FakeSession({"show vlan": self._collected()}))
        blocks, reason = drv._vlan_blocks(self._collected())
        self.assertEqual(reason, "")
        self.assertEqual([b["vid"] for b in blocks],
                         [1, 109, 105, 118, 111, 253, 104, 315])
        self.assertEqual(drv._current_untagged_vlans(11), [315])
        self.assertEqual(drv.find_uplink_ports(253), [28])

    def test_record_split_by_a_stray_break_still_parses(self):
        # A stray carriage return can break a record in half; the scan spans
        # newlines so the header is still recognised.
        text = real_show_vlan().replace("VID                : 315",
                                        "VID\n                : 315")
        drv = DlinkDes1210Driver(FakeSession({"show vlan": text}))
        self.assertEqual(drv._current_untagged_vlans(11), [315])


if __name__ == "__main__":
    unittest.main()
