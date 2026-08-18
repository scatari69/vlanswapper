"""D-Link 1100-series driver tests: detection, inherited CLI, and paged views.

Two levels here: the real Telnet protocol against a mock switch that keeps
paging enabled (the 1100's actual problem), and a session double that records
which read path a view took.
"""

import types
import unittest

from tests.mock_switch import MockSwitch
from vlanswapper.detect import detect_vendor
from vlanswapper.drivers import get_driver
from vlanswapper.drivers.dlink_1100 import Dlink1100Driver
from vlanswapper.drivers.dlink_1100_me import Dlink1100MeDriver
from vlanswapper.session import Session
from vlanswapper.telnet import TelnetClient


class PagingSession:
    """Session double recording whether run() or run_paged() was used."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.sent: list[str] = []
        self.paged: list[str] = []
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
        self.paged.append(command)
        return self._lookup(command)

    def run_expect(self, command, expect, **kw):
        self.sent.append(command)
        return self._lookup(command), 1

    def log(self, msg):
        pass


class DetectTests(unittest.TestCase):
    def test_dgs1100_beats_generic_dlink(self):
        sess = PagingSession({"show switch": "Device Type : DGS-1100-10 D-Link Smart Switch"})
        self.assertEqual(detect_vendor(sess), "dlink_1100")

    def test_des1100_beats_generic_dlink(self):
        sess = PagingSession({"show switch": "Device Type : DES-1100-16 D-Link"})
        self.assertEqual(detect_vendor(sess), "dlink_1100")

    def test_des1210_still_wins_for_its_own_model(self):
        # The 1100 markers must not steal the DES-1210's detection.
        sess = PagingSession({"show switch": "Device Type : DES-1210-28 D-Link"})
        self.assertEqual(detect_vendor(sess), "dlink_des1210")

    def test_registry_has_model(self):
        self.assertIs(get_driver("dlink_1100"), Dlink1100Driver)


class PagedViewTests(unittest.TestCase):
    def test_views_use_the_paged_read_path(self):
        # MAC table, port list and the VLAN dump must all go through run_paged,
        # otherwise the pager would hang the session.
        sess = PagingSession({
            "show fdb": "100 vlan100 AA-BB-CC-DD-EE-FF 7 Dynamic",
            "show ports": " 1  Enabled/Auto  Auto  Link Down  Enabled",
            "show vlan": "VID : 253  VLAN NAME : mgmt\nMember Ports : 9-10\n",
        })
        drv = Dlink1100Driver(sess)
        drv.show_mac_table()
        drv.list_port_status()
        drv.find_uplink_ports(253)
        self.assertEqual(sess.paged, ["show fdb", "show ports", "show vlan"])

    def test_config_commands_are_not_paged(self):
        sess = PagingSession({"show vlan": "VID : 1\nUntagged Ports : 1-10\n"})
        Dlink1100Driver(sess).set_access_vlan(5, 105)
        # The VLAN dump is a view (paged); the config writes are not.
        self.assertEqual(sess.paged, ["show vlan"])
        self.assertIn("config vlan vlanid 105 add untagged 5", sess.sent)
        self.assertIn("config port_vlan 5 pvid 105", sess.sent)

    def test_inherits_des1210_uplink_parsing(self):
        show_vlan = (
            "VID                : 253       VLAN NAME      : mgmt\n"
            "Member Ports       : 9-10\n"
            "Untagged Ports     : \n"
        )
        drv = Dlink1100Driver(PagingSession({"show vlan": show_vlan}))
        self.assertEqual(drv.find_uplink_ports(253), [9, 10])
        self.assertTrue(drv.is_uplink_port(9, 253))


class PagerProtocolTests(unittest.TestCase):
    """The real thing: a switch that pages, read over a real TelnetClient."""

    def test_run_paged_collects_all_pages(self):
        macs = {f"aabb.ccdd.ee0{i}": i for i in range(1, 6)}
        sw = MockSwitch(mac_port=macs, page_size=2).start()   # 5 rows, 2 per page
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        try:
            session = Session(client)
            session.login("admin", "secret")
            out = session.run_paged("show mac address-table")
        finally:
            client.close()

        # Every row from every page is present...
        for mac, port in macs.items():
            self.assertIn(mac, out)
            self.assertIn(f"gi1/0/{port}", out)
        # ...the pager legend is stripped whole, not just the matched marker...
        for leftover in ("Next Page", "--More--", "CTRL+C", "Quit", "Next Entry"):
            self.assertNotIn(leftover, out)
        # ...so what's left is exactly the five data rows.
        rows = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(rows), len(macs), f"unexpected rows: {rows}")

    def test_unpaged_output_reads_normally(self):
        # With no pager involved run_paged must behave exactly like run().
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        try:
            session = Session(client)
            session.login("admin", "secret")
            out = session.run_paged("show mac address-table")
        finally:
            client.close()
        self.assertIn("aabb.ccdd.eeff", out)
        self.assertIn("gi1/0/7", out)


class MeVariantTests(unittest.TestCase):
    """The /ME line must not be swallowed by the plain 'dgs-1100' marker."""

    def test_me_models_get_their_own_driver(self):
        for banner in ("Device Type : DGS-1100-10/ME Gigabit Ethernet Switch",
                       "Device Type : DGS-1100-06/ME",
                       "Device Type : DGS-1100-26/ME"):
            with self.subTest(banner=banner):
                sess = PagingSession({"show switch": banner})
                self.assertEqual(detect_vendor(sess), "dlink_1100_me")

    def test_plain_1100_still_uses_the_non_me_driver(self):
        # The /ME markers must not steal detection from the standard models.
        for banner in ("Device Type : DGS-1100-10 D-Link Smart Switch",
                       "Device Type : DES-1100-16 D-Link"):
            with self.subTest(banner=banner):
                sess = PagingSession({"show switch": banner})
                self.assertEqual(detect_vendor(sess), "dlink_1100")

    def test_registry_has_model(self):
        self.assertIs(get_driver("dlink_1100_me"), Dlink1100MeDriver)

    def test_reads_plainly_like_the_1210_not_the_1100(self):
        # The /ME CLI is the 1210's: it does not carry the 1100's pager quirk,
        # so the listing views use the plain read path.
        sess = PagingSession({"show ports": " 1  Enabled/Auto  Auto  Link Down  Enabled"})
        drv = Dlink1100MeDriver(sess)
        self.assertEqual(drv.list_port_status(), [(1, "down")])
        self.assertEqual(sess.paged, [])
        self.assertIn("show ports", sess.sent)

    def test_uses_the_1210_vlan_command_set(self):
        show_vlan = (
            "VID                : 1         VLAN NAME      : default\n"
            "Member Ports       : 1-10\n"
            "Untagged Ports     : 1-10\n"
            "\n"
            "VID                : 253       VLAN NAME      : mgmt\n"
            "Member Ports       : 9-10\n"
            "Untagged Ports     : \n"
        )
        sess = PagingSession({"show vlan": show_vlan})
        drv = Dlink1100MeDriver(sess)
        # Uplink detection reads 'Member Ports' from the block-style dump...
        self.assertEqual(drv.find_uplink_ports(253), [9, 10])
        drv.set_access_vlan(5, 105)
        # ...and the port move uses the 1210 syntax, not the DES-3200 gvrp one.
        self.assertIn("config vlan vlanid 1 delete 5", sess.sent)
        self.assertIn("config vlan vlanid 105 add untagged 5", sess.sent)
        self.assertIn("config port_vlan 5 pvid 105", sess.sent)
        self.assertFalse(any("gvrp" in c for c in sess.sent))


if __name__ == "__main__":
    unittest.main()
