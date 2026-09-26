import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import deckthere_dashboard  # noqa: E402


class DashboardTests(unittest.TestCase):
    def test_battery_skips_peripheral_and_invalid_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, scope, capacity in (("headset", "Device", "100"), ("system", "System", "42")):
                node = root / name
                node.mkdir()
                for field, value in (
                    ("scope", scope),
                    ("type", "Battery"),
                    ("capacity", capacity),
                    ("status", "Charging"),
                ):
                    (node / field).write_text(value)
            self.assertEqual(deckthere_dashboard.battery(root), ("42%", "Charging"))
            (root / "system/capacity").write_text("999")
            self.assertEqual(deckthere_dashboard.battery(root), ("--%", "Unavailable"))

    def test_network_deduplicates_peers_and_filters_untrusted_output(self):
        with patch.object(
            deckthere_dashboard,
            "command",
            side_effect=[
                "1.1.1.1 dev wlan0 src 192.168.1.2",
                "0 0 192.168.1.2:7575 192.168.1.3:123\n0 0 192.168.1.2:7575 192.168.1.3:456\n0 0 local bad:42\n",
            ],
        ):
            self.assertEqual(deckthere_dashboard.network(), ("192.168.1.2", "192.168.1.3"))
        for address in ("fe80::1%\x1b", "<script>", "192.168.1.1\n"):
            self.assertIsNone(deckthere_dashboard.address(address))

    def test_missing_network_commands_are_unavailable(self):
        with patch.object(deckthere_dashboard, "command", return_value=None):
            self.assertEqual(deckthere_dashboard.network(), ("Unavailable", "Unavailable"))
