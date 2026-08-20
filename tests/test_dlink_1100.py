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
        # No PVID step at all — membership alone moves the port.
        self.assertFalse(any("pvid" in c for c in sess.sent))

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

    # Real 'show ports' output from a DGS-1100-10/ME: each port wraps onto a
    # second line (the MDI setting continues underneath), and the listing pages.
    SHOW_PORTS = (
        " Port  State/          Settings              Connection          Address\n"
        "       MDI       Speed/Duplex/FlowCtrl   Speed/Duplex/FlowCtrl   Learning\n"
        " ----- --------  ---------------------   ---------------------   --------\n"
        " 1     Enabled   Auto/Disabled           100M/Full/Disabled      Enabled\n"
        "       Auto\n"
        " 2     Enabled   Auto/Disabled           Link Down               Enabled\n"
        "       Auto\n"
        " 3     Enabled   Auto/Disabled           100M/Full/Disabled      Enabled\n"
        "       Auto\n"
    )

    def test_listing_views_are_paged_like_the_1100(self):
        # Confirmed on hardware: 'disable clipaging' succeeds but ports/VLANs
        # still page, so these views must not use the plain read path.
        sess = PagingSession({"show ports": self.SHOW_PORTS,
                              "show vlan": "VID : 253\nMember Ports : 9-10\n"})
        drv = Dlink1100MeDriver(sess)
        drv.list_port_status()
        drv.find_uplink_ports(253)
        self.assertEqual(sess.paged, ["show ports", "show vlan"])

    def test_parses_the_real_two_line_port_table(self):
        sess = PagingSession({"show ports": self.SHOW_PORTS})
        # The 'Auto' continuation lines carry no port number and are skipped.
        self.assertEqual(Dlink1100MeDriver(sess).list_port_status(),
                         [(1, "up"), (2, "down"), (3, "up")])

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
        # No PVID step at all — membership alone moves the port.
        self.assertFalse(any("pvid" in c for c in sess.sent))
        self.assertFalse(any("gvrp" in c for c in sess.sent))


# Verbatim 'show ports' output from a DGS-1100-10/ME (8 ports, alternating link
# state), including the two-line-per-port wrap and the trailing blank filler.
ME_SHOW_PORTS = "\r\n".join([
    " Port  State/          Settings              Connection          Address",
    "       MDI       Speed/Duplex/FlowCtrl   Speed/Duplex/FlowCtrl   Learning",
    " ----- --------  ---------------------   ---------------------   --------",
] + [
    line
    for port in range(1, 9)
    for line in (
        f" {port}     Enabled   Auto/Disabled           "
        + ("100M/Full/Disabled      Enabled" if port % 2 else "Link Down               Enabled"),
        "       Auto",
    )
] + ["", ""])


class MeHardwareFormatTests(unittest.TestCase):
    """The whole chain on real /ME data: pager → collected text → port list."""

    class MeSwitch(MockSwitch):
        # The legend this firmware actually prints (note: no 'Next Entry' here).
        PAGER = b"CTRL+C ESC q Quit SPACE n Next Page p Previous Page r Refresh"

        def _reply(self, line, prompt):
            if line.lower().startswith("show ports"):
                return ME_SHOW_PORTS.encode(), prompt
            return super()._reply(line, prompt)

    def test_port_list_survives_the_real_pager(self):
        sw = self.MeSwitch(page_size=6).start()      # forces several pages
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        try:
            session = Session(client)
            session.login("admin", "secret")
            driver = get_driver("dlink_1100_me")(session)
            ports = driver.list_port_status()
        finally:
            client.close()

        self.assertEqual(ports, [(1, "up"), (2, "down"), (3, "up"), (4, "down"),
                                 (5, "up"), (6, "down"), (7, "up"), (8, "down")])


class RefreshingPagerTests(unittest.TestCase):
    """'show ports' is a live screen: it redraws instead of ever ending."""

    class RefreshingSwitch(MockSwitch):
        PAGER = b"CTRL+C ESC q Quit SPACE n Next Page p Previous Page r Refresh"

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.keys: list[bytes] = []       # every pager keypress received

        def _send_paged(self, conn, reply, prompt):
            # Everything fits one screen; any key but 'q' just redraws it.
            conn.sendall(b"\r\n" + reply + b"\r\n" + self.PAGER)
            while True:
                key = conn.recv(1)
                if not key:
                    return
                self.keys.append(key)
                if key in (b"q", b"Q", b"\x1b", b"\x03"):
                    conn.sendall(b"\r\n" + prompt)
                    return
                conn.sendall(b"\r\n" + reply + b"\r\n" + self.PAGER)

        def _reply(self, line, prompt):
            if line.lower().startswith("show ports"):
                return ME_SHOW_PORTS.encode(), prompt
            return super()._reply(line, prompt)

    def test_redrawing_screen_is_left_with_the_quit_key(self):
        sw = self.RefreshingSwitch(page_size=1).start()   # force the paged path
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        try:
            session = Session(client)
            session.login("admin", "secret")
            driver = get_driver("dlink_1100_me")(session)
            ports = driver.list_port_status()
            # The pager was quit, so the prompt is back and the session usable.
            follow_up = session.run("show mac address-table")
        finally:
            client.close()

        self.assertEqual(ports, [(1, "up"), (2, "down"), (3, "up"), (4, "down"),
                                 (5, "up"), (6, "down"), (7, "up"), (8, "down")])
        self.assertIn(b"q", sw.keys)                 # we actually left the pager
        self.assertLess(len(sw.keys), 5, "should stop as soon as the screen repeats")
        self.assertIn("aabb.ccdd.eeff", follow_up)   # session not stranded


if __name__ == "__main__":
    unittest.main()
