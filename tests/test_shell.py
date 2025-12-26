"""Tests for the interactive shell module."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

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
def mock_client() -> httpx.Client:
    """Create a mock HTTP client."""
    def _handler(request: httpx.Request) -> httpx.Response:
        # Default successful response
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(_handler)
    return httpx.Client(transport=transport, base_url="http://test:8000")


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for shell."""
    data_dir = tmp_path / ".govee_artnet"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def shell(mock_config: ClientConfig, mock_client: httpx.Client, temp_data_dir: Path) -> GoveeShell:
    """Create a shell instance with mocked dependencies."""
    with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
        mock_home.return_value = temp_data_dir.parent
        with patch.object(GoveeShell, "_connect"):
            shell = GoveeShell(mock_config)
            shell.client = mock_client
            return shell


class TestShellInitialization:
    """Test shell initialization and setup."""

    def test_shell_creates_data_directory(self, temp_data_dir: Path, mock_config: ClientConfig) -> None:
        """Test that shell creates data directory on init."""
        with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
            mock_home.return_value = temp_data_dir.parent
            with patch.object(GoveeShell, "_connect"):
                shell = GoveeShell(mock_config)
                assert shell.data_dir.exists()
                assert shell.data_dir.is_dir()

    def test_shell_loads_empty_bookmarks(self, shell: GoveeShell) -> None:
        """Test that shell starts with empty bookmarks if file doesn't exist."""
        assert shell.bookmarks == {}

    def test_shell_loads_empty_aliases(self, shell: GoveeShell) -> None:
        """Test that shell starts with empty aliases if file doesn't exist."""
        assert shell.aliases == {}

    def test_shell_loads_existing_bookmarks(self, temp_data_dir: Path, mock_config: ClientConfig) -> None:
        """Test that shell loads existing bookmarks from file."""
        bookmarks_file = temp_data_dir / ".govee_artnet" / "bookmarks.json"
        bookmarks_file.parent.mkdir(exist_ok=True)
        bookmarks_data = {"device1": "ABC123", "server1": "http://localhost:8000"}
        bookmarks_file.write_text(json.dumps(bookmarks_data))

        with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
            mock_home.return_value = temp_data_dir.parent
            with patch.object(GoveeShell, "_connect"):
                shell = GoveeShell(mock_config)
                assert shell.bookmarks == bookmarks_data

    def test_shell_handles_corrupt_json_gracefully(
        self, temp_data_dir: Path, mock_config: ClientConfig
    ) -> None:
        """Test that shell handles corrupt JSON files gracefully."""
        bookmarks_file = temp_data_dir / ".govee_artnet" / "bookmarks.json"
        bookmarks_file.parent.mkdir(exist_ok=True)
        bookmarks_file.write_text("invalid json{")

        with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
            mock_home.return_value = temp_data_dir.parent
            with patch.object(GoveeShell, "_connect"):
                shell = GoveeShell(mock_config)
                assert shell.bookmarks == {}  # Falls back to default


class TestShellConnection:
    """Test shell connection management."""

    def test_connect_creates_client(self, mock_config: ClientConfig, temp_data_dir: Path) -> None:
        """Test that connect creates HTTP client."""
        with patch("govee_artnet_lan_bridge.shell.Path.home") as mock_home:
            mock_home.return_value = temp_data_dir.parent
            with patch("govee_artnet_lan_bridge.shell._build_client") as mock_build:
                mock_client = Mock()
                mock_client.get.return_value = Mock(raise_for_status=lambda: None)
                mock_build.return_value = mock_client

                shell = GoveeShell(mock_config)
                assert shell.client is not None

    def test_disconnect_closes_client(self, shell: GoveeShell) -> None:
        """Test that disconnect closes the HTTP client."""
        shell.client = Mock()
        shell.do_disconnect("")
        shell.client.close.assert_called_once()
        assert shell.client is None

    def test_connect_with_url_updates_config(self, shell: GoveeShell) -> None:
        """Test that connect with URL updates the configuration."""
        with patch.object(shell, "_connect"):
            original_url = shell.config.server_url
            new_url = "http://newserver:9000"
            shell.do_connect(new_url)
            assert shell.config.server_url == new_url
            assert shell.config.server_url != original_url


