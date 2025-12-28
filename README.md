# Govee ArtNet LAN Bridge

This project bridges ArtNet DMX input to Govee LAN devices, allowing you to control Govee smart lights using standard lighting control software like QLC+, Chamsys MagicQ, or any other ArtNet-compatible controller.

## Architecture

The bridge consists of two components:

1. **Bridge Server** (`govee-artnet-bridge`) - Runs as a daemon and provides:
   - ArtNet listener (default port 6454)
   - REST API server (default port 8000)
   - Automatic device discovery
   - Device health monitoring

2. **CLI Client** (`govee-artnet`) - Command-line tool for managing the bridge:
   - List and manage devices
   - Create and manage DMX channel mappings
   - Query server status

## Quick Start

### 1. Install

See [INSTALL.md](INSTALL.md) for detailed installation instructions.

```bash
# Install from source
pip install -e .

# Or install from package
pip install govee-artnet-lan-bridge
```

### 2. Start the Bridge Server

```bash
# Start with default settings
govee-artnet-bridge

# Or with custom configuration
govee-artnet-bridge --config /path/to/config.toml
```

The server will:
- Listen for ArtNet packets on port 6454
- Start the REST API on port 8000
- Automatically discover Govee devices on your network

### 3. Discover Devices

The CLI launches an interactive shell by default:

```bash
# Start the interactive shell (default behavior)
govee-artnet

# Or run commands directly without the shell
govee-artnet devices list
```

Discovery responses that include a `model_number` automatically pull metadata from the bundled capability catalog. Device payloads now surface `model_number`, `device_type`, and length/segment hints so mappings can be validated without guesswork:

```json
{
  "id": "AA:BB:CC:DD:EE:FF",
  "model_number": "H6160",
  "device_type": "led_strip",
  "length_meters": 5.0,
  "segment_count": null,
  "capabilities": {
    "model_number": "H6160",
    "device_type": "led_strip",
    "brightness": true,
    "color_temperature": true,
    "color_modes": ["color", "ct"],
    "color_temp_range": [2000, 9000]
  }
}
```

### 4. Create DMX Mappings

Map DMX channels to your devices using templates:

```bash
# Map an RGB light strip to channels 1-3 on universe 0
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 1 \
  --template rgb

# Map an RGBW light to channels 10-13
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:01" \
  --universe 0 \
  --start-channel 10 \
  --template rgbw
```

### 5. Send ArtNet

Point your lighting software at the bridge server's IP address and start controlling your lights!

## Interactive CLI Shell

The `govee-artnet` CLI launches an interactive shell by default with real-time monitoring and log viewing:

```bash
# Start interactive shell (default - just run govee-artnet)
govee-artnet

# Or explicitly use the shell command
govee-artnet shell

# Shell features:
govee> logs tail                    # Tail logs in real-time (like tail -f)
govee> logs search "error"          # Search through logs
govee> monitor dashboard            # Live system dashboard
govee> devices watch                # Watch device state changes
govee> devices list                 # All existing CLI commands work!
```

**Key Shell Features:**
- 📊 **Real-time monitoring** - Live dashboards for devices, ArtNet, queue, and health
- 📝 **Log viewing & tailing** - View, search, and stream logs with filtering
- ⌨️  **Command history & autocomplete** - Tab completion and persistent history
- 🔖 **Bookmarks & aliases** - Save frequently used devices and commands
- 📜 **Scripting support** - Execute batch commands from files
- 🎨 **Rich formatting** - Beautiful tables and colored output

See the **[CLI Shell Guide](README_CLI_SHELL.md)** for complete documentation and examples.

## Available Templates

| Template | Channels | Layout | Use Case |
|----------|----------|--------|----------|
| `rgb` | 3 | R, G, B | Standard RGB fixtures |
| `rgbw` | 4 | R, G, B, W | RGB + dedicated white channel |
| `brightness_rgb` | 4 | Brightness, R, G, B | Master dimmer + RGB color |
| `rgbwa` | 5 | R, G, B, W, Brightness | RGBW color + master dimmer |
| `rgbaw` | 5 | Brightness, R, G, B, W | Master dimmer + RGBW color |
| `brgbwct` | 6 | Brightness, R, G, B, W, CT | Full control with color temperature |

### Single Channel Mappings

For individual field control, use single channel mappings instead of templates:

