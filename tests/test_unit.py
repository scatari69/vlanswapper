"""Юнит-тесты без сети: форматы MAC и разбор IAC в TelnetClient."""

import unittest

from vlanswapper import mac
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
        return cls.__new__(cls)  # парсеры не трогают сессию

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


class TelnetParsingTests(unittest.TestCase):
    def test_iac_negotiation_stripped(self):
        c = TelnetClient("127.0.0.1")           # сокет не открываем
        c._feed(bytes([ord("A"), ord("B"), IAC, WILL, ECHO, ord("C"), IAC, DO, ECHO, ord("D")]))
        self.assertEqual(c.take_buffer(), "ABCD")

    def test_escaped_iac_becomes_single_byte(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("X"), IAC, IAC, ord("Y")]))
        self.assertEqual(c.take_buffer(), "X\xffY")

    def test_split_sequence_across_feeds(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("A"), IAC]))          # последовательность оборвана
        c._feed(bytes([WILL, ECHO, ord("B")]))   # продолжение
        self.assertEqual(c.take_buffer(), "AB")


if __name__ == "__main__":
    unittest.main()
