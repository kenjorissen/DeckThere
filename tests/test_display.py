import fcntl
import os
import pty
import re
import resource
import select
import shlex
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"


def functions(name, marker):
    return (
        (ROOT / name).read_text().split(f"# BEGIN {marker}\n", 1)[1].split(f"# END {marker}", 1)[0]
    )


class BrightnessTests(unittest.TestCase):
    def apply(self, preference, maximum="599000", original="400000", product="Galileo"):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            level = folder / "brightness"
            level.write_text(original + "\n")
            (folder / "max_brightness").write_text(maximum + "\n")
            setting = folder / "percent"
            if preference is not None:
                setting.write_text(preference)
            model = folder / "product_name"
            if product is not None:
                model.write_text(product + "\n")
            env = dict(
                os.environ,
                BRIGHTNESS_FILE=str(level),
                BRIGHTNESS_PERCENT_FILE=str(setting),
                DMI_PRODUCT_FILE=str(model),
            )
            result = subprocess.run(
                [
                    "bash",
                    "-euc",
                    functions("deckthere-root", "BRIGHTNESS_FUNCTIONS")
                    + '\nbrightness=""; apply_brightness; printf "saved=%s\\n" "$brightness"',
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return level.read_text().strip(), result

    def test_percentage_conversion_and_original_preservation(self):
        for percent, expected in (
            ("0", "1207"),
            ("5", "2027"),
            ("10", "3405"),
            ("20", "9604"),
            ("30", "27086"),
            ("40", "76387"),
            ("50", "215423"),
            ("60", "279370"),
            ("70", "362298"),
            ("80", "469843"),
            ("90", "593677"),
            ("95", "593677"),
            ("100", "593677"),
            ("010", "3405"),
        ):
            with self.subTest(percent=percent):
                actual, result = self.apply(percent + "\n")
                self.assertEqual(actual, expected)
                self.assertIn("saved=400000", result.stdout)
                self.assertIn("calibrated Galileo OLED", result.stderr)

    def test_other_models_and_ranges_use_generic_gamma(self):
        for product, maximum, expected in (
            ("Jupiter", "599000", "3779"),
            ("Unknown", "599000", "3779"),
            (None, "599000", "3779"),
            ("Galileo", "65535", "413"),
        ):
            with self.subTest(product=product, maximum=maximum):
                actual, result = self.apply("10", maximum=maximum, product=product)
                self.assertEqual(actual, expected)
                self.assertIn("generic perceptual gamma 2.2", result.stderr)

    def test_rounds_to_nearest_hardware_step(self):
        actual, _ = self.apply("10", maximum="255", original="200")
        self.assertEqual(actual, "2")

    def test_generic_endpoints(self):
        for percent, expected in (("0", "0"), ("100", "255")):
            actual, _ = self.apply(percent, maximum="255", original="200", product="Jupiter")
            self.assertEqual(actual, expected)

    def test_curves_are_monotonic_and_bounded_at_every_step(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "product_name"
            for product in ("Galileo", "Jupiter"):
                with self.subTest(product=product):
                    model.write_text(product + "\n")
                    result = subprocess.run(
                        [
                            "bash",
                            "-euc",
                            functions("deckthere-root", "BRIGHTNESS_FUNCTIONS")
                            + '\nfor step in {0..100}; do brightness_target 599000 "$step"; done',
                        ],
                        env=dict(os.environ, DMI_PRODUCT_FILE=str(model)),
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    values = [int(value) for value in result.stdout.splitlines()]
                    self.assertEqual(len(values), 101)
                    self.assertEqual(values, sorted(values))
                    self.assertTrue(all(0 <= value <= 599000 for value in values))

    def test_missing_invalid_and_injection_values_fall_back(self):
        for value in (None, "", "101", "-1", "5%", "5.5", "5\n6", "$(exit 77)", "9999999999999999"):
            with self.subTest(value=value):
                actual, result = self.apply(value)
                self.assertEqual(actual, "1339")
                self.assertIn("using 1%", result.stderr)

    def read_preference(self, path):
        def limit_memory():
            resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))

        source = functions("deckthere-root", "BRIGHTNESS_FUNCTIONS") + "\nread_brightness_percent"
        return subprocess.run(
            ["bash", "-euc", source],
            env=dict(os.environ, BRIGHTNESS_PERCENT_FILE=str(path)),
            capture_output=True,
            text=True,
            timeout=3,
            preexec_fn=limit_memory,
        )

    def test_one_gibibyte_file_is_rejected_under_small_memory_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "percent"
            with path.open("wb") as stream:
                stream.write(b"5\n")
                stream.truncate(1024**3)  # Sparse: do not allocate a gigabyte of data.
            result = self.read_preference(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "1\n")
            self.assertIn("missing/invalid", result.stderr)

    def test_binary_junk_and_extra_lines_are_rejected_but_crlf_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "percent"
            for data, expected in ((b"25\x00", "1\n"), (b"25\n\n", "1\n"), (b"25\r\n", "25\n")):
                with self.subTest(data=data):
                    path.write_bytes(data)
                    result = self.read_preference(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)

    def test_fifo_directory_and_symlink_are_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / "valid"
            target.write_text("25\n")
            link = folder / "link"
            link.symlink_to(target)
            fifo = folder / "fifo"
            os.mkfifo(fifo)
            for path in (folder, link, fifo):
                with self.subTest(path=path):
                    result = self.read_preference(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "1\n")
                    self.assertIn("missing/invalid", result.stderr)

    def test_bad_maximum_or_original_leaves_backlight_unchanged(self):
        for maximum, original in (("0", "400000"), ("invalid", "400000"), ("599000", "invalid")):
            actual, result = self.apply("5", maximum=maximum, original=original)
            self.assertEqual(actual, original)
            self.assertIn("leaving backlight unchanged", result.stderr)


class DashboardTests(unittest.TestCase):
    def run_dashboard(self, supplies, commands, route=None, sockets=None, route6=None):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, values in supplies.items():
                supply = folder / name
                supply.mkdir()
                for key, value in values.items():
                    (supply / key).write_text(value + "\n")
            env = dict(os.environ, POWER_SUPPLY_ROOT=str(folder))
            source = functions("deckthere.sh", "DASHBOARD_FUNCTIONS")

            def stub(value):
                return "return 1" if value is None else f"printf '%s\\n' {shlex.quote(value)}"

            mocks = (
                '\ntimeout() { shift; "$@"; }\n'
                f"ip() {{ if [[ $2 == -4 ]]; then {stub(route)}; else {stub(route6)}; fi; }}\n"
                f"ss() {{ {stub(sockets)}; }}\n"
            )
            init = mocks + (
                "\nui_active=false; ui_dirty=true; last_display=''; rows=24; cols=80; "
                "clock_time='12:34'; local_ip='Unavailable'; client_ips='Unavailable'; "
                "client_count=0; client_status='Status unavailable'; sample_battery\n"
            )
            result = subprocess.run(
                ["bash", "-euc", source + init + commands],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_battery_percentage_and_status(self):
        for status in ("Charging", "Discharging", "Full", "Not charging"):
            text = self.run_dashboard(
                {"BAT1": {"type": "Battery", "capacity": "87", "status": status}}, "paint_dashboard"
            )
            self.assertIn(f"87% ({status})", text)
            self.assertNotIn("\033", text)

    def test_peripherals_are_ignored_and_missing_data_is_safe(self):
        supplies = {
            "mouse": {"type": "Battery", "scope": "Device", "capacity": "99"},
            "bad": {"type": "Battery", "capacity": "$(exit 77)"},
            "charger": {"type": "Mains"},
        }
        text = self.run_dashboard(supplies, "paint_dashboard")
        self.assertIn("--% (Unavailable)", text)

    def test_does_not_redraw_unchanged_values(self):
        supplies = {"BAT0": {"type": "Battery", "capacity": "50", "status": "Charging"}}
        text = self.run_dashboard(supplies, "paint_dashboard; sample_battery; paint_dashboard")
        self.assertEqual(text.count("Server running"), 1)

    def test_full_dashboard_and_compact_terminal(self):
        supplies = {"BAT0": {"type": "Battery", "capacity": "100", "status": "Full"}}
        text = self.run_dashboard(supplies, "ui_active=true; paint_dashboard")
        self.assertIn("SERVER RUNNING", text)
        self.assertEqual(text.count("HOLD 2s"), 4)
        self.assertIn("#", text)
        text = self.run_dashboard(supplies, "ui_active=true; rows=10; cols=40; paint_dashboard")
        self.assertIn("Battery: 100% (Full)", text)

    def test_scaled_layout_places_clock_left_battery_right_and_status_below(self):
        supplies = {"BAT0": {"type": "Battery", "capacity": "100", "status": "Full"}}
        for cols, rows, height, title_width in (
            (80, 24, 5, 35),
            (90, 30, 8, 53),
            (128, 40, 10, 70),
        ):
            with self.subTest(cols=cols, rows=rows):
                text = self.run_dashboard(
                    supplies, f"ui_active=true; cols={cols}; rows={rows}; paint_dashboard"
                )
                writes = [
                    (int(r), int(c), value)
                    for r, c, value in re.findall(r"\x1b\[(\d+);(\d+)H([^\x1b]*)", text)
                ]
                labels = {value: (r, c) for r, c, value in writes}
                clock = labels["LOCAL TIME"]
                battery = labels["BATTERY"]
                self.assertEqual(clock[0], battery[0])
                self.assertLess(clock[1], battery[1])
                blocks = [(r, c, value) for r, c, value in writes if "#" in value]
                self.assertEqual(len(blocks), 3 * height)
                if cols == 80:
                    # DeckThere: lowercase bodies sit below the capital/ascender tops.
                    self.assertEqual(
                        blocks[0][2],
                        " ".join(("## ", "   ", "   ", "#  ", "###", "#  ", "   ", "   ", "   ")),
                    )
                self.assertTrue(all(len(value) == title_width for _, _, value in blocks[:height]))
                self.assertEqual(len({r for r, _, _ in blocks[:height]}), height)
                self.assertTrue(all(c == clock[1] for _, c, _ in blocks[height : 2 * height]))
                self.assertTrue(all(c == battery[1] for _, c, _ in blocks[2 * height :]))
                self.assertGreater(labels["SERVER RUNNING"][0], max(r for r, _, _ in blocks))
                self.assertGreater(labels["Local IP: Unavailable"][0], labels["SERVER RUNNING"][0])
                self.assertTrue(
                    all(
                        1 <= r <= rows and 1 <= c and c + len(value) - 1 <= cols
                        for r, c, value in writes
                    )
                )

    def test_network_clients_are_deduplicated_sorted_and_numeric(self):
        text = self.run_dashboard(
            {},
            "sample_network; paint_dashboard",
            route="1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.20 uid 1000",
            sockets=(
                "0 0 192.168.1.20:7575 192.168.1.9:40000\n"
                "0 0 192.168.1.20:7575 192.168.1.8:40001\n"
                "0 0 192.168.1.20:7575 192.168.1.9:40002\n"
                "0 0 192.168.1.20:7575 evil\033[31m:40003"
            ),
        )
        self.assertIn("Local IP: 192.168.1.20", text)
        self.assertIn("TCP clients: 2", text)
        self.assertIn("Clients: 192.168.1.8, 192.168.1.9", text)
        self.assertNotIn("evil", text)

    def test_ipv6_route_and_peer(self):
        text = self.run_dashboard(
            {},
            "sample_network; paint_dashboard",
            route6="2606:4700:4700::1111 dev wlan0 src 2001:db8::2",
            sockets="0 0 [::]:7575 [2001:db8::1]:40000",
        )
        self.assertIn("Local IP: 2001:db8::2", text)
        self.assertIn("Clients: 2001:db8::1", text)

    def test_missing_tools_and_no_connections_are_distinct(self):
        text = self.run_dashboard({}, "sample_network; paint_dashboard")
        self.assertIn("Status unavailable", text)
        self.assertIn("Local IP: Unavailable", text)
        text = self.run_dashboard({}, "sample_network; paint_dashboard", sockets="")
        self.assertIn("Waiting for client", text)
        self.assertIn("Clients: None", text)

    def test_clock_and_network_changes_redraw(self):
        text = self.run_dashboard(
            {},
            "paint_dashboard; clock_time=12:35; paint_dashboard; "
            "local_ip=192.168.1.20; paint_dashboard; "
            "client_ips=192.168.1.8; client_count=1; "
            "client_status='TCP clients: 1'; paint_dashboard; paint_dashboard",
        )
        self.assertEqual(text.count("Server running"), 4)
        self.assertIn("12:35", text)
        self.assertIn("Clients: 192.168.1.8", text)

    def test_battery_color_thresholds(self):
        for value, color in (("15", "31"), ("30", "33"), ("31", "32")):
            text = self.run_dashboard(
                {"BAT0": {"type": "Battery", "capacity": value}},
                "ui_active=true; paint_dashboard",
            )
            self.assertIn(f"\033[1;{color}m", text)

    def test_text_is_clipped_and_outside_rows_are_skipped(self):
        text = self.run_dashboard({}, "rows=2; cols=10; text_at 1 8 ABCDEFG; text_at 3 1 HIDDEN")
        self.assertEqual(text, "\033[1;8HABC")

    def test_shutdown_screen_is_large_idempotent_and_has_compact_fallback(self):
        text = self.run_dashboard({}, "ui_active=true; show_shutdown; show_shutdown")
        self.assertEqual(text.count("Waiting for VirtualHere to stop..."), 1)
        self.assertIn("#", text)
        self.assertNotIn("SERVER RUNNING", text)
        text = self.run_dashboard({}, "ui_active=true; cols=40; rows=8; show_shutdown")
        self.assertIn("SHUTTING DOWN", text)
        text = self.run_dashboard({}, "show_shutdown; show_shutdown")
        self.assertEqual(text.count("SHUTTING DOWN"), 1)
        self.assertNotIn("\033", text)

    def test_service_shutdown_notification_replaces_dashboard_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            helper = folder / "helper"
            helper.write_text("#!/bin/bash\n[[ $1 != keepalive ]] || exit 2\n")
            sudo = folder / "sudo"
            sudo.write_text('#!/bin/bash\nshift\nexec "$@"\n')
            systemctl = folder / "systemctl"
            systemctl.write_text(
                '#!/bin/bash\n[[ $1 == is-active && ! -e "$ACTIVE_CHECK" ]] || exit 1\ntouch "$ACTIVE_CHECK"\n'
            )
            (folder / "deckthere_idle.py").write_text("# idle helper stub\n")
            launcher = folder / "launcher"
            launcher.write_text(
                (ROOT / "deckthere.sh")
                .read_text()
                .replace("/home/.deckthere/bin/deckthere-root", str(helper))
                .replace("/usr/bin/systemctl", str(systemctl))
            )
            for command in (helper, sudo, systemctl, launcher):
                command.chmod(0o755)
            result = subprocess.run(
                [str(launcher)],
                stdin=subprocess.DEVNULL,
                env=dict(
                    os.environ,
                    PATH=f"{folder}:" + os.environ["PATH"],
                    ACTIVE_CHECK=str(folder / "active"),
                ),
                capture_output=True,
                text=True,
                timeout=4,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("SHUTTING DOWN"), 1)
            self.assertNotIn("Server running", result.stdout)
            self.assertNotIn("ERROR", result.stderr)

    def test_ctrl_c_restores_terminal_and_stops_service(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            helper = folder / "helper"
            helper.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$1" >> "$CALLS"\n'
                "if [[ $1 == stop ]]; then sleep 0.1; echo MOCK_STOP_FINISHED; fi\n"
            )
            sudo = folder / "sudo"
            sudo.write_text('#!/bin/bash\nshift\nexec "$@"\n')
            systemctl = folder / "systemctl"
            systemctl.write_text("#!/bin/bash\n[[ $1 == is-active ]]\n")
            ip = folder / "ip"
            ss = folder / "ss"
            for command in (ip, ss):
                command.write_text("#!/bin/bash\nexit 1\n")
            for command in (helper, sudo, systemctl, ip, ss):
                command.chmod(0o755)
            (folder / "deckthere_idle.py").write_text("# idle helper stub\n")
            launcher = folder / "launcher"
            launcher.write_text(
                (ROOT / "deckthere.sh")
                .read_text()
                .replace("/home/.deckthere/bin/deckthere-root", str(helper))
                .replace("/usr/bin/systemctl", str(systemctl))
                .replace("/sys/class/power_supply", str(folder / "no-battery"))
            )
            launcher.chmod(0o755)
            calls = folder / "calls"
            env = dict(
                os.environ,
                PATH=str(folder) + ":" + os.environ["PATH"],
                TERM="xterm-256color",
                CALLS=str(calls),
            )
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            process = subprocess.Popen(
                [str(launcher)],
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
            )
            os.close(slave)
            output = b""
            try:
                deadline = time.monotonic() + 3
                while b"SERVER RUNNING" not in output:
                    self.assertLess(time.monotonic(), deadline, output.decode(errors="replace"))
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                process.send_signal(signal.SIGINT)
                process.wait(timeout=3)
                while select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                self.assertEqual(process.returncode, 0, output.decode(errors="replace"))
                self.assertIn(b"\x1b[?1049h", output)
                self.assertIn(b"\x1b[?25h\x1b[?1049l", output)
                self.assertLess(
                    output.index(b"Waiting for VirtualHere to stop..."),
                    output.index(b"MOCK_STOP_FINISHED"),
                )
                self.assertLess(
                    output.index(b"MOCK_STOP_FINISHED"), output.index(b"\x1b[?25h\x1b[?1049l")
                )
                self.assertEqual(calls.read_text().splitlines()[-1], "stop")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                os.close(master)

    def test_resize_forces_redraw(self):
        text = self.run_dashboard({}, "paint_dashboard; cols=100; ui_dirty=true; paint_dashboard")
        self.assertEqual(text.count("Server running"), 2)


if __name__ == "__main__":
    unittest.main()
