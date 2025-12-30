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

The `govee-artnet` CLI communicates with the bridge server via its REST API. By default, it launches an interactive shell and connects to `http://127.0.0.1:8000`.

You can run commands directly without the shell by specifying a command:

```bash
# Launch interactive shell (default)
govee-artnet

# Or run a command directly
govee-artnet devices list
```

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

The CLI launches in interactive shell mode by default, providing an enhanced user experience with real-time monitoring and log viewing:

```bash
# Start interactive shell (default - just run govee-artnet)
govee-artnet

# Or explicitly use the shell command
govee-artnet shell
```

The shell provides:
- **Real-time monitoring** - Live dashboards for system metrics
- **Log viewing and tailing** - View and stream logs with filtering
- **Command history** - Tab completion and persistent history
- **Bookmarks and aliases** - Save frequently used devices and commands
- **Batch execution** - Run commands from scripts
- **Enhanced output** - Beautiful formatted tables

See the **[CLI Shell Guide](README_CLI_SHELL.md)** for complete shell documentation, configuration options, and examples.

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

The bridge maps ArtNet DMX channels to Govee device controls. Each device can be mapped to one or more DMX channels to control brightness, color (RGB), and color temperature.

### Using Templates (Recommended)

Templates provide pre-configured channel layouts for common lighting fixture types. They automatically create all necessary mappings in the correct order.

#### Available Templates (Multi-Channel Mappings)

| Template | Channels | Layout | Use Case |
|----------|----------|--------|----------|
| `rgb` | 3 | R, G, B | Standard RGB fixtures |
| `rgbc` | 4 | R, G, B, CT | RGB + color temperature |
| `brgbc` | 5 | Brightness, R, G, B, CT | Full control with brightness, color, and color temperature |
| `bc` | 2 | Brightness, CT | Brightness + color temperature (tunable white) |

#### Single Channel Mappings

For individual field control, use single channel mappings instead of templates:

| Field | Aliases | Description | Capability Required |
|-------|---------|-------------|---------------------|
| `power` | - | Power on/off (DMX >= 128 = on, < 128 = off) | None (all devices) |
| `brightness` | - | Brightness control (0-255, 0=power off, >0=power on+brightness) | `brightness` |
| `r` | `red` | Red channel only | `color` |
| `g` | `green` | Green channel only | `color` |
| `b` | `blue` | Blue channel only | `color` |
| `ct` | `color_temp` | Color temperature in Kelvin | `color_temperature` |

**Capability Summary:**
- **None** - Works on all Govee devices (plugs, lights, bulbs, switches)
- **`brightness`** - Dimmable devices only (lights, bulbs) - NOT plugs or switches
- **`color`** - Color-capable devices (RGB lights, RGB strips)
- **`color_temperature`** - Color temperature devices (tunable white lights)

**Important**: Device capabilities are validated when creating mappings. Not all Govee devices support all features:
- **All devices** support `power` control (on/off)
- **Brightness-capable devices** (lights, bulbs) support `brightness` field
- **Non-dimmable devices** (plugs, switches) do NOT support `brightness` field
- **Color-capable devices** support `r`, `g`, `b` fields
- **Color temperature devices** support `ct` field

Use `govee-artnet devices list` to check which capabilities your device supports.

**Example - Power and Brightness Control:**
```bash
# Power control on channel 1 (works on ALL devices, including plugs)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --channel 1 \
  --field power

# Brightness control on channel 5 (only works if device supports brightness)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --channel 5 \
  --field brightness

# Use field aliases for convenience (only works if device supports color)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --channel 10 \
  --field red
```

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

**RGB + Color Temperature Light (4-channel RGB+CT)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --start-channel 10 \
  --template rgbc
```

This creates mappings for:
- Channel 10: Red
- Channel 11: Green
- Channel 12: Blue
- Channel 13: Color Temperature

**Full Control Light (5-channel Brightness+RGB+CT)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 1 \
  --start-channel 5 \
  --template brgbc
```

