#!/usr/bin/env python3
"""Unit tests for Railway per-profile gateway lifecycle control."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profile_gateway_control import ProfileGatewayController


class FakeProcess:
    def __init__(self, pid=4321):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


class FakePopenFactory:
    def __init__(self):
        self.calls = []
        self.process = FakeProcess()

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return self.process


class ProfileGatewayControllerTests(unittest.TestCase):
    def test_missing_state_defaults_to_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=Path(tmp),
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
            )

            status = controller.status()

            self.assertFalse(status["running"])
            self.assertEqual("stopped", status["desired"])
            self.assertEqual("rafapessoal", status["profile"])

    def test_start_spawns_whitelisted_command_and_persists_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = FakePopenFactory()
            command = ["hermes", "-p", "rafapessoal", "gateway", "run"]
            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=Path(tmp),
                command=command,
                popen_factory=factory,
            )

            status = controller.start()

            self.assertEqual(command, factory.calls[0][0])
            self.assertTrue(factory.calls[0][1]["start_new_session"])
            self.assertTrue(status["running"])
            self.assertEqual("running", status["desired"])
            self.assertEqual(4321, status["pid"])
            saved = controller._read_state()
            self.assertEqual({"desired": "running", "pid": 4321, "last_error": None}, saved)

    def test_start_reuses_valid_persisted_gateway_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway_control.json").write_text(
                '{"desired":"running","pid":9876,"last_error":null}',
                encoding="utf-8",
            )
            factory = FakePopenFactory()
            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                popen_factory=factory,
                pid_alive=lambda pid: pid == 9876,
                read_cmdline=lambda pid: "hermes -p rafapessoal gateway run",
            )

            status = controller.start()

            self.assertEqual([], factory.calls)
            self.assertTrue(status["running"])
            self.assertEqual(9876, status["pid"])

    def test_stop_terminates_only_valid_profile_gateway_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway_control.json").write_text(
                '{"desired":"running","pid":7654,"last_error":null}',
                encoding="utf-8",
            )
            alive = {7654}
            terminated = []

            def terminate(pid):
                terminated.append(pid)
                alive.discard(pid)

            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                pid_alive=lambda pid: pid in alive,
                read_cmdline=lambda pid: "hermes -p rafapessoal gateway run",
                terminate_pid=terminate,
            )

            status = controller.stop()

            self.assertEqual([7654], terminated)
            self.assertFalse(status["running"])
            self.assertEqual("stopped", status["desired"])
            self.assertIsNone(status["pid"])

    def test_restart_replaces_valid_profile_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway_control.json").write_text(
                '{"desired":"running","pid":7654,"last_error":null}',
                encoding="utf-8",
            )
            alive = {7654}
            terminated = []
            factory = FakePopenFactory()

            def terminate(pid):
                terminated.append(pid)
                alive.discard(pid)

            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                popen_factory=factory,
                pid_alive=lambda pid: pid in alive,
                read_cmdline=lambda pid: "hermes -p rafapessoal gateway run",
                terminate_pid=terminate,
            )

            status = controller.restart()

            self.assertEqual([7654], terminated)
            self.assertEqual(1, len(factory.calls))
            self.assertTrue(status["running"])
            self.assertEqual("running", status["desired"])
            self.assertEqual(4321, status["pid"])

    def test_reconcile_restarts_crashed_gateway_when_desired_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway_control.json").write_text(
                '{"desired":"running","pid":7654,"last_error":null}',
                encoding="utf-8",
            )
            factory = FakePopenFactory()
            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                popen_factory=factory,
                pid_alive=lambda pid: False,
                read_cmdline=lambda pid: "",
            )

            status = controller.reconcile()

            self.assertEqual(1, len(factory.calls))
            self.assertTrue(status["running"])
            self.assertEqual("running", status["desired"])

    def test_status_adopts_existing_hermes_gateway_pid_without_control_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway.pid").write_text('{"pid":2468}', encoding="utf-8")
            factory = FakePopenFactory()
            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                popen_factory=factory,
                pid_alive=lambda pid: pid == 2468,
                read_cmdline=lambda pid: "hermes -p rafapessoal gateway run",
            )

            status = controller.start()

            self.assertEqual([], factory.calls)
            self.assertTrue(status["running"])
            self.assertEqual("running", status["desired"])
            self.assertEqual(2468, status["pid"])

    def test_stop_wins_race_against_monitor_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gateway_control.json").write_text(
                '{"desired":"running","pid":7654,"last_error":null}',
                encoding="utf-8",
            )
            spawn_entered = threading.Event()
            release_spawn = threading.Event()
            process = FakeProcess()
            thread_errors = []

            def blocking_popen(*args, **kwargs):
                spawn_entered.set()
                release_spawn.wait(timeout=2)
                return process

            def terminate(pid):
                process.returncode = 0

            controller = ProfileGatewayController(
                profile="rafapessoal",
                profile_home=home,
                command=["hermes", "-p", "rafapessoal", "gateway", "run"],
                popen_factory=blocking_popen,
                pid_alive=lambda pid: False,
                read_cmdline=lambda pid: "",
                terminate_pid=terminate,
            )

            def run_in_thread(operation):
                try:
                    operation()
                except BaseException as exc:
                    thread_errors.append(exc)

            reconcile_thread = threading.Thread(
                target=run_in_thread, args=(controller.reconcile,)
            )
            reconcile_thread.start()
            self.assertTrue(spawn_entered.wait(timeout=1))

            stop_thread = threading.Thread(
                target=run_in_thread, args=(controller.stop,)
            )
            stop_thread.start()
            release_spawn.set()
            reconcile_thread.join(timeout=2)
            stop_thread.join(timeout=2)

            status = controller.status()
            self.assertEqual([], thread_errors)
            self.assertFalse(status["running"])
            self.assertEqual("stopped", status["desired"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
