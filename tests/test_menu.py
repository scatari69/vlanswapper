"""Interactive-menu test against the mock switch (input/isatty are patched)."""

import unittest
from unittest import mock

from tests.mock_switch import MockSwitch
from vlanswapper.cli import run


def _run_with_input(sw: MockSwitch, answers, extra=()):
    it = iter(answers)
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", lambda *a: next(it)):
        return run(["--host", "127.0.0.1", "--port-tcp", str(sw.port),
                    "--username", "admin", "--password", "secret", *extra])


class MenuTests(unittest.TestCase):
    def test_menu_specify_port(self):
        sw = MockSwitch().start()
        rc = _run_with_input(sw, ["1", "5", "y", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("switchport access vlan 105", "\n".join(sw.received))

    def test_menu_find_by_mac(self):
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        rc = _run_with_input(sw, ["2", "aa:bb:cc:dd:ee:ff", "y", "0"])
        self.assertEqual(rc, 0)
        joined = "\n".join(sw.received)
        self.assertIn("show mac address-table address", joined)
        self.assertIn("switchport access vlan 107", joined)  # port 7 -> VLAN 107

    def test_menu_show_mac_table(self):
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        rc = _run_with_input(sw, ["3", "0"])
        self.assertEqual(rc, 0)
        joined = "\n".join(sw.received)
        # Full table dump, no address — the new menu item.
        self.assertIn("show mac address-table", joined)
        self.assertFalse(any("show mac address-table address" in c for c in sw.received))
        # Viewing the table must not configure anything.
        self.assertNotIn("switchport access vlan", joined)

    def test_menu_show_ports(self):
        sw = MockSwitch().start()
        with mock.patch("sys.stderr") as err:
            err.isatty.return_value = False
            rc = _run_with_input(sw, ["4", "0"])
        self.assertEqual(rc, 0)
        joined = "\n".join(sw.received)
        self.assertIn("show interfaces status", joined)
        # Viewing the port list configures nothing.
        self.assertNotIn("switchport access vlan", joined)
        # What was collected for printing: port → status (up/down/disabled).
        printed = "".join(str(c.args[0]) for c in err.write.call_args_list if c.args)
        self.assertIn("🟢", printed)  # gi1/0/1 connected -> up
        self.assertIn("🔴", printed)  # gi1/0/2 notconnect -> down
        self.assertIn("⚫", printed)  # gi1/0/3 disabled

    def test_uplink_port_blocked(self):
        # Port 5 is an uplink (VLAN 253 trunk): the guard must cancel the change.
        sw = MockSwitch(uplink_ports={5}).start()
        rc = _run_with_input(sw, ["1", "5", "0"])
        self.assertEqual(rc, 0)  # the menu exited normally (item cancelled, not an error)
        self.assertNotIn("switchport access vlan 105", "\n".join(sw.received))

    def test_uplink_port_forced(self):
        # With --force the guard is lifted and the port is configured.
        sw = MockSwitch(uplink_ports={5}).start()
        rc = _run_with_input(sw, ["1", "5", "y", "0"], extra=["--force"])
        self.assertEqual(rc, 0)
        self.assertIn("switchport access vlan 105", "\n".join(sw.received))

    def test_uplink_guard_off_allows(self):
        # --uplink-vlan 0 disables the check entirely.
        sw = MockSwitch(uplink_ports={5}).start()
        rc = _run_with_input(sw, ["1", "5", "y", "0"], extra=["--uplink-vlan", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("switchport access vlan 105", "\n".join(sw.received))

    def test_menu_immediate_exit(self):
        sw = MockSwitch().start()
        rc = _run_with_input(sw, ["0"])
        self.assertEqual(rc, 0)
        # After login/detect there must be no configuration commands.
        self.assertNotIn("configure terminal", sw.received)


if __name__ == "__main__":
    unittest.main()
