"""Tests for advanced shell features (watch, batch, monitor, logs)."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, call, patch

import httpx
import pytest

from govee_artnet_lan_bridge.cli import ClientConfig
from govee_artnet_lan_bridge.shell import GoveeShell


@pytest.fixture
def mock_config() -> ClientConfig:
    """Create a mock client configuration."""
    return ClientConfig(
        server_url="http://test:8000",
        api_key="test-key",
        api_bearer_token=None,
        output="json",
    )


@pytest.fixture
def shell(mock_config: ClientConfig, tmp_path: Path) -> GoveeShell:
    """Create a shell instance with mocked dependencies."""
    with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
        mock_home.return_value = tmp_path
        with patch.object(GoveeShell, "_connect"):
            shell = GoveeShell(mock_config)

            def _handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"status": "ok"})

            shell.client = httpx.Client(
                transport=httpx.MockTransport(_handler), base_url="http://test:8000"
            )
            return shell


class TestBatchExecution:
    """Test batch command execution from files."""

    def test_batch_executes_commands_from_file(self, shell: GoveeShell, tmp_path: Path) -> None:
        """Test batch execution of commands from file."""
        batch_file = tmp_path / "commands.txt"
        batch_file.write_text(
            "# This is a comment\n"
            "version\n"
            "\n"  # Empty line
            "output json\n"
        )

        with patch.object(shell, "onecmd") as mock_onecmd:
            shell.do_batch(str(batch_file))
            # Should execute non-comment, non-empty lines
            calls = [c[0][0] for c in mock_onecmd.call_args_list]
            assert "version" in calls
            assert "output json" in calls

    def test_batch_skips_comments_and_empty_lines(self, shell: GoveeShell, tmp_path: Path) -> None:
        """Test that batch skips comments and empty lines."""
        batch_file = tmp_path / "commands.txt"
        batch_file.write_text(
            "# Comment line\n"
            "  # Another comment\n"
            "\n"
            "version\n"
        )

        with patch.object(shell, "onecmd") as mock_onecmd:
            shell.do_batch(str(batch_file))
            # Only 'version' should be executed
            assert mock_onecmd.call_count == 1
            assert mock_onecmd.call_args[0][0] == "version"

    def test_batch_handles_missing_file(self, shell: GoveeShell, capsys) -> None:
        """Test batch with non-existent file shows error."""
        shell.do_batch("nonexistent.txt")
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_batch_handles_file_errors(self, shell: GoveeShell, tmp_path: Path) -> None:
        """Test batch handles file read errors gracefully."""
        batch_file = tmp_path / "commands.txt"
        batch_file.write_text("version\n")
        batch_file.chmod(0o000)  # Remove read permissions

        try:
            with patch.object(shell.console, "print") as mock_print:
                shell.do_batch(str(batch_file))
                # Should print error message
                error_calls = [c for c in mock_print.call_args_list if "error" in str(c).lower()]
                assert len(error_calls) > 0
        finally:
            batch_file.chmod(0o644)  # Restore permissions


class TestWatchMode:
    """Test watch mode for continuous monitoring."""

    def test_watch_devices(self, shell: GoveeShell) -> None:
        """Test watch mode for devices."""
        iterations = 0

        def mock_sleep(duration: float) -> None:
            nonlocal iterations
            iterations += 1
            if iterations >= 2:
                raise KeyboardInterrupt()

        with patch("time.sleep", mock_sleep):
            with patch.object(shell, "do_devices") as mock_devices:
                with patch.object(shell.console, "clear"):
                    with patch.object(shell.console, "print"):
                        shell.do_watch("devices 0.1")

        # Should have called do_devices at least twice
        assert mock_devices.call_count >= 2
        assert all(c[0][0] == "list" for c in mock_devices.call_args_list)

    def test_watch_status(self, shell: GoveeShell) -> None:
        """Test watch mode for status."""
        iterations = 0

        def mock_sleep(duration: float) -> None:
            nonlocal iterations
            iterations += 1
            if iterations >= 2:
                raise KeyboardInterrupt()

        with patch("time.sleep", mock_sleep):
            with patch.object(shell, "do_status") as mock_status:
                with patch.object(shell.console, "clear"):
                    with patch.object(shell.console, "print"):
                        shell.do_watch("status 0.1")

        assert mock_status.call_count >= 2

    def test_watch_dashboard(self, shell: GoveeShell) -> None:
        """Test watch mode for dashboard."""
        iterations = 0

        def mock_sleep(duration: float) -> None:
            nonlocal iterations
            iterations += 1
            if iterations >= 1:
                raise KeyboardInterrupt()

        with patch("time.sleep", mock_sleep):
            with patch.object(shell, "_monitor_dashboard") as mock_dashboard:
                with patch.object(shell.console, "clear"):
                    with patch.object(shell.console, "print"):
                        shell.do_watch("dashboard 0.1")

        assert mock_dashboard.call_count >= 1

    def test_watch_uses_custom_interval(self, shell: GoveeShell) -> None:
        """Test that watch mode uses custom interval."""
        sleep_duration = None

        def mock_sleep(duration: float) -> None:
            nonlocal sleep_duration
            sleep_duration = duration
            raise KeyboardInterrupt()

        with patch("time.sleep", mock_sleep):
            with patch.object(shell, "do_devices"):
                with patch.object(shell.console, "clear"):
                    with patch.object(shell.console, "print"):
                        shell.do_watch("devices 3.5")

        assert sleep_duration == 3.5

    def test_watch_clears_screen(self, shell: GoveeShell) -> None:
        """Test that watch mode clears screen between updates."""
        iterations = 0

        def mock_sleep(duration: float) -> None:
            nonlocal iterations
            iterations += 1
            if iterations >= 2:
                raise KeyboardInterrupt()

        with patch("time.sleep", mock_sleep):
            with patch.object(shell, "do_devices"):
                with patch.object(shell.console, "clear") as mock_clear:
                    with patch.object(shell.console, "print"):
                        shell.do_watch("devices 0.1")

        # Clear should be called for each iteration
        assert mock_clear.call_count >= 2

    def test_watch_requires_connection(self, shell: GoveeShell, capsys) -> None:
        """Test that watch mode requires connection."""
        shell.client = None
        shell.do_watch("devices")
        captured = capsys.readouterr()
        assert "not connected" in captured.out.lower()


class TestMonitorCommands:
    """Test monitor commands (dashboard, stats)."""

    def test_monitor_dashboard(self, shell: GoveeShell) -> None:
        """Test monitor dashboard command."""
        health_response = {
            "status": "ok",
            "subsystems": {
                "discovery": {"status": "ok"},
                "sender": {"status": "ok"},
            },
        }
        status_response = {
            "discovered_count": 5,
            "manual_count": 2,
            "queue_depth": 10,
        }

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json=health_response)
            elif request.url.path == "/status":
                return httpx.Response(200, json=status_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch.object(shell.console, "print") as mock_print:
            with patch.object(shell.console, "status"):
                with patch.object(shell.console, "rule"):
                    shell.do_monitor("dashboard")

        # Verify console methods were called
        assert mock_print.called

    def test_monitor_stats(self, shell: GoveeShell) -> None:
        """Test monitor stats command."""
        status_response = {"discovered_count": 5, "queue_depth": 10}

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/status":
                return httpx.Response(200, json=status_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_monitor("stats")

        mock_print.assert_called_once()

    def test_monitor_requires_connection(self, shell: GoveeShell, capsys) -> None:
        """Test that monitor commands require connection."""
        shell.client = None
        shell.do_monitor("dashboard")
        captured = capsys.readouterr()
        assert "not connected" in captured.out.lower()


class TestLogsCommands:
    """Test log viewing commands."""

    def test_logs_basic(self, shell: GoveeShell) -> None:
        """Test basic logs command."""
        logs_response = {
            "logs": [
                {"timestamp": "2024-01-01 10:00:00", "level": "INFO", "message": "Test log"},
            ],
            "total": 1,
            "lines": 1,
        }

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/logs":
                return httpx.Response(200, json=logs_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            with patch("builtins.print") as mock_builtin_print:
                shell.do_logs("")

        # Should print summary and logs
        assert mock_builtin_print.called or mock_print.called

    def test_logs_with_filters(self, shell: GoveeShell) -> None:
        """Test logs command with filters."""
        captured_params = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/logs":
                # Extract query parameters
                captured_params.update(dict(request.url.params))
                return httpx.Response(
                    200, json={"logs": [], "total": 0, "lines": 0}
                )
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output"):
            with patch("builtins.print"):
                shell.do_logs("--lines 100 --level ERROR --logger govee.discovery")

        assert captured_params.get("lines") == "100"
        assert captured_params.get("level") == "ERROR"
        assert captured_params.get("logger") == "govee.discovery"

    def test_logs_search(self, shell: GoveeShell) -> None:
        """Test logs search command."""
        search_response = {
            "logs": [{"message": "Found device"}],
            "count": 1,
        }

        captured_params = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            if "/logs/search" in str(request.url):
                captured_params.update(dict(request.url.params))
                return httpx.Response(200, json=search_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            with patch("builtins.print") as mock_builtin_print:
                shell.do_logs('search "device discovered"')

        assert captured_params.get("pattern") == "device discovered"
        assert mock_print.called or mock_builtin_print.called

    def test_logs_search_with_regex(self, shell: GoveeShell) -> None:
        """Test logs search with regex flag."""
        captured_params = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            if "/logs/search" in str(request.url):
                captured_params.update(dict(request.url.params))
                return httpx.Response(200, json={"logs": [], "count": 0})
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output"):
            with patch("builtins.print"):
                shell.do_logs('search "error.*timeout" --regex')

        assert captured_params.get("regex") == "true"

    def test_logs_tail_missing_websockets(self, shell: GoveeShell, capsys) -> None:
        """Test logs tail without websockets library shows error."""
        with patch("builtins.__import__", side_effect=ImportError):
            shell.do_logs("tail")

        captured = capsys.readouterr()
        assert "websockets" in captured.out.lower()
        assert "not installed" in captured.out.lower()

    def test_logs_requires_connection(self, shell: GoveeShell, capsys) -> None:
        """Test that logs commands require connection."""
        shell.client = None
        shell.do_logs("")
        captured = capsys.readouterr()
        assert "not connected" in captured.out.lower()


class TestWebSocketLogTailing:
    """Test WebSocket log tailing functionality."""

    def test_logs_tail_with_websockets(self, shell: GoveeShell) -> None:
        """Test logs tail command with mocked WebSocket."""
        mock_ws = MagicMock()
        messages = [
            json.dumps({"timestamp": "2024-01-01", "level": "INFO", "logger": "test", "message": "msg1"}),
            json.dumps({"timestamp": "2024-01-01", "level": "ERROR", "logger": "test", "message": "msg2"}),
        ]
        recv_count = 0

        def mock_recv(timeout: float) -> str:
            nonlocal recv_count
            if recv_count >= len(messages):
                raise KeyboardInterrupt()
            msg = messages[recv_count]
            recv_count += 1
            return msg

        mock_ws.recv = mock_recv
        mock_ws.__enter__ = lambda self: mock_ws
        mock_ws.__exit__ = lambda self, *args: None

        mock_ws_client = MagicMock()
        mock_ws_client.connect.return_value = mock_ws

        with patch("websockets.sync.client", mock_ws_client):
            with patch("builtins.print") as mock_print:
                shell._logs_tail([])

        # Should have printed both messages
        print_calls = [str(c) for c in mock_print.call_args_list]
        assert any("msg1" in c for c in print_calls)
        assert any("msg2" in c for c in print_calls)

    def test_logs_tail_with_filters(self, shell: GoveeShell) -> None:
        """Test logs tail with level and logger filters."""
        mock_ws = MagicMock()
        sent_data = []

        def mock_send(data: str) -> None:
            sent_data.append(json.loads(data))

        mock_ws.send = mock_send
        mock_ws.recv = lambda timeout: json.dumps({"type": "ping"})  # Just ping to avoid iteration
        mock_ws.__enter__ = lambda self: mock_ws
        mock_ws.__exit__ = lambda self, *args: None

        mock_ws_client = MagicMock()
        mock_ws_client.connect.return_value = mock_ws

        # Mock recv to raise KeyboardInterrupt immediately after first call
        recv_count = 0

        def mock_recv_interrupt(timeout: float) -> str:
            nonlocal recv_count
            recv_count += 1
            if recv_count > 1:
                raise KeyboardInterrupt()
            return json.dumps({"type": "ping"})

        mock_ws.recv = mock_recv_interrupt

        with patch("websockets.sync.client", mock_ws_client):
            with patch("builtins.print"):
                shell._logs_tail(["--level", "ERROR", "--logger", "govee.discovery"])

        # Should have sent filter configuration
        assert len(sent_data) == 1
        assert sent_data[0]["level"] == "ERROR"
        assert sent_data[0]["logger"] == "govee.discovery"

    def test_logs_tail_skips_ping_messages(self, shell: GoveeShell) -> None:
        """Test that logs tail skips ping messages."""
        mock_ws = MagicMock()
        messages = [
            json.dumps({"type": "ping"}),
            json.dumps({"timestamp": "2024-01-01", "level": "INFO", "logger": "test", "message": "real"}),
        ]
        recv_count = 0

        def mock_recv(timeout: float) -> str:
            nonlocal recv_count
            if recv_count >= len(messages):
                raise KeyboardInterrupt()
            msg = messages[recv_count]
            recv_count += 1
            return msg

        mock_ws.recv = mock_recv
        mock_ws.__enter__ = lambda self: mock_ws
        mock_ws.__exit__ = lambda self, *args: None

        mock_ws_client = MagicMock()
        mock_ws_client.connect.return_value = mock_ws

        with patch("websockets.sync.client", mock_ws_client):
            with patch("builtins.print") as mock_print:
                shell._logs_tail([])

        # Should only print the real message, not the ping
        print_calls = [str(c) for c in mock_print.call_args_list]
        message_prints = [c for c in print_calls if "real" in c]
        assert len(message_prints) > 0

    def test_logs_tail_handles_websocket_errors(self, shell: GoveeShell, capsys) -> None:
        """Test that logs tail handles WebSocket connection errors."""
        mock_ws_client = MagicMock()
        mock_ws_client.connect.side_effect = Exception("Connection failed")

        with patch("websockets.sync.client", mock_ws_client):
            shell._logs_tail([])

        captured = capsys.readouterr()
        assert "error" in captured.out.lower()

    def test_logs_tail_handles_keyboard_interrupt(self, shell: GoveeShell, capsys) -> None:
        """Test that logs tail handles Ctrl+C gracefully."""
        mock_ws = MagicMock()
        mock_ws.recv = lambda timeout: (_ for _ in ()).throw(KeyboardInterrupt())
        mock_ws.__enter__ = lambda self: mock_ws
        mock_ws.__exit__ = lambda self, *args: None

        mock_ws_client = MagicMock()
        mock_ws_client.connect.return_value = mock_ws

        with patch("websockets.sync.client", mock_ws_client):
            shell._logs_tail([])

        captured = capsys.readouterr()
        assert "stopped" in captured.out.lower()

    def test_logs_tail_handles_timeout(self, shell: GoveeShell) -> None:
        """Test that logs tail handles recv timeouts."""
        mock_ws = MagicMock()
        timeout_count = 0

        def mock_recv(timeout: float) -> str:
            nonlocal timeout_count
            timeout_count += 1
            if timeout_count < 3:
                raise TimeoutError()
            else:
                raise KeyboardInterrupt()  # Exit after a few timeouts

        mock_ws.recv = mock_recv
        mock_ws.__enter__ = lambda self: mock_ws
        mock_ws.__exit__ = lambda self, *args: None

        mock_ws_client = MagicMock()
        mock_ws_client.connect.return_value = mock_ws

        with patch("websockets.sync.client", mock_ws_client):
            with patch("builtins.print"):
                shell._logs_tail([])

        # Should have handled timeouts gracefully
        assert timeout_count >= 3
