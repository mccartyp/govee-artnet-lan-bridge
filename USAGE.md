# Usage Guide

This guide provides detailed information on using the Govee ArtNet LAN Bridge CLI to manage devices and configure DMX channel mappings.

## Table of Contents

- [Starting the Bridge Server](#starting-the-bridge-server)
- [CLI Overview](#cli-overview)
- [DMX Channel Mapping](#dmx-channel-mapping)
  - [Using Templates (Recommended)](#using-templates-recommended)
  - [Individual Channel Mapping](#individual-channel-mapping)
  - [Device Capability Requirements](#device-capability-requirements)
  - [Troubleshooting Mapping Errors](#troubleshooting-mapping-errors)
- [CLI Command Reference](#cli-command-reference)
- [Sample Configurations](#sample-configurations)

## Starting the Bridge Server

The bridge server runs as a daemon and provides:
- ArtNet listener on port 6454 (configurable)
- REST API on port 8000 (configurable)
- Automatic device discovery
- Device health monitoring

Start the server:
```bash
# Start with default settings
govee-artnet-bridge

# Start with custom configuration
govee-artnet-bridge --config /path/to/config.toml

# Start with custom API port
govee-artnet-bridge --api-port 9000
```

See [INSTALL.md](INSTALL.md) for systemd service setup and other installation options.

## CLI Overview

The `govee-artnet` CLI communicates with the bridge server via its REST API. By default, it connects to `http://127.0.0.1:8000`.

### Connecting to a Remote Server

```bash
# Connect to a remote bridge server
govee-artnet --server-url http://192.168.1.100:8000 devices list

# Or set the environment variable
export GOVEE_ARTNET_SERVER_URL=http://192.168.1.100:8000
govee-artnet devices list
```

### Authentication

If the bridge server has API authentication enabled:

```bash
# Using API key
govee-artnet --api-key your-api-key devices list

# Using bearer token
govee-artnet --api-bearer-token your-token devices list

# Or use environment variables
export GOVEE_ARTNET_API_KEY=your-api-key
govee-artnet devices list
```

### Output Formats

```bash
# JSON output (default)
govee-artnet devices list

# YAML output
govee-artnet devices list --output yaml
```

### Interactive Shell Mode

For an enhanced user experience with real-time monitoring and log viewing, use the interactive shell:

```bash
# Start interactive shell
govee-artnet shell
```

The shell provides:
- **Real-time monitoring** - Live dashboards for system metrics
- **Log viewing and tailing** - View and stream logs with filtering
- **Command history** - Tab completion and persistent history
- **Bookmarks and aliases** - Save frequently used devices and commands
- **Batch execution** - Run commands from scripts
- **Enhanced output** - Beautiful formatted tables

See the **[CLI Shell Guide](CLI_SHELL_README.md)** for complete shell documentation, configuration options, and examples.

#### Shell Configuration

Create a configuration file at `~/.govee_artnet/shell_config.toml`:

```toml
[shell]
default_output = "table"    # Default output format
history_size = 1000         # Command history size

[connection]
server_url = "http://127.0.0.1:8000"  # Default server URL
timeout = 10.0                        # Request timeout

[monitoring]
watch_interval = 2.0        # Default watch interval (seconds)
log_lines = 50              # Default log lines to show
```

## DMX Channel Mapping

The bridge maps ArtNet DMX channels to Govee device controls. Each device can be mapped to one or more DMX channels to control brightness, color (RGB), and white channels.

### Using Templates (Recommended)

Templates provide pre-configured channel layouts for common lighting fixture types. They automatically create all necessary mappings in the correct order.

#### Available Templates

| Template | Channels | Layout | Use Case |
|----------|----------|--------|----------|
| `rgb` | 3 | R, G, B | Standard RGB fixtures |
| `rgbw` | 4 | R, G, B, W | RGB + dedicated white channel |
| `brightness_rgb` | 4 | Brightness, R, G, B | Master dimmer + RGB color |
| `master_only` | 1 | Brightness | Simple dimmer/brightness control |
| `rgbwa` | 5 | R, G, B, W, Brightness | RGBW color + master dimmer |
| `rgbaw` | 5 | Brightness, R, G, B, W | Master dimmer + RGBW color |

#### Examples for Common Fixtures

**RGB Light Strip (3-channel RGB)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 1 \
  --template rgb
```

This creates mappings for:
- Channel 1: Red
- Channel 2: Green
- Channel 3: Blue

**RGBW Light (4-channel RGBW)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 10 \
  --template rgbw
```

This creates mappings for:
- Channel 10: Red
- Channel 11: Green
- Channel 12: Blue
- Channel 13: White

**Brightness + RGB Light (4-channel master dimmer + color)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 1 \
  --start-channel 5 \
  --template brightness_rgb
```

This creates mappings for:
- Channel 5: Master brightness/dimmer
- Channel 6: Red
- Channel 7: Green
- Channel 8: Blue

**Simple Brightness-Only Light (1-channel dimmer)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 100 \
  --template master_only
```

This creates a mapping for:
- Channel 100: Brightness

**Advanced RGBW + Master Brightness (5-channel)**
```bash
# Option 1: Brightness first (RGBAW pattern)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 2 \
  --start-channel 20 \
  --template rgbaw

# Option 2: RGBW first (RGBWA pattern)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 2 \
  --start-channel 30 \
  --template rgbwa
```

### Individual Channel Mapping

For fine-grained control or non-standard fixture layouts, you can create individual channel mappings.

#### Mapping Types

- **`range`**: Maps consecutive DMX channels to color fields (R, G, B, W)
- **`discrete`**: Maps a single DMX channel to one device field (brightness, r, g, b, or w)

#### Quick Guide: How to Map Individual Channels

**Step 1: Map a single brightness channel**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 1 \
  --length 1 \
  --type discrete \
  --field brightness
```

**Step 2: Map RGB as a range (3 consecutive channels)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 2 \
  --length 3 \
  --type range
```
This automatically maps channels 2, 3, 4 to R, G, B respectively.

**Step 3: Map individual color channels**
```bash
# Map red to channel 10
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 10 \
  --length 1 \
  --type discrete \
  --field r

# Map green to channel 11
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 11 \
  --length 1 \
  --type discrete \
  --field g

# Map blue to channel 12
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 12 \
  --length 1 \
  --type discrete \
  --field b
```

**Step 4: Map a white channel**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 13 \
  --length 1 \
  --type discrete \
  --field w
```

#### Important Notes

- **No duplicate fields**: Each field (brightness, r, g, b, w) can only be mapped once per device per universe
- **Channel overlap**: By default, overlapping channel ranges are prevented. Use `--allow-overlap` to override
- **Range mapping**: For `type=range`, the bridge automatically assigns fields based on length:
  - Length 3: R, G, B
  - Length 4: R, G, B, W
- **Discrete mapping**: For `type=discrete`, you must specify the `--field` parameter

### Device Capability Requirements

Not all templates work with all devices. The bridge validates device capabilities before creating mappings.

#### Device Capabilities

Govee devices report their capabilities:
- **`brightness`**: Device has adjustable brightness/dimming
- **`color`**: Device supports RGB color control
- **`color_temperature`**: Device supports color temperature (warm/cool white)

#### Template Compatibility Matrix

| Template | Requires Brightness | Requires Color | Compatible Devices |
|----------|---------------------|----------------|-------------------|
| `rgb` | No | Yes | Any RGB-capable device |
| `rgbw` | No | Yes | Any RGB-capable device |
| `brightness_rgb` | Yes | Yes | Devices with both brightness AND color |
| `master_only` | Yes | No | Any device with brightness control |
| `rgbwa` | Yes | Yes | Devices with both brightness AND color |
| `rgbaw` | Yes | Yes | Devices with both brightness AND color |

#### Checking Device Capabilities

List all devices with their capabilities:
```bash
govee-artnet devices list
```

Example output:
```json
{
  "id": "AA:BB:CC:DD:EE:FF",
  "name": "Living Room Strip",
  "model_number": "H6160",
  "device_type": "led_strip",
  "length_meters": 5.0,
  "has_segments": false,
  "capabilities": {
    "model_number": "H6160",
    "device_type": "led_strip",
    "brightness": true,
    "color_temperature": true,
    "color": true,
    "color_modes": ["color", "ct"],
    "color_temp_range": [2000, 9000]
  }
}
```

Discovery responses that include a `model_number` are matched against the bundled capability catalog. When a device does not report full capabilities, the bridge fills in `device_type`, `length_meters`, and segment metadata from the catalog so channel templates can be validated without manual editing. Manual devices can provide the same fields to override or augment catalog values.

### Troubleshooting Mapping Errors

#### "Unknown template"
```
Unknown template 'rgbb'. Supported templates: brightness_rgb, master_only, rgb, rgbaw, rgbwa, rgbw.
```

**Solution**: Check your template name for typos. Use one of the six supported templates listed above.

---

#### "Template is incompatible with this device"
```
Template 'brightness_rgb' is incompatible with this device (missing brightness support; supported: color).
```

**Cause**: The device doesn't support all features required by the template.

**Solution**:
1. Check device capabilities: `govee-artnet devices list`
2. Choose a compatible template based on the capability matrix above
3. For this example, use `rgb` template instead (doesn't require brightness)

---

#### "Field(s) already mapped for device"
```
Field(s) already mapped for device AA:BB:CC:DD:EE:FF on universe 0: r, g, b
```

**Cause**: You're trying to map a field (like 'r' for red) that's already mapped for this device on this universe.

**Solution**:
1. List existing mappings: `govee-artnet mappings list`
2. Delete the conflicting mapping: `govee-artnet mappings delete <mapping_id>`
3. Or use a different universe for the new mapping

---

#### "Device does not support brightness control"
```
Device does not support brightness control.
```

**Cause**: You're trying to map a brightness channel, but the device doesn't support brightness adjustment.

**Solution**: Use a template or mapping that doesn't include brightness (e.g., `rgb` or `rgbw`).

---

#### "Device does not support color control"
```
Device does not support color control. Supported modes: ct
```

**Cause**: You're trying to map color channels (R, G, B), but the device only supports color temperature (warm/cool white).

**Solution**: Use `master_only` template for brightness control only, or check if you selected the correct device.

---

#### "Unsupported field"
```
Unsupported field 'red'. Supported fields: brightness, b, g, r, w.
```

**Cause**: Field name typo or using unsupported field name.

**Solution**: Use one of the five supported field names:
- `brightness`: Master brightness/dimmer
- `r`: Red channel
- `g`: Green channel
- `b`: Blue channel
- `w`: White channel

---

#### "Mapping overlaps an existing entry"
```
Mapping overlaps an existing entry
```

**Cause**: The DMX channel range you're trying to map overlaps with an existing mapping.

**Solution**:
1. Check existing mappings: `govee-artnet mappings list`
2. Use a different channel range, or
3. Delete the conflicting mapping, or
4. Use `--allow-overlap` flag if intentional

## CLI Command Reference

### Server Health and Status

```bash
# Check API health
govee-artnet health

# Show server status and metrics
govee-artnet status
```

### Device Management

```bash
# List all discovered devices
govee-artnet devices list

# Add a manual device
govee-artnet devices add \
  --id "AA:BB:CC:DD:EE:FF" \
  --ip "192.168.1.100" \
  --model-number "H6160" \
  --device-type "led_strip" \
  --length-meters 5 \
  --description "Living Room Strip"

# Update a device
govee-artnet devices update "AA:BB:CC:DD:EE:FF" \
  --ip "192.168.1.101" \
  --description "Updated description"

# Enable/disable a device
govee-artnet devices enable "AA:BB:CC:DD:EE:FF"
govee-artnet devices disable "AA:BB:CC:DD:EE:FF"

# Send a test payload to a device
govee-artnet devices test "AA:BB:CC:DD:EE:FF" \
  --payload '{"cmd":"turn","turn":"on"}'

# Send a quick command (on/off/brightness/color/kelvin)
govee-artnet devices command "AA:BB:CC:DD:EE:FF" --on --brightness 200 --color ff8800
govee-artnet devices command "AA:BB:CC:DD:EE:FF" --kelvin 32
```

The `devices command` helper accepts the following actions:
- `--on` / `--off`: convenience toggles (sends the Govee LAN `turn` command without forcing brightness)
- `--brightness <0-255>`: raw brightness level
- `--color <hex>`: RGB hex string (`ff3366`, `#00ccff`, or three-digit shorthand like `0cf`)
- `--kelvin <0-255>`: 0-255 slider scaled to the device's supported color-temperature range (defaults to 2000-9000K when the range is unknown)

Manual configuration files can also define the same metadata used by discovery/catalog lookups. Example `config.toml` snippet:

```toml
manual_devices = [
  { id = "AA:BB:CC:DD:EE:FF", ip = "192.168.1.100", model_number = "H6160", device_type = "led_strip", length_meters = 5.0, has_segments = false }
]
```

### Mapping Management

```bash
# List all mappings
govee-artnet mappings list

# Get specific mapping
govee-artnet mappings get <mapping_id>

# Create mapping with template
govee-artnet mappings create \
  --device-id <device_id> \
  --universe <universe_number> \
  --start-channel <channel_number> \
  --template <template_name>

# Create individual mapping
govee-artnet mappings create \
  --device-id <device_id> \
  --universe <universe_number> \
  --channel <channel_number> \
  --length <channel_count> \
  --type {range|discrete} \
  --field <field_name>  # required for discrete type

# Update mapping
govee-artnet mappings update <mapping_id> \
  --channel <new_channel> \
  --universe <new_universe>

# Delete mapping
govee-artnet mappings delete <mapping_id>

# View channel map (universe -> mappings)
govee-artnet mappings channel-map
```

## Sample Configurations

### Example 1: Three RGB Light Strips on Universe 0

```bash
# Strip 1: Channels 1-3
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:01" \
  --universe 0 \
  --start-channel 1 \
  --template rgb

# Strip 2: Channels 4-6
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:02" \
  --universe 0 \
  --start-channel 4 \
  --template rgb

# Strip 3: Channels 7-9
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:03" \
  --universe 0 \
  --start-channel 7 \
  --template rgb
```

**DMX Channel Layout:**
```
Ch  1-3:   Device 01 (RGB)
Ch  4-6:   Device 02 (RGB)
Ch  7-9:   Device 03 (RGB)
Ch 10-512: Unused
```

### Example 2: Mixed Fixture Types

```bash
# Living room: RGBW strip with master brightness (5 channels)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:10" \
  --universe 1 \
  --start-channel 1 \
  --template rgbaw

# Bedroom: Simple brightness-only bulb (1 channel)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:20" \
  --universe 1 \
  --start-channel 10 \
  --template master_only

# Kitchen: RGBW strip (4 channels)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:30" \
  --universe 1 \
  --start-channel 20 \
  --template rgbw
```

**DMX Channel Layout:**
```
Universe 1:
Ch  1-5:   Living Room (Brightness, R, G, B, W)
Ch  6-9:   Unused
Ch 10:     Bedroom (Brightness only)
Ch 11-19:  Unused
Ch 20-23:  Kitchen (R, G, B, W)
```

### Example 3: Advanced Custom Mapping

If you need a non-standard layout, use individual mappings:

```bash
# Brightness on channel 1
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 1 \
  --type discrete \
  --field brightness

# Skip channel 2 (for future use)

# RGB on channels 3-5
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 3 \
  --length 3 \
  --type range

# White on channel 10 (non-consecutive)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 10 \
  --type discrete \
  --field w
```
