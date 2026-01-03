# DMX LAN Bridge

[![Latest Release](https://img.shields.io/github/v/release/mccartyp/dmx-lan-bridge)](https://github.com/mccartyp/dmx-lan-bridge/releases/latest)
[![Download DEB](https://img.shields.io/badge/download-.deb-blue)](https://github.com/mccartyp/dmx-lan-bridge/releases/latest)
[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/github/license/mccartyp/dmx-lan-bridge)](LICENSE)

A multi-protocol DMX to LAN device bridge with priority-based source merging. Control smart lights using professional lighting control protocols (ArtNet, sACN) and software like QLC+, Chamsys MagicQ, or any DMX-compatible controller.

**Supported Input Protocols:**
- ✅ **ArtNet** (fully supported with configurable priority)
- ✅ **sACN/E1.31** (fully supported with native priority control)

**Supported Device Protocols:**
- ✅ **Govee** (JSON-based LAN control)
- ✅ **LIFX** (binary LAN protocol)
- 🔜 **WiZ** (planned)
- 🔜 **TPLink/Kasa** (planned)

## Architecture

The bridge consists of two components:

1. **Bridge Server** (`dmx-lan-bridge`) - Runs as a daemon and provides:
   - **Multi-protocol DMX input** (ArtNet on port 6454, sACN/E1.31 on port 5568)
   - **Priority-based source merging** (multiple consoles, graceful failover)
   - REST API server (default port 8000)
   - Multi-protocol device support (Govee, LIFX, and more)
   - Automatic device discovery
   - Device health monitoring

2. **CLI Client** (`dmx-lan-cli`) - Command-line tool for managing the bridge:
   - List and manage devices across all supported protocols
   - Create and manage DMX channel mappings
   - Query server status

## Quick Start

### 1. Install

See [INSTALL.md](INSTALL.md) for detailed installation instructions.

```bash
# Install from source
pip install -e .

# Or install from package
pip install dmx-lan-bridge
```

### 2. Start the Bridge Server

```bash
# Start with default settings
dmx-lan-bridge

# Or with custom configuration
dmx-lan-bridge --config /path/to/config.toml
```

The server will:
- Listen for DMX input protocols (ArtNet on port 6454, sACN on port 5568 if enabled)
- Start the REST API on port 8000
- Automatically discover devices on your network (Govee, LIFX, etc.)
- Apply priority-based merging if multiple sources detected

### 3. Discover Devices

Use the CLI to list discovered devices:

```bash
dmx-lan-cli devices list
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
# Map an RGB light strip to channels 1-3 on universe 1
dmx-lan-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 1 \
  --start-channel 1 \
  --template RGB

# Map an RGB+CT light to channels 10-13
dmx-lan-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:01" \
  --universe 1 \
  --start-channel 10 \
  --template RGBc
```

**Note:** Mappings are protocol-agnostic. The same mapping works whether you send ArtNet or sACN to that universe.

**Universe Defaults:** sACN (E1.31) universes are 1–63999. Art-Net supports universe 0. Universe 0 is Art-Net-only in this application; universes 1+ are mergeable across protocols.

### 5. Send DMX Data

Point your lighting software at the bridge server's IP address and start controlling your lights!

**Priority-Based Source Merging:**
- If multiple sources send to the same universe, highest priority wins
- ArtNet: Configurable priority (default 25, well below sACN default)
- sACN: Uses native priority from packets (0-200, default 100)
- Graceful failover: If primary source stops, backup takes over automatically (2.5s timeout)

## Interactive Console

The `dmx-lan-cli` tool provides direct command-line access to the bridge API.

For an interactive shell experience with features like:
- 📊 **Real-time monitoring** - Live dashboards for devices, ArtNet, queue, and health
- 📝 **Log viewing & tailing** - View, search, and stream logs with filtering
- ⌨️  **Command history & autocomplete** - Tab completion and persistent history
- 🔖 **Bookmarks & aliases** - Save frequently used devices and commands
- 📜 **Scripting support** - Execute batch commands from files
- 🎨 **Rich formatting** - Beautiful tables and colored output

Check out the dedicated interactive console tool:

**[artnet-console](https://github.com/mccartyp/artnet-console)**

## Available Templates

| Template | Channels | Layout | Use Case |
|----------|----------|--------|----------|
| `RGB` | 3 | R, G, B | Standard RGB fixtures |
| `RGBCT` | 4 | R, G, B, CT | RGB + color temperature |
| `DimRGBCT` | 5 | Dim, R, G, B, CT | Full control with dimmer, color, and color temperature |
| `DimCT` | 2 | Dim, CT | Dimmer + color temperature (tunable white) |

### Single Channel Mappings

For individual field control, use single channel mappings instead of templates:

| Field | Aliases | Description | Capability Required |
|-------|---------|-------------|---------------------|
| `power` | - | Power on/off (DMX >= 128 = on, < 128 = off) | None (all devices) |
| `dimmer` | - | Dimmer/brightness control (0-255, 0=power off, >0=power on+brightness) | `brightness` |
| `r` | `red` | Red channel only | `color` |
| `g` | `green` | Green channel only | `color` |
| `b` | `blue` | Blue channel only | `color` |
| `ct` | `color_temp` | Color temperature in Kelvin | `color_temperature` |

**Capability Summary:**
- **`power`** - Works on all devices (plugs, lights, bulbs, switches)
- **`brightness`** - Dimmable devices only (lights, bulbs) - NOT plugs or switches
- **`color`** - Color-capable devices (RGB lights, RGB strips)
- **`color_temperature`** - Color temperature devices (tunable white lights)

**Note**: Device capabilities are validated when creating mappings. Not all devices support all features (e.g., plug-type devices only support power control). Use `dmx-lan-cli devices list` to check device capabilities.

```bash
# Create a power control mapping (works on all devices)
dmx-lan-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --field power

# Create a brightness control mapping (requires brightness capability)
dmx-lan-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 5 --field brightness

# Use field aliases for convenience (requires color capability)
dmx-lan-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 10 --field red
```

## CLI Commands

### Device Management

```bash
# List all devices
dmx-lan-cli devices list

# Add a manual device (specify protocol: govee, lifx, etc.)
dmx-lan-cli devices add --id "..." --ip "192.168.1.100" --protocol govee --model-number "H61XX" --device-type "led_strip"

# Enable/disable a device
dmx-lan-cli devices enable "AA:BB:CC:DD:EE:FF"
dmx-lan-cli devices disable "AA:BB:CC:DD:EE:FF"

# Send a quick command (on/off/brightness/color/kelvin)
dmx-lan-cli devices command "AA:BB:CC:DD:EE:FF" --on --brightness 200 --color ff8800
dmx-lan-cli devices command "AA:BB:CC:DD:EE:FF" --kelvin 64
```

- `--on`/`--off` send the native LAN protocol `turn` command instead of manipulating brightness.
- `--brightness` and `--kelvin` accept 0-255 sliders. Kelvin values are scaled to the device's supported color-temperature range when available.
- `--color` accepts RGB hex strings (e.g., `ff3366` or `#ff3366`); shorthand three-character forms expand automatically.

### Mapping Management

```bash
# List all mappings
dmx-lan-cli mappings list

# Create mapping with template
dmx-lan-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 1 \
  --start-channel 1 \
  --template RGB

# Delete a mapping
dmx-lan-cli mappings delete <mapping_id>

# View channel map
dmx-lan-cli mappings channel-map
```

### Server Status

```bash
# Check server health
dmx-lan-cli health

# View server status and metrics
dmx-lan-cli status
```

## Remote Server Connection

The CLI can connect to a remote bridge server:

```bash
# Connect to remote server
dmx-lan-cli --server-url http://192.168.1.100:8000 devices list

# Or set environment variable
export DMX_LAN_CLI_SERVER_URL=http://192.168.1.100:8000
dmx-lan-cli devices list

# With authentication
dmx-lan-cli --api-key your-key devices list
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
- **[Configuration Guide](USAGE.md#configuration)** - Server configuration options
- **[Template Reference](USAGE.md#available-templates)** - Complete template documentation

## Rate Limiting

The bridge uses a token-bucket limiter to prevent overwhelming devices:

- Refills at `rate_limit_per_second` tokens per second (default: 10)
- Holds up to `rate_limit_burst` tokens (default: 20)
- Each command consumes one token
- Metrics available via Prometheus endpoint

Monitor rate limiting:
```bash
# View current metrics
curl http://localhost:8000/metrics | grep artnet_rate_limit
```

## Troubleshooting

**Devices not discovered?**
- Check that devices are on the same network
- Ensure multicast traffic is allowed
- Try adding devices manually with `dmx-lan-cli devices add`

**Mappings not working?**
- Verify device capabilities: `dmx-lan-cli devices list`
- Check mapping compatibility with device capabilities
- View active mappings: `dmx-lan-cli mappings channel-map`

**Can't connect to server?**
- Ensure the bridge server is running
- Check firewall rules for port 8000
- Verify server URL with `dmx-lan-cli health`

See [USAGE.md](USAGE.md#troubleshooting-mapping-errors) for detailed troubleshooting.

## Credits

<p align="center">
  <img src="res/images/art-net-logo.svg" alt="Art-Net Logo" width="300">
</p>

<p align="center">
<strong>Art-Net™ Designed by and Copyright Artistic Licence Engineering Ltd</strong>
</p>

This project implements the Art-Net protocol. For more information about Art-Net, visit [https://art-net.org.uk/](https://art-net.org.uk/)

## License

See [LICENSE](LICENSE) file for details.