This creates mappings for:
- Channel 5: Brightness (0=power off, >0=power on+brightness)
- Channel 6: Red
- Channel 7: Green
- Channel 8: Blue
- Channel 9: Color Temperature

**Simple Brightness-Only Light (single channel mapping)**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 100 \
  --field brightness
```

This creates a single channel mapping for:
- Channel 100: Brightness (0-255)

**Note**: This only works if the device has the `brightness` capability. Check with `govee-artnet devices list`.

**Tunable White (2-channel Brightness+CT)**
```bash
# Brightness and Color Temperature
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 2 \
  --start-channel 20 \
  --template bc
```

This creates mappings for:
- Channel 20: Brightness (0=power off, >0=power on+brightness)
- Channel 21: Color Temperature (kelvin)

### Individual Channel Mapping

For fine-grained control or non-standard fixture layouts, you can create individual channel mappings.

#### Mapping Types

- **`range`**: Maps consecutive DMX channels to color fields (R, G, B)
- **`discrete`**: Maps a single DMX channel to one device field (brightness, r, g, b, or ct)

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

**Step 4: Map a color temperature channel**
```bash
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 13 \
  --length 1 \
  --type discrete \
  --field ct
```

#### Important Notes

- **No duplicate fields**: Each field (brightness, r, g, b, ct) can only be mapped once per device per universe
- **Channel overlap**: By default, overlapping channel ranges are prevented. Use `--allow-overlap` to override
- **Range mapping**: For `type=range`, the bridge automatically assigns fields based on length:
  - Length 3: R, G, B
- **Discrete mapping**: For `type=discrete`, you must specify the `--field` parameter

### Device Capability Requirements

Not all templates work with all devices. The bridge validates device capabilities before creating mappings.

#### Device Capabilities

Govee devices report their capabilities:
- **`brightness`**: Device has adjustable brightness/dimming
- **`color`**: Device supports RGB color control
- **`color_temperature`**: Device supports color temperature (warm/cool white)

#### Template Compatibility Matrix

| Template | Requires Brightness | Requires Color | Requires Color Temp | Compatible Devices |
|----------|---------------------|----------------|---------------------|-------------------|
| `rgb` | No | Yes | No | Any RGB-capable device |
| `rgbc` | No | Yes | Yes | Devices with color AND color temperature |
| `brgbc` | Yes | Yes | Yes | Devices with brightness, color, and color temperature |
| `bc` | Yes | No | Yes | Devices with brightness and color temperature |

**Note**: For individual field control (brightness only, power, color channels, etc.), use single channel mappings instead of templates. All Govee devices support `power` mappings, but `brightness`, color, and color temperature mappings require the corresponding device capabilities.

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

#### Common Device Types and Their Capabilities

| Device Type | Power | Brightness | Color | Color Temp | Examples |
|-------------|-------|------------|-------|------------|----------|
| **Smart Plugs** | ✓ | ✗ | ✗ | ✗ | H5080, H5081 |
| **Dimmable Bulbs** | ✓ | ✓ | ✗ | ✗ | Simple dimmable bulbs |
| **White Bulbs** | ✓ | ✓ | ✗ | ✓ | Tunable white bulbs |
| **RGB Lights** | ✓ | ✓ | ✓ | ✗ | RGB strips, bulbs |
| **RGBIC Lights** | ✓ | ✓ | ✓ | ✗ | Multi-segment RGB strips |
| **RGB+CT Lights** | ✓ | ✓ | ✓ | ✓ | Full-featured lights |

**Important**: Always check your specific device's capabilities with `govee-artnet devices list` before creating mappings.

Discovery responses that include a `model_number` are matched against the bundled capability catalog. When a device does not report full capabilities, the bridge fills in `device_type`, `length_meters`, and segment metadata from the catalog so channel templates can be validated without manual editing. Manual devices can provide the same fields to override or augment catalog values.

### Troubleshooting Mapping Errors

#### "Unknown template"
```
Unknown template 'rgbb'. Supported templates: rgb, rgbc, brgbc, bc.
```

**Solution**: Check your template name for typos. Use one of the supported templates listed above.

---

#### "Template is incompatible with this device"
```
Template 'brgbc' is incompatible with this device (missing brightness support; supported: color, color temperature).
```

**Cause**: The device doesn't support all features required by the template.

**Solution**:
1. Check device capabilities: `govee-artnet devices list`
2. Choose a compatible template based on the capability matrix above
3. For this example, use `rgbc` template instead (doesn't require brightness)

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

**Solution**: Use a template or mapping that doesn't include brightness (e.g., `rgb` or `rgbc`).

---

#### "Device does not support color control"
```
Device does not support color control. Supported modes: ct
```

**Cause**: You're trying to map color channels (R, G, B), but the device only supports color temperature (warm/cool white).

**Solution**: Use single channel mapping with `--field brightness` for brightness control only, or check if you selected the correct device.

---

#### "Device does not support brightness control"
```
Device does not support brightness control.
```

**Cause**: You're trying to create a brightness mapping on a device that doesn't have the `brightness` capability (e.g., a smart plug).

**Solution**:
1. Check device capabilities: `govee-artnet devices list`
2. For non-dimmable devices (like plugs), use `--field power` instead for on/off control
3. Verify you selected the correct device ID

**Example - Controlling a smart plug:**
```bash
# This will FAIL on a plug (no brightness capability)
govee-artnet mappings create --device-id H5080_PLUG --channel 1 --field brightness

