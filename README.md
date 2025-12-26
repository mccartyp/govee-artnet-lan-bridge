# Govee ArtNet LAN Bridge

This project bridges ArtNet DMX input to Govee LAN devices, allowing you to control Govee smart lights using standard lighting control software like QLC+, Chamsys MagicQ, or any other ArtNet-compatible controller.

## Quick Start

1. **Install**: See [INSTALL.md](INSTALL.md) for installation instructions
2. **Discover devices**: The bridge automatically discovers Govee devices on your LAN
3. **Create mappings**: Map DMX channels to device controls using templates or individual channel mappings
4. **Send ArtNet**: Point your lighting software at the bridge and start controlling your lights

## Contents

- [DMX Channel Mapping](#dmx-channel-mapping)
  - [Using Templates (Recommended)](#using-templates-recommended)
  - [Individual Channel Mapping](#individual-channel-mapping)
  - [Device Capability Requirements](#device-capability-requirements)
  - [Troubleshooting Mapping Errors](#troubleshooting-mapping-errors)
- [CLI Reference](#cli-reference)
- [Sample Configurations](#sample-configurations)
- [Rate Limiting](#rate-limiting)

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

#### CLI Examples for Common Fixtures

**RGB Light Strip (3-channel RGB)**
```bash
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 2 \
  --start-channel 20 \
  --template rgbaw

# Option 2: RGBW first (RGBWA pattern)
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 1 \
  --length 1 \
  --type discrete \
  --field brightness
```

**Step 2: Map RGB as a range (3 consecutive channels)**
```bash
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 10 \
  --length 1 \
  --type discrete \
  --field r

# Map green to channel 11
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 11 \
  --length 1 \
  --type discrete \
  --field g

# Map blue to channel 12
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 12 \
  --length 1 \
  --type discrete \
  --field b
```

**Step 4: Map a white channel**
```bash
govee-artnet-bridge mappings create \
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
- **`supports_brightness`**: Device has adjustable brightness/dimming
- **`supports_color`**: Device supports RGB color control
- **`supports_color_temperature`**: Device supports color temperature (warm/cool white)

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
govee-artnet-bridge devices list
```

Example output:
```json
{
  "id": "AA:BB:CC:DD:EE:FF",
  "name": "Living Room Strip",
  "model": "H6160",
  "capabilities": {
    "supports_brightness": true,
    "supports_color": true,
    "color_modes": ["color", "ct"]
  }
}
```

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
1. Check device capabilities: `govee-artnet-bridge devices list`
2. Choose a compatible template based on the capability matrix above
3. For this example, use `rgb` template instead (doesn't require brightness)

---

#### "Field(s) already mapped for device"
```
Field(s) already mapped for device AA:BB:CC:DD:EE:FF on universe 0: r, g, b
```

**Cause**: You're trying to map a field (like 'r' for red) that's already mapped for this device on this universe.

**Solution**:
1. List existing mappings: `govee-artnet-bridge mappings list`
2. Delete the conflicting mapping: `govee-artnet-bridge mappings delete <mapping_id>`
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
1. Check existing mappings: `govee-artnet-bridge mappings list`
2. Use a different channel range, or
3. Delete the conflicting mapping, or
4. Use `--allow-overlap` flag if intentional

## CLI Reference

### List Devices
```bash
# List all discovered devices
govee-artnet-bridge devices list

# Get specific device
govee-artnet-bridge devices get <device_id>
```

### Manage Mappings
```bash
# List all mappings
govee-artnet-bridge mappings list

# Get specific mapping
govee-artnet-bridge mappings get <mapping_id>

# Create mapping with template
govee-artnet-bridge mappings create \
  --device-id <device_id> \
  --universe <universe_number> \
  --start-channel <channel_number> \
  --template <template_name>

# Create individual mapping
govee-artnet-bridge mappings create \
  --device-id <device_id> \
  --universe <universe_number> \
  --channel <channel_number> \
  --length <channel_count> \
  --type {range|discrete} \
  --field <field_name>  # required for discrete type

# Update mapping
govee-artnet-bridge mappings update <mapping_id> \
  --channel <new_channel> \
  --universe <new_universe>

# Delete mapping
govee-artnet-bridge mappings delete <mapping_id>

# View channel map (universe -> mappings)
govee-artnet-bridge mappings channel-map
```

### Output Formats
```bash
# JSON output (default)
govee-artnet-bridge devices list

# YAML output
govee-artnet-bridge devices list --output yaml
```

### Server Configuration
```bash
# Connect to custom server
govee-artnet-bridge --server-url http://192.168.1.100:8000 devices list

# Use API authentication
govee-artnet-bridge --api-key your-api-key devices list
govee-artnet-bridge --api-bearer-token your-token devices list
```

## Sample Configurations

### Example 1: Three RGB Light Strips on Universe 0

```bash
# Strip 1: Channels 1-3
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:01" \
  --universe 0 \
  --start-channel 1 \
  --template rgb

# Strip 2: Channels 4-6
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:02" \
  --universe 0 \
  --start-channel 4 \
  --template rgb

# Strip 3: Channels 7-9
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:10" \
  --universe 1 \
  --start-channel 1 \
  --template rgbaw

# Bedroom: Simple brightness-only bulb (1 channel)
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:20" \
  --universe 1 \
  --start-channel 10 \
  --template master_only

# Kitchen: RGBW strip (4 channels)
govee-artnet-bridge mappings create \
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
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 1 \
  --type discrete \
  --field brightness

# Skip channel 2 (for future use)

# RGB on channels 3-5
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 3 \
  --length 3 \
  --type range

# White on channel 10 (non-consecutive)
govee-artnet-bridge mappings create \
  --device-id "AA:BB:CC:DD:EE:FF" \
  --universe 0 \
  --channel 10 \
  --type discrete \
  --field w
```

## Rate Limiting

The bridge uses a global token-bucket limiter to smooth outgoing traffic to Govee devices and prevent overwhelming them.

* The limiter refills at `rate_limit_per_second` tokens per second and holds up to `rate_limit_burst` tokens.
* Each payload send consumes one token. If no tokens are available, sends wait until enough tokens accumulate before proceeding.
* Burst capacity allows short spikes up to the configured bucket size before throttling engages.

### Visibility

* Gauge `govee_rate_limit_tokens` reports current available tokens.
* Counter `govee_rate_limit_waits_total{scope="global"}` increments whenever a send waits for the limiter.
* The sender logs when throttling delays a payload, including the estimated wait duration and remaining tokens.
