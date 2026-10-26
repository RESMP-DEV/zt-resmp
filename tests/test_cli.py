#!/usr/bin/env python3
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import zt_resmp


class CliTests(unittest.TestCase):
    def test_ping_summary_parses_macos(self) -> None:
        output = """
        5 packets transmitted, 5 packets received, 0.0% packet loss
        round-trip min/avg/max/stddev = 0.099/0.146/0.213/0.044 ms
        """
        self.assertEqual(zt_resmp.parse_ping(output), (5, 0.146))

    def test_deep_merge_preserves_siblings(self) -> None:
        result = zt_resmp.deep_merge(
            {"settings": {"keep": 1, "replace": 2}, "physical": {}},
            {"settings": {"replace": 3}},
        )
        self.assertEqual(result, {"settings": {"keep": 1, "replace": 3}, "physical": {}})

    def test_route_parse_macos(self) -> None:
        output = "   route to: 198.51.100.1\ndestination: 198.51.100.1\n  interface: feth0\n"
        self.assertEqual(zt_resmp.parse_route_interface(output, "198.51.100.1"), "feth0")

    def test_profile_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / "local.conf"
            path.write_text(json.dumps({"settings": {"keep": 1}}), encoding="utf-8")
            config = zt_resmp.config_from_mapping(
                {
                    "network_id": "aaaaaaaaaaaaaaaa",
                    "profile": {"settings": {"allowTcpFallbackRelay": True}},
                }
            )
            result = zt_resmp.apply_profile(config, str(home), write=False)
            self.assertTrue(result["changed"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["settings"], {"keep": 1})

    def test_profile_replace_removes_unspecified_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / "local.conf"
            path.write_text(json.dumps({"settings": {"stale": 1}}), encoding="utf-8")
            config = zt_resmp.config_from_mapping(
                {
                    "network_id": "aaaaaaaaaaaaaaaa",
                    "profile": {"settings": {"allowTcpFallbackRelay": True}},
                }
            )
            result = zt_resmp.apply_profile(config, str(home), write=True, replace=True)
            self.assertTrue(result["changed"])
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"settings": {"allowTcpFallbackRelay": True}},
            )

    def test_expected_route_is_not_hardcoded(self) -> None:
        config = zt_resmp.config_from_mapping({"network_id": "aaaaaaaaaaaaaaaa"})
        self.assertEqual(config.expected_route, "")

    def test_tailscale_prefix_overlap(self) -> None:
        self.assertTrue(zt_resmp.prefixes_overlap("10.0.0.0/8", "10.1.0.0/16"))
        self.assertFalse(zt_resmp.prefixes_overlap("10.0.0.0/8", "192.0.2.0/24"))
        self.assertFalse(zt_resmp.prefixes_overlap("not-a-network", "10.0.0.0/8"))

    def test_collects_tailscale_routes(self) -> None:
        state = {
            "Self": {"PrimaryRoutes": ["100.64.0.0/10"]},
            "Peer": {"node": {"AdvertisedRoutes": ["192.0.2.0/24"]}},
        }
        self.assertEqual(
            zt_resmp.tailscale_route_prefixes(state),
            {"100.64.0.0/10", "192.0.2.0/24"},
        )

    def test_parses_linux_interface_state(self) -> None:
        output = """
        [{"ifname":"zt0","flags":["UP","POINTOPOINT"],"oper_state":"UNKNOWN",
          "addr_info":[{"family":"inet","local":"10.10.0.2","prefixlen":16}]}]
        """
        up, addresses = zt_resmp.parse_linux_interface_json(output)
        self.assertTrue(up)
        self.assertEqual(addresses, ["10.10.0.2/16"])

    def test_parser_accepts_options_after_verbs(self) -> None:
        args = zt_resmp.build_parser().parse_args(
            ["doctor", "--config", "network.json", "--probe-underlay"]
        )
        self.assertEqual(args.command, "doctor")
        self.assertEqual(args.config, Path("network.json"))
        self.assertTrue(args.probe_underlay)

        restored = zt_resmp.build_parser().parse_args(
            ["profile", "restore", "backup.json", "--write", "--home", "/tmp/zt"]
        )
        self.assertEqual(restored.action, "restore")
        self.assertEqual(restored.home, "/tmp/zt")

    def test_rejects_invalid_configuration(self) -> None:
        with self.assertRaises(zt_resmp.ZtResmpError):
            zt_resmp.config_from_mapping({"network_id": "not-hex"})

    def test_restore_profiles_valid_json_and_backs_up_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            target = home / "local.conf"
            target.write_text('{"settings":{"current":true}}', encoding="utf-8")
            source = home / "restore.json"
            source.write_text('{"settings":{"replacement":true}}', encoding="utf-8")
            result = zt_resmp.restore_profile(str(home), source)
            backup = Path(result["backup"])
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"settings": {"replacement": True}},
            )
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8")),
                {"settings": {"current": True}},
            )

    def test_profile_restore_does_not_require_network_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            source = home / "restore.json"
            source.write_text('{"settings":{"replacement":true}}', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = zt_resmp.main(
                    ["profile", "restore", str(source), "--write", "--home", str(home)]
                )
            self.assertEqual(exit_code, 0)
            self.assertIn('"ok": true', output.getvalue())

    def test_central_absolute_path_is_rejected_before_http(self) -> None:
        with self.assertRaises(zt_resmp.ZtResmpError):
            zt_resmp.central_request("https://example.invalid", "unused-token")

    def test_macos_ping_uses_millisecond_wait(self) -> None:
        recorded: dict[str, object] = {}

        def fake_run(argv: list[str], timeout: float = 0) -> object:
            recorded["argv"] = argv
            recorded["timeout"] = timeout
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": "1 packets transmitted, 1 packets received\n",
                    "stderr": "",
                },
            )()

        original_run = zt_resmp.run
        original_system = zt_resmp.platform.system
        zt_resmp.run = fake_run  # type: ignore[assignment]
        zt_resmp.platform.system = lambda: "Darwin"  # type: ignore[assignment]
        try:
            result = zt_resmp.probe_address("198.51.100.10")
        finally:
            zt_resmp.run = original_run  # type: ignore[assignment]
            zt_resmp.platform.system = original_system  # type: ignore[assignment]
        self.assertTrue(result["passed"])
        self.assertIn("-W1000", recorded["argv"])


if __name__ == "__main__":
    unittest.main()
