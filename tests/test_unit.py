"""Network-free unit tests: MAC formats and IAC parsing in TelnetClient."""

import tempfile
import unittest
from pathlib import Path

from vlanswapper import mac
from vlanswapper.blacklist import is_blacklisted, load_blacklist
from vlanswapper.session import MORE_RE, _strip_pager
from vlanswapper.drivers.dlink import DlinkDriver
from vlanswapper.drivers.eltex import EltexDriver
from vlanswapper.drivers.huawei import HuaweiDriver
from vlanswapper.telnet import DO, ECHO, IAC, WILL, TelnetClient


class MacTests(unittest.TestCase):
    def test_normalize_accepts_all_forms(self):
        for form in ("aa:bb:cc:dd:ee:ff", "aabb.ccdd.eeff", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"):
            self.assertEqual(mac.normalize(form), "aabbccddeeff")

    def test_vendor_formats(self):
        m = "aabbccddeeff"
        self.assertEqual(mac.colon(m), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(mac.dot(m), "aabb.ccdd.eeff")
        self.assertEqual(mac.dash(m), "AA-BB-CC-DD-EE-FF")
        self.assertEqual(mac.huawei(m), "aabb-ccdd-eeff")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            mac.normalize("not-a-mac")


class PortStatusParsingTests(unittest.TestCase):
    def _drv(self, cls):
        return cls.__new__(cls)  # the parsers don't touch the session

    def test_cisco_like_status(self):
        out = ("Port      Status\n"
               "gi1/0/1   connected  100\n"
               "gi1/0/2   notconnect 1\n"
               "gi1/0/3   disabled   1\n")
        self.assertEqual(self._drv(EltexDriver).parse_port_status(out),
                         [(1, "up"), (2, "down"), (3, "disabled")])

    def test_huawei_brief_with_admin_down(self):
        out = ("Interface PHY  Protocol\n"
               "GE0/0/1   up   up\n"
               "GE0/0/2   down down\n"
               "GE0/0/3   *down down\n")
        self.assertEqual(self._drv(HuaweiDriver).parse_port_status(out),
                         [(1, "up"), (2, "down"), (3, "disabled")])

    def test_dlink_show_ports(self):
        out = (" 1  Enabled/Auto  Auto/Disabled/Disabled  100M/Full/None  Enabled\n"
               " 2  Enabled/Auto  Auto/Disabled/Disabled  Link Down       Enabled\n"
               " 3  Disabled/Auto Auto/Disabled/Disabled  Link Down       Enabled\n")
        self.assertEqual(self._drv(DlinkDriver).parse_port_status(out),
                         [(1, "up"), (2, "down"), (3, "disabled")])

    def test_combo_port_merges_to_best_status(self):
        drv = self._drv(EltexDriver)
        drv.port_status_cmd = "show interfaces status"
        drv.s = _StubSession("gi1/0/1 notconnect\ngi1/0/1 connected\n")
        self.assertEqual(drv.list_port_status(), [(1, "up")])


class _StubSession:
    def __init__(self, out):
        self._out = out

    def run(self, command, **kw):
        return self._out


class _RecordingSession:
    """Returns preset responses by command substring and records everything sent."""
    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.sent: list[str] = []

    def run(self, command, **kw):
        self.sent.append(command)
        for key, val in self.responses.items():
            if key in command:
                return val
        return ""


class TaggedUplinkTests(unittest.TestCase):
    def test_cisco_add_tagged_vlan_extends_trunk(self):
        sess = _RecordingSession()
        drv = EltexDriver(sess)
        drv.add_tagged_vlan(5, 105)
        self.assertIn("interface gigabitethernet 1/0/5", sess.sent)
        self.assertIn("switchport trunk allowed vlan add 105", sess.sent)

    def test_huawei_add_tagged_vlan_allow_pass(self):
        sess = _RecordingSession()
        HuaweiDriver(sess).add_tagged_vlan(5, 105)
        self.assertIn("port trunk allow-pass vlan 105", sess.sent)

    def test_generic_find_uplink_ports_enumerates(self):
        # Port 2 carries VLAN 253 in its trunk → it is the uplink.
        sess = _RecordingSession({
            "show interfaces status": "gi1/0/1 connected\ngi1/0/2 connected\n",
            "switchport gigabitethernet 1/0/1": "Access Mode VLAN: 105\n",
            "switchport gigabitethernet 1/0/2": "Trunking VLANs Enabled: 100,253\n",
        })
        self.assertEqual(EltexDriver(sess).find_uplink_ports(253), [2])

    def test_swap_tags_uplink_but_skips_target(self):
        sess = _RecordingSession()
        EltexDriver(sess).swap(5, 105, save=False, uplink_ports=[2, 5])
        self.assertIn("switchport trunk allowed vlan add 105", sess.sent)
        # Tagged only on the uplink (2), never on the access target (5).
        self.assertIn("interface gigabitethernet 1/0/2", sess.sent)
        self.assertEqual(sess.sent.count("switchport trunk allowed vlan add 105"), 1)


class UplinkGuardTests(unittest.TestCase):
    def _drv(self, cls, out):
        d = cls.__new__(cls)
        d.s = _StubSession(out)
        return d

    def test_cisco_trunk_detected_as_uplink(self):
        out = ("Administrative Mode: trunk\n"
               "Trunking Native Mode VLAN: 1\n"
               "Trunking VLANs Enabled: 253,100-105\n")
        drv = self._drv(EltexDriver, out)
        self.assertEqual(drv.port_vlans(5), {1, 100, 101, 102, 103, 104, 105, 253})
        self.assertTrue(drv.is_uplink_port(5, 253))

    def test_cisco_access_port_not_uplink(self):
        out = "Administrative Mode: access\nAccess Mode VLAN: 105\n"
        drv = self._drv(EltexDriver, out)
        self.assertEqual(drv.is_uplink_port(5, 253), False)

    def test_empty_output_is_undetermined(self):
        drv = self._drv(EltexDriver, "")
        self.assertIsNone(drv.is_uplink_port(5, 253))

    def test_dlink_show_vlan_ports_counts_tagged(self):
        out = ("  24   1    -    -    -\n"
               "  24   253  -    X    -\n")  # 253 tagged (trunk)
        drv = self._drv(DlinkDriver, out)
        self.assertIn(253, drv.port_vlans(24))
        self.assertTrue(drv.is_uplink_port(24, 253))


class BlacklistTests(unittest.TestCase):
    def _file(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        self.addCleanup(Path(tmp.name).unlink)
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def test_load_skips_comments_and_blanks(self):
        path = self._file("# restricted switches\n\n192.0.2.10\ncore-sw1  # do not touch\n")
        self.assertEqual(load_blacklist(path), ["192.0.2.10", "core-sw1"])

    def test_missing_file_blocks_nothing(self):
        self.assertEqual(load_blacklist(Path("/nonexistent/blacklist.txt")), [])
        self.assertFalse(is_blacklisted("192.0.2.10", []))

    def test_exact_ip_and_hostname_match(self):
        entries = ["192.0.2.10", "Core-SW1"]
        self.assertTrue(is_blacklisted("192.0.2.10", entries))
        self.assertTrue(is_blacklisted("core-sw1", entries))   # case-insensitive
        self.assertFalse(is_blacklisted("192.0.2.11", entries))

    def test_cidr_matches_ips_only(self):
        entries = ["10.20.0.0/16"]
        self.assertTrue(is_blacklisted("10.20.5.8", entries))
        self.assertFalse(is_blacklisted("10.21.0.1", entries))
        self.assertFalse(is_blacklisted("some-host", entries))  # hostname vs CIDR

    def test_malformed_entry_is_ignored(self):
        self.assertFalse(is_blacklisted("192.0.2.10", ["not/a/network"]))


class PagerStrippingTests(unittest.TestCase):
    LEGEND = "CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a ALL"

    def test_record_glued_to_the_legend_survives(self):
        # After a keypress the device may continue on the same row. Dropping the
        # whole legend line would take the VLAN header with it, and the port
        # lists below would then be charged to the VLAN above.
        text = ("Forbidden Ports    : \r\n" + self.LEGEND +
                "VID                : 118       VLAN NAME      : vlan118\r\n")
        kept = _strip_pager(text, MORE_RE).splitlines()
        self.assertEqual(kept, ["Forbidden Ports    : ",
                                "VID                : 118       VLAN NAME      : vlan118"])

    def test_legend_on_its_own_line_goes_completely(self):
        text = "Untagged Ports     : 9\r\n" + self.LEGEND + "\r\nForbidden Ports    : "
        self.assertEqual(_strip_pager(text, MORE_RE).splitlines(),
                         ["Untagged Ports     : 9", "Forbidden Ports    : "])

    def test_ansi_and_control_bytes_are_removed(self):
        # A pager erases itself with escapes; left in place they corrupt records.
        text = "VID\x1b[K                : 7\r\n\x00Member Ports       : 1"
        self.assertEqual(_strip_pager(text, MORE_RE).splitlines(),
                         ["VID                : 7", "Member Ports       : 1"])


class TelnetParsingTests(unittest.TestCase):
    def test_iac_negotiation_stripped(self):
        c = TelnetClient("127.0.0.1")           # don't open the socket
        c._feed(bytes([ord("A"), ord("B"), IAC, WILL, ECHO, ord("C"), IAC, DO, ECHO, ord("D")]))
        self.assertEqual(c.take_buffer(), "ABCD")

    def test_escaped_iac_becomes_single_byte(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("X"), IAC, IAC, ord("Y")]))
        self.assertEqual(c.take_buffer(), "X\xffY")

    def test_split_sequence_across_feeds(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("A"), IAC]))          # sequence cut off
        c._feed(bytes([WILL, ECHO, ord("B")]))   # continuation
        self.assertEqual(c.take_buffer(), "AB")


if __name__ == "__main__":
    unittest.main()