class TestDeviceCommands:
    """Test device management commands."""

    def test_devices_list(self, shell: GoveeShell) -> None:
        """Test devices list command."""
        mock_response = [
            {"id": "device1", "ip": "192.168.1.10", "enabled": True},
            {"id": "device2", "ip": "192.168.1.11", "enabled": False},
        ]

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/devices":
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_devices("list")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response

    def test_devices_enable(self, shell: GoveeShell) -> None:
        """Test devices enable command."""
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            if "/devices/" in str(request.url):
                captured["method"] = request.method
                captured["body"] = json.loads(request.content.decode())
                return httpx.Response(200, json={"id": "device1", "enabled": True})
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output"):
            shell.do_devices("enable device1")

        assert captured["method"] == "PATCH"
        assert captured["body"] == {"enabled": True}

    def test_devices_disable(self, shell: GoveeShell) -> None:
        """Test devices disable command."""
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            if "/devices/" in str(request.url):
                captured["method"] = request.method
                captured["body"] = json.loads(request.content.decode())
                return httpx.Response(200, json={"id": "device1", "enabled": False})
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output"):
            shell.do_devices("disable device1")

        assert captured["method"] == "PATCH"
        assert captured["body"] == {"enabled": False}

    def test_devices_requires_connection(self, shell: GoveeShell, capsys) -> None:
        """Test that device commands require connection."""
        shell.client = None
        shell.do_devices("list")
        captured = capsys.readouterr()
        assert "Not connected" in captured.out


class TestMappingCommands:
    """Test mapping management commands."""

    def test_mappings_list(self, shell: GoveeShell) -> None:
        """Test mappings list command."""
        mock_response = [
            {"id": 1, "device_id": "device1", "universe": 0, "channel": 1},
            {"id": 2, "device_id": "device2", "universe": 1, "channel": 10},
        ]

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/mappings":
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_mappings("list")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response

    def test_mappings_get(self, shell: GoveeShell) -> None:
        """Test mappings get command."""
        mock_response = {"id": 1, "device_id": "device1", "universe": 0}

        def _handler(request: httpx.Request) -> httpx.Response:
            if "/mappings/1" in str(request.url):
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_mappings("get 1")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response

    def test_mappings_delete(self, shell: GoveeShell, capsys) -> None:
        """Test mappings delete command."""
        def _handler(request: httpx.Request) -> httpx.Response:
            if "/mappings/1" in str(request.url) and request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        shell.do_mappings("delete 1")
        captured = capsys.readouterr()
        assert "deleted" in captured.out.lower()

    def test_mappings_channel_map(self, shell: GoveeShell) -> None:
        """Test mappings channel-map command."""
        mock_response = {"0": {}, "1": {}}

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/channel-map":
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_mappings("channel-map")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response


class TestBookmarkManagement:
    """Test bookmark management functionality."""

    def test_bookmark_add(self, shell: GoveeShell, capsys) -> None:
        """Test adding a bookmark."""
        shell.do_bookmark("add mydevice ABC123")
        assert "mydevice" in shell.bookmarks
        assert shell.bookmarks["mydevice"] == "ABC123"
        captured = capsys.readouterr()
        assert "added" in captured.out.lower()

    def test_bookmark_list_empty(self, shell: GoveeShell, capsys) -> None:
        """Test listing empty bookmarks."""
        shell.do_bookmark("list")
        captured = capsys.readouterr()
        assert "no bookmarks" in captured.out.lower() or len(captured.out) > 0

    def test_bookmark_list_with_items(self, shell: GoveeShell) -> None:
        """Test listing bookmarks with items."""
        shell.bookmarks = {"device1": "ABC123", "server1": "http://localhost:8000"}
        with patch.object(shell.console, "print") as mock_print:
            shell.do_bookmark("list")
            # Verify print was called (table display)
            assert mock_print.called

    def test_bookmark_delete(self, shell: GoveeShell, capsys) -> None:
        """Test deleting a bookmark."""
        shell.bookmarks = {"mydevice": "ABC123"}
        shell.do_bookmark("delete mydevice")
        assert "mydevice" not in shell.bookmarks
        captured = capsys.readouterr()
        assert "deleted" in captured.out.lower()

    def test_bookmark_delete_nonexistent(self, shell: GoveeShell, capsys) -> None:
        """Test deleting a non-existent bookmark."""
        shell.do_bookmark("delete nonexistent")
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_bookmark_use_url(self, shell: GoveeShell) -> None:
        """Test using a bookmark with URL."""
        shell.bookmarks = {"server1": "http://newserver:8000"}
        with patch.object(shell, "do_connect") as mock_connect:
            shell.do_bookmark("use server1")
            mock_connect.assert_called_once_with("http://newserver:8000")

    def test_bookmark_use_device_id(self, shell: GoveeShell, capsys) -> None:
        """Test using a bookmark with device ID."""
        shell.bookmarks = {"mydevice": "ABC123"}
        shell.do_bookmark("use mydevice")
        captured = capsys.readouterr()
        assert "ABC123" in captured.out


