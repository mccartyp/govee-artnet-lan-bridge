# Govee ArtNet LAN Bridge

This project bridges ArtNet DMX input to Govee LAN devices, allowing you to control Govee smart lights using standard lighting control software like QLC+, Chamsys MagicQ, or any other ArtNet-compatible controller.

## Architecture

The bridge consists of two components:

1. **Bridge Server** (`govee-artnet-bridge`) - Runs as a daemon and provides:
   - ArtNet listener (default port 6454)
   - REST API server (default port 8000)
   - Automatic device discovery
   - Device health monitoring

2. **CLI Client** (`govee-artnet-cli`) - Command-line tool for managing the bridge:
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

Use the CLI to list discovered devices:

```bash
govee-artnet-cli devices list
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
govee-artnet-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 1 \
  --template RGB

# Map an RGB+CT light to channels 10-13
govee-artnet-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:01" \
  --universe 0 \
  --start-channel 10 \
  --template RGBc
```

### 5. Send ArtNet

Point your lighting software at the bridge server's IP address and start controlling your lights!

## Interactive Console

The `govee-artnet-cli` tool provides direct command-line access to the bridge API.

For an interactive shell experience with features like:
- 📊 **Real-time monitoring** - Live dashboards for devices, ArtNet, queue, and health
- 📝 **Log viewing & tailing** - View, search, and stream logs with filtering
- ⌨️  **Command history & autocomplete** - Tab completion and persistent history
- 🔖 **Bookmarks & aliases** - Save frequently used devices and commands
- 📜 **Scripting support** - Execute batch commands from files
- 🎨 **Rich formatting** - Beautiful tables and colored output

Check out the dedicated interactive console tool:

**[govee-artnet-console](https://github.com/mccartyp/govee-artnet-console)**

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
- **None** - Works on all Govee devices (plugs, lights, bulbs, switches)
- **`brightness`** - Dimmable devices only (lights, bulbs) - NOT plugs or switches
- **`color`** - Color-capable devices (RGB lights, RGB strips)
- **`color_temperature`** - Color temperature devices (tunable white lights)

**Note**: Device capabilities are validated when creating mappings. Not all Govee devices support all features (e.g., plug-type devices only support power control). Use `govee-artnet-cli devices list` to check device capabilities.

```bash
# Create a power control mapping (works on all devices)
govee-artnet-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --field power

# Create a brightness control mapping (requires brightness capability)
govee-artnet-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 5 --field brightness

# Use field aliases for convenience (requires color capability)
govee-artnet-cli mappings create --device-id AA:BB:CC:DD:EE:FF --channel 10 --field red
```

## CLI Commands

### Device Management

```bash
# List all devices
govee-artnet-cli devices list

# Add a manual device
govee-artnet-cli devices add --id "..." --ip "192.168.1.100" --model-number "H61XX" --device-type "led_strip"

# Enable/disable a device
govee-artnet-cli devices enable "AA:BB:CC:DD:EE:FF"
govee-artnet-cli devices disable "AA:BB:CC:DD:EE:FF"

# Send a quick command (on/off/brightness/color/kelvin)
govee-artnet-cli devices command "AA:BB:CC:DD:EE:FF" --on --brightness 200 --color ff8800
govee-artnet-cli devices command "AA:BB:CC:DD:EE:FF" --kelvin 64
```

- `--on`/`--off` send the native Govee LAN `turn` command instead of manipulating brightness.
- `--brightness` and `--kelvin` accept 0-255 sliders. Kelvin values are scaled to the device's supported color-temperature range when available.
- `--color` accepts RGB hex strings (e.g., `ff3366` or `#ff3366`); shorthand three-character forms expand automatically.

### Mapping Management

```bash
# List all mappings
govee-artnet-cli mappings list

# Create mapping with template
govee-artnet-cli mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 1 \
  --template RGB

# Delete a mapping
govee-artnet-cli mappings delete <mapping_id>

# View channel map
govee-artnet-cli mappings channel-map
```

### Server Status

```bash
# Check server health
govee-artnet-cli health

# View server status and metrics
govee-artnet-cli status
```

## Remote Server Connection

The CLI can connect to a remote bridge server:

```bash
# Connect to remote server
govee-artnet-cli --server-url http://192.168.1.100:8000 devices list

# Or set environment variable
export GOVEE_ARTNET_CLI_SERVER_URL=http://192.168.1.100:8000
govee-artnet-cli devices list

# With authentication
govee-artnet-cli --api-key your-key devices list
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
- Try adding devices manually with `govee-artnet-cli devices add`

**Mappings not working?**
- Verify device capabilities: `govee-artnet-cli devices list`
- Check mapping compatibility with device capabilities
- View active mappings: `govee-artnet-cli mappings channel-map`

**Can't connect to server?**
- Ensure the bridge server is running
- Check firewall rules for port 8000
- Verify server URL with `govee-artnet-cli health`

See [USAGE.md](USAGE.md#troubleshooting-mapping-errors) for detailed troubleshooting.

## License

See [LICENSE](LICENSE) file for details.