| Field | Aliases | Description |
|-------|---------|-------------|
| `power` | - | Power on/off (DMX >= 128 = on, < 128 = off) |
| `brightness` | - | Brightness control (0-255) |
| `r` | `red` | Red channel only |
| `g` | `green` | Green channel only |
| `b` | `blue` | Blue channel only |
| `w` | `white` | White channel only |
| `ct` | `color_temp` | Color temperature in Kelvin |

```bash
# Create a power control mapping
govee-artnet mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --field power

# Create a brightness control mapping
govee-artnet mappings create --device-id AA:BB:CC:DD:EE:FF --channel 5 --field brightness

# Use field aliases for convenience
govee-artnet mappings create --device-id AA:BB:CC:DD:EE:FF --channel 10 --field red
```

## CLI Commands

### Device Management

```bash
# List all devices
govee-artnet devices list

# Add a manual device
govee-artnet devices add --id "..." --ip "192.168.1.100" --model-number "H61XX" --device-type "led_strip"

# Enable/disable a device
govee-artnet devices enable "AA:BB:CC:DD:EE:FF"
govee-artnet devices disable "AA:BB:CC:DD:EE:FF"

# Send a quick command (on/off/brightness/color/kelvin)
govee-artnet devices command "AA:BB:CC:DD:EE:FF" --on --brightness 200 --color ff8800
govee-artnet devices command "AA:BB:CC:DD:EE:FF" --kelvin 64
```

- `--on`/`--off` send the native Govee LAN `turn` command instead of manipulating brightness.
- `--brightness` and `--kelvin` accept 0-255 sliders. Kelvin values are scaled to the device's supported color-temperature range when available.
- `--color` accepts RGB hex strings (e.g., `ff3366` or `#ff3366`); shorthand three-character forms expand automatically.

### Mapping Management

```bash
# List all mappings
govee-artnet mappings list

# Create mapping with template
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 1 \
  --template rgb

# Delete a mapping
govee-artnet mappings delete <mapping_id>

# View channel map
govee-artnet mappings channel-map
```

### Server Status

```bash
# Check server health
govee-artnet health

# View server status and metrics
govee-artnet status
```

## Remote Server Connection

The CLI can connect to a remote bridge server:

```bash
# Connect to remote server
govee-artnet --server-url http://192.168.1.100:8000 devices list

# Or set environment variable
export GOVEE_ARTNET_SERVER_URL=http://192.168.1.100:8000
govee-artnet devices list

# With authentication
govee-artnet --api-key your-key devices list
```

## Configuration

The bridge server can be configured via:
- Configuration file (TOML format)
- Command-line arguments
- Environment variables

Example minimal configuration:

```toml
config_version = 1

[general]
artnet_port = 6454
api_port = 8000

[rate_limiting]
rate_limit_per_second = 10.0
rate_limit_burst = 20
```

See [USAGE.md](USAGE.md) for detailed configuration options.

## Documentation

- **[INSTALL.md](INSTALL.md)** - Installation instructions, systemd setup, and deployment options
- **[USAGE.md](USAGE.md)** - Detailed usage guide, mapping strategies, and troubleshooting
- **[CLI Shell Guide](README_CLI_SHELL.md)** - Interactive shell features, log viewing, and real-time monitoring
- **[Configuration Guide](USAGE.md#configuration)** - Server configuration options
- **[Template Reference](USAGE.md#available-templates)** - Complete template documentation

## Rate Limiting

The bridge uses a token-bucket limiter to prevent overwhelming Govee devices:

- Refills at `rate_limit_per_second` tokens per second (default: 10)
- Holds up to `rate_limit_burst` tokens (default: 20)
- Each command consumes one token
- Metrics available via Prometheus endpoint

Monitor rate limiting:
```bash
# View current metrics
curl http://localhost:8000/metrics | grep govee_rate_limit
```

## Troubleshooting

**Devices not discovered?**
- Check that devices are on the same network
- Ensure multicast traffic is allowed
- Try adding devices manually with `govee-artnet devices add`

**Mappings not working?**
- Verify device capabilities: `govee-artnet devices list`
- Check mapping compatibility with device capabilities
- View active mappings: `govee-artnet mappings channel-map`

**Can't connect to server?**
- Ensure the bridge server is running
- Check firewall rules for port 8000
- Verify server URL with `govee-artnet health`

See [USAGE.md](USAGE.md#troubleshooting-mapping-errors) for detailed troubleshooting.

## License

See [LICENSE](LICENSE) file for details.