# This will WORK on a plug (power is supported by all devices)
govee-artnet mappings create --device-id H5080_PLUG --channel 1 --field power
```

---

#### "Unsupported field"
```
Unsupported field 'white'. Supported fields: brightness, b, ct, g, r, power.
```

**Cause**: Field name typo or using unsupported field name.

**Solution**: Use one of the supported field names:
- `brightness`: Master brightness/dimmer (0=power off, >0=power on+brightness)
- `r`: Red channel
- `g`: Green channel
- `b`: Blue channel
- `ct`: Color temperature (kelvin)
- `power`: Power on/off control

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
- `--kelvin <0-255>` or `--ct <0-255>`: 0-255 slider scaled to the device's supported color-temperature range (defaults to 2000-9000K when the range is unknown)

#### Shell Mode Device Commands

The same `devices command` functionality is available in the interactive shell:

```bash
# In shell mode
govee> devices command AA:BB:CC:DD:EE:FF --on --brightness 200 --color #FF00FF
govee> devices command AA:BB:CC:DD:EE:FF --off
govee> devices command AA:BB:CC:DD:EE:FF --color ff8800 --brightness 128
govee> devices command AA:BB:CC:DD:EE:FF --ct 128

# Use bookmarks for convenience
govee> bookmark add kitchen "AA:BB:CC:DD:EE:FF"
govee> devices command @kitchen --on --color #00FF00
```

See the **[CLI Shell Guide](README_CLI_SHELL.md)** for complete documentation on shell features, bookmarks, and autocomplete.

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
# Living room: RGB+CT strip with master brightness (5 channels)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:10" \
  --universe 1 \
  --start-channel 1 \
  --template brgbc

# Bedroom: Tunable white bulb (2 channels - brightness + color temp)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:20" \
  --universe 1 \
  --start-channel 10 \
  --template bc

# Kitchen: RGB+CT strip (4 channels)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:30" \
  --universe 1 \
  --start-channel 20 \
  --template rgbc
```

**DMX Channel Layout:**
```
Universe 1:
Ch  1-5:   Living Room (Brightness, R, G, B, CT)
Ch  6-9:   Unused
Ch 10-11:  Bedroom (Brightness, CT)
Ch 12-19:  Unused
Ch 20-23:  Kitchen (R, G, B, CT)
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

# Color temperature on channel 10 (non-consecutive)
govee-artnet mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 10 \
  --type discrete \
  --field ct
```