class TestAliasManagement:
    """Test alias management functionality."""

    def test_alias_add(self, shell: GoveeShell, capsys) -> None:
        """Test adding an alias."""
        shell.do_alias("add dl devices list")
        assert "dl" in shell.aliases
        assert shell.aliases["dl"] == "devices list"
        captured = capsys.readouterr()
        assert "added" in captured.out.lower()

    def test_alias_list_empty(self, shell: GoveeShell, capsys) -> None:
        """Test listing empty aliases."""
        shell.do_alias("list")
        captured = capsys.readouterr()
        assert "no aliases" in captured.out.lower() or len(captured.out) > 0

    def test_alias_delete(self, shell: GoveeShell, capsys) -> None:
        """Test deleting an alias."""
        shell.aliases = {"dl": "devices list"}
        shell.do_alias("delete dl")
        assert "dl" not in shell.aliases
        captured = capsys.readouterr()
        assert "deleted" in captured.out.lower()

    def test_alias_expansion(self, shell: GoveeShell) -> None:
        """Test that aliases are expanded in precmd."""
        shell.aliases = {"dl": "devices list"}
        expanded = shell.precmd("dl")
        assert expanded == "devices list"

    def test_alias_expansion_with_args(self, shell: GoveeShell) -> None:
        """Test alias expansion with additional arguments."""
        shell.aliases = {"dev": "devices"}
        with patch.object(shell.console, "print"):
            expanded = shell.precmd("dev enable device1")
            assert "devices enable device1" in expanded


class TestSessionManagement:
    """Test session save/load functionality."""

    def test_session_save(self, shell: GoveeShell, capsys) -> None:
        """Test saving a session."""
        shell.config = ClientConfig(
            server_url="http://test:8000", api_key=None, api_bearer_token=None, output="table"
        )
        shell.do_session("save test_session")
        captured = capsys.readouterr()
        assert "saved" in captured.out.lower()

        # Verify session file exists
        sessions_file = shell.data_dir / "sessions.json"
        assert sessions_file.exists()
        sessions = json.loads(sessions_file.read_text())
        assert "test_session" in sessions
        assert sessions["test_session"]["server_url"] == "http://test:8000"
        assert sessions["test_session"]["output"] == "table"

    def test_session_load(self, shell: GoveeShell) -> None:
        """Test loading a session."""
        # Create a session file
        sessions_file = shell.data_dir / "sessions.json"
        sessions = {
            "prod": {"server_url": "http://prod:8000", "output": "yaml"}
        }
        sessions_file.write_text(json.dumps(sessions))

        with patch.object(shell, "_connect"):
            shell.do_session("load prod")
            assert shell.config.server_url == "http://prod:8000"
            assert shell.config.output == "yaml"

    def test_session_list(self, shell: GoveeShell) -> None:
        """Test listing sessions."""
        sessions_file = shell.data_dir / "sessions.json"
        sessions = {
            "prod": {"server_url": "http://prod:8000", "output": "yaml"},
            "dev": {"server_url": "http://dev:8000", "output": "json"},
        }
        sessions_file.write_text(json.dumps(sessions))

        with patch.object(shell.console, "print") as mock_print:
            shell.do_session("list")
            assert mock_print.called

    def test_session_delete(self, shell: GoveeShell, capsys) -> None:
        """Test deleting a session."""
        sessions_file = shell.data_dir / "sessions.json"
        sessions = {"test": {"server_url": "http://test:8000", "output": "json"}}
        sessions_file.write_text(json.dumps(sessions))

        shell.do_session("delete test")
        captured = capsys.readouterr()
        assert "deleted" in captured.out.lower()

        # Verify session was removed
        updated_sessions = json.loads(sessions_file.read_text())
        assert "test" not in updated_sessions


