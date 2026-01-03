"""Tests for wrap_govee_command function."""

from dmx_lan_bridge.devices import wrap_govee_command


def test_wrap_govee_command_color_and_turn_on() -> None:
    """Test that color + turn:on sends both commands."""
    payload = {
        "color": {"r": 255, "g": 100, "b": 50},
        "turn": "on"
    }
    result = wrap_govee_command(payload)

    assert "_multiple" in result
    cmds = result["_multiple"]
    assert len(cmds) == 2

    # First command should be turn:on with numeric value 1
    assert cmds[0]["msg"]["cmd"] == "turn"
    assert cmds[0]["msg"]["data"]["value"] == 1

    # Second command should be colorwc
    assert cmds[1]["msg"]["cmd"] == "colorwc"
    assert cmds[1]["msg"]["data"]["color"] == {"r": 255, "g": 100, "b": 50}


def test_wrap_govee_command_color_and_turn_off() -> None:
    """Test that color + turn:off only sends turn-off (color is ignored when turning off)."""
    payload = {
        "color": {"r": 255, "g": 0, "b": 0},
        "turn": "off"
    }
    result = wrap_govee_command(payload)

    # When turning off, only the turn command should be sent
    assert "msg" in result
    assert result["msg"]["cmd"] == "turn"
    assert result["msg"]["data"]["value"] == 0


def test_wrap_govee_command_color_turn_brightness() -> None:
    """Test that color + turn + brightness sends all three commands."""
    payload = {
        "color": {"r": 100, "g": 150, "b": 200},
        "turn": "on",
        "brightness": 128
    }
    result = wrap_govee_command(payload)

    assert "_multiple" in result
    cmds = result["_multiple"]
    assert len(cmds) == 3

    # First: turn with numeric value 1
    assert cmds[0]["msg"]["cmd"] == "turn"
    assert cmds[0]["msg"]["data"]["value"] == 1

    # Second: colorwc
    assert cmds[1]["msg"]["cmd"] == "colorwc"
    assert cmds[1]["msg"]["data"]["color"] == {"r": 100, "g": 150, "b": 200}

    # Third: brightness
    assert cmds[2]["msg"]["cmd"] == "brightness"
    assert cmds[2]["msg"]["data"]["value"] == 128


def test_wrap_govee_command_turn_brightness_only() -> None:
    """Test that turn + brightness (no color) sends both commands."""
    payload = {
        "turn": "on",
        "brightness": 200
    }
    result = wrap_govee_command(payload)

    assert "_multiple" in result
    cmds = result["_multiple"]
    assert len(cmds) == 2

    assert cmds[0]["msg"]["cmd"] == "turn"
    assert cmds[0]["msg"]["data"]["value"] == 1

    assert cmds[1]["msg"]["cmd"] == "brightness"
    assert cmds[1]["msg"]["data"]["value"] == 200


def test_wrap_govee_command_brightness_only() -> None:
    """Test that brightness-only command works."""
    payload = {"brightness": 150}
    result = wrap_govee_command(payload)

    assert "msg" in result
    assert result["msg"]["cmd"] == "brightness"
    assert result["msg"]["data"]["value"] == 150


def test_wrap_govee_command_color_only() -> None:
    """Test that color-only command works."""
    payload = {"color": {"r": 255, "g": 255, "b": 255}}
    result = wrap_govee_command(payload)

    assert "msg" in result
    assert result["msg"]["cmd"] == "colorwc"
    assert result["msg"]["data"]["color"] == {"r": 255, "g": 255, "b": 255}


def test_wrap_govee_command_turn_only() -> None:
    """Test that turn-only command works."""
    payload = {"turn": "off"}
    result = wrap_govee_command(payload)

    assert "msg" in result
    assert result["msg"]["cmd"] == "turn"
    assert result["msg"]["data"]["value"] == 0