class TestOutputFormatSwitching:
    """Test output format switching."""

    def test_output_switch_to_yaml(self, shell: GoveeShell, capsys) -> None:
        """Test switching output to YAML."""
        shell.do_output("yaml")
        assert shell.config.output == "yaml"
        captured = capsys.readouterr()
        assert "yaml" in captured.out.lower()

    def test_output_switch_to_table(self, shell: GoveeShell, capsys) -> None:
        """Test switching output to table."""
        shell.do_output("table")
        assert shell.config.output == "table"
        captured = capsys.readouterr()
        assert "table" in captured.out.lower()

    def test_output_switch_to_json(self, shell: GoveeShell, capsys) -> None:
        """Test switching output to JSON."""
        shell.do_output("json")
        assert shell.config.output == "json"
        captured = capsys.readouterr()
        assert "json" in captured.out.lower()

    def test_output_invalid_format(self, shell: GoveeShell, capsys) -> None:
        """Test that invalid output format shows usage."""
        original_format = shell.config.output
        shell.do_output("invalid")
        assert shell.config.output == original_format  # Unchanged
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "current" in captured.out.lower()


class TestErrorHandling:
    """Test error handling in shell commands."""

    def test_devices_without_connection(self, shell: GoveeShell, capsys) -> None:
        """Test device commands without connection show error."""
        shell.client = None
        shell.do_devices("list")
        captured = capsys.readouterr()
        assert "not connected" in captured.out.lower()

    def test_mappings_without_connection(self, shell: GoveeShell, capsys) -> None:
        """Test mapping commands without connection show error."""
        shell.client = None
        shell.do_mappings("list")
        captured = capsys.readouterr()
        assert "not connected" in captured.out.lower()

    def test_http_error_handling(self, shell: GoveeShell, capsys) -> None:
        """Test HTTP errors are handled gracefully."""
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "Internal server error"})

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        shell.do_devices("list")
        captured = capsys.readouterr()
        assert "error" in captured.out.lower()

    def test_connection_error_handling(self, shell: GoveeShell, capsys) -> None:
        """Test connection errors are handled gracefully."""
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection failed")

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        shell.do_devices("list")
        captured = capsys.readouterr()
        assert "error" in captured.out.lower()


class TestUtilityCommands:
    """Test utility commands."""

    def test_version_command(self, shell: GoveeShell, capsys) -> None:
        """Test version command displays version info."""
        shell.do_version("")
        captured = capsys.readouterr()
        assert "version" in captured.out.lower() or "shell" in captured.out.lower()

    def test_tips_command(self, shell: GoveeShell, capsys) -> None:
        """Test tips command displays helpful tips."""
        shell.do_tips("")
        captured = capsys.readouterr()
        assert len(captured.out) > 0  # Tips are displayed

    def test_clear_command(self, shell: GoveeShell) -> None:
        """Test clear command uses console.clear()."""
        with patch.object(shell.console, "clear") as mock_clear:
            shell.do_clear("")
            mock_clear.assert_called_once()

    def test_exit_command(self, shell: GoveeShell, capsys) -> None:
        """Test exit command closes client and returns True."""
        shell.client = Mock()
        result = shell.do_exit("")
        assert result is True
        shell.client.close.assert_called_once()
        captured = capsys.readouterr()
        assert "goodbye" in captured.out.lower()

    def test_quit_command(self, shell: GoveeShell) -> None:
        """Test quit is alias for exit."""
        shell.client = Mock()
        result = shell.do_quit("")
        assert result is True


class TestStatusCommands:
    """Test status and health commands."""

    def test_health_command(self, shell: GoveeShell) -> None:
        """Test health command."""
        mock_response = {"status": "ok"}

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_health("")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response

    def test_status_command(self, shell: GoveeShell) -> None:
        """Test status command."""
        mock_response = {
            "discovered_count": 2,
            "manual_count": 1,
            "queue_depth": 5,
        }

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/status":
                return httpx.Response(200, json=mock_response)
            return httpx.Response(404)

        shell.client = httpx.Client(
            transport=httpx.MockTransport(_handler), base_url="http://test:8000"
        )

        with patch("govee_artnet_lan_bridge.shell._print_output") as mock_print:
            shell.do_status("")
            mock_print.assert_called_once()
            assert mock_print.call_args[0][0] == mock_response
