# Govee ArtNet LAN Bridge - API Documentation

This document describes the REST API for the Govee ArtNet LAN Bridge.

## Base URL

Default: `http://127.0.0.1:8000`

## Authentication

The API supports optional authentication via API keys or bearer tokens:

```bash
# Using API key
curl -H "X-API-Key: your-api-key" http://127.0.0.1:8000/devices

# Using bearer token
curl -H "Authorization: Bearer your-token" http://127.0.0.1:8000/devices
```

## API Endpoints

### Devices

#### List All Devices
```
GET /devices
```

Returns all discovered Govee devices with their capabilities.

**Response**: `200 OK`
```json
[
  {
    "id": "AA:BB:CC:DD:EE:FF",
    "name": "Living Room Strip",
    "model": "H6160",
    "ip": "192.168.1.100",
    "capabilities": {
      "brightness": true,
      "color": true,
      "color_temperature": true,
      "color_modes": ["color", "ct"],
      "color_temp_range": [2000, 9000],
      "effects": ["sunrise", "sunset"]
    },
    "last_seen": "2025-12-26T10:30:00Z"
  }
]
```

#### Get Specific Device
```
GET /devices/{device_id}
```

**Parameters**:
- `device_id` (path): Device MAC address (e.g., "AA:BB:CC:DD:EE:FF")

**Response**: `200 OK`
```json
{
  "id": "AA:BB:CC:DD:EE:FF",
  "name": "Living Room Strip",
  "model": "H6160",
  "capabilities": {
    "brightness": true,
    "color": true
  }
}
```

**Errors**:
- `404 Not Found`: Device not found

---

### Mappings

#### List All Mappings
```
GET /mappings
```

Returns all DMX channel mappings.

**Response**: `200 OK`
```json
[
  {
    "id": 1,
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "channel": 1,
    "length": 3,
    "mapping_type": "range",
    "field": null,
    "fields": ["r", "g", "b"],
    "created_at": "2025-12-26T10:00:00Z",
    "updated_at": "2025-12-26T10:00:00Z"
  },
  {
    "id": 2,
    "device_id": "AA:BB:CC:DD:EE:01",
    "universe": 0,
    "channel": 10,
    "length": 1,
    "mapping_type": "discrete",
    "field": "dimmer",
    "fields": ["dimmer"],
    "created_at": "2025-12-26T10:05:00Z",
    "updated_at": "2025-12-26T10:05:00Z"
  }
]
```

#### Get Specific Mapping
```
GET /mappings/{mapping_id}
```

**Parameters**:
- `mapping_id` (path): Mapping ID

**Response**: `200 OK`
```json
{
  "id": 1,
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "channel": 1,
  "length": 3,
  "mapping_type": "range",
  "fields": ["r", "g", "b"]
}
```

**Errors**:
- `404 Not Found`: Mapping not found

#### Create Mapping(s)
```
POST /mappings
```

Create a single mapping or multiple mappings using a template.

**Request Body** (Individual Mapping):
```json
{
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "channel": 1,
  "length": 3,
  "mapping_type": "range",
  "allow_overlap": false
}
```

**Request Body** (Template):
```json
{
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "start_channel": 1,
  "template": "rgb"
}
```

**Request Parameters**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `device_id` | string | Yes | Device MAC address |
| `universe` | integer | Yes | ArtNet universe (≥ 0) |
| `channel` | integer | Conditional* | DMX channel (1-512) |
| `start_channel` | integer | Conditional* | Starting DMX channel for template |
| `length` | integer | No | Number of channels (default: 1) |
| `mapping_type` | string | No | "range" or "discrete" (default: "range") |
| `field` | string | Conditional** | Field name for discrete mappings |
| `template` | string | No | Template name (see below) |
| `allow_overlap` | boolean | No | Allow overlapping channels (default: false) |

\* Either `channel` or `start_channel` is required
\*\* Required when `mapping_type` is "discrete"

**Template Names** (for multi-channel mappings):
- `rgb`: 3-channel RGB
- `rgbw`: 4-channel RGBW
- `dimmer_rgb`: 4-channel dimmer + RGB
- `rgbwa`: 5-channel RGBW + dimmer
- `rgbaw`: 5-channel dimmer + RGBW
- `brgbwct`: 6-channel dimmer + RGBW + color temperature

**Single Channel Field Names**:
- `power`: Power on/off control (DMX >= 128 = on, < 128 = off) - **All devices**
- `dimmer`: Dimmer/brightness control (0-255) - **Requires `brightness` capability**
- `r` (alias: `red`): Red channel only - **Requires `color` capability**
- `g` (alias: `green`): Green channel only - **Requires `color` capability**
- `b` (alias: `blue`): Blue channel only - **Requires `color` capability**
- `w` (alias: `white`): White channel only - **Requires `white` capability**
- `ct` (alias: `color_temp`): Color temperature in Kelvin - **Requires `color_temperature` capability**

**Note**: Device capabilities are validated when creating mappings. Use `GET /devices` to check device capabilities.

**Response** (Individual Mapping): `201 Created`
```json
{
  "id": 1,
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "channel": 1,
  "length": 3,
  "mapping_type": "range",
  "fields": ["r", "g", "b"],
  "created_at": "2025-12-26T10:00:00Z",
  "updated_at": "2025-12-26T10:00:00Z"
}
```

**Response** (Template): `201 Created`
```json
[
  {
    "id": 1,
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "channel": 1,
    "length": 3,
    "mapping_type": "range",
    "fields": ["r", "g", "b"],
    "created_at": "2025-12-26T10:00:00Z",
    "updated_at": "2025-12-26T10:00:00Z"
  }
]
```

**Errors**:

`400 Bad Request` - Validation errors:
```json
{
  "detail": "Unknown template 'rgbb'. Supported templates: brgbwct, dimmer_rgb, rgb, rgbaw, rgbwa, rgbw."
}
```

```json
{
  "detail": "Template 'dimmer_rgb' is incompatible with this device (missing brightness support; supported: color)."
}
```

```json
{
  "detail": "Field(s) already mapped for device AA:BB:CC:DD:EE:FF on universe 0: r, g, b"
}
```

```json
{
  "detail": "Device does not support brightness control."
}
```

```json
{
  "detail": "Device does not support color control. Supported modes: ct"
}
```

```json
{
  "detail": "Unsupported field 'red'. Supported fields: dimmer, b, g, r, w."
}
```

```json
{
  "detail": "Mapping overlaps an existing entry"
}
```

`404 Not Found` - Device not found:
```json
{
  "detail": "Device not found"
}
```

#### Update Mapping
```
PUT /mappings/{mapping_id}
```

Update an existing mapping.

**Parameters**:
- `mapping_id` (path): Mapping ID

**Request Body**:
```json
{
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "channel": 5,
  "length": 3,
  "mapping_type": "range",
  "allow_overlap": false
}
```

**Response**: `200 OK`
```json
{
  "id": 1,
  "device_id": "AA:BB:CC:DD:EE:FF",
  "universe": 0,
  "channel": 5,
  "length": 3,
  "mapping_type": "range",
  "fields": ["r", "g", "b"],
  "updated_at": "2025-12-26T11:00:00Z"
}
```

**Errors**:
- `404 Not Found`: Mapping not found
- `400 Bad Request`: Validation error (same as create)

#### Delete Mapping
```
DELETE /mappings/{mapping_id}
```

Delete a mapping.

**Parameters**:
- `mapping_id` (path): Mapping ID

**Response**: `204 No Content`

**Errors**:
- `404 Not Found`: Mapping not found

#### Get Channel Map
```
GET /channel-map
```

Returns a map of universes to their channel mappings, useful for visualizing the DMX layout.

**Response**: `200 OK`
```json
{
  "0": [
    {
      "id": 1,
      "device_id": "AA:BB:CC:DD:EE:FF",
      "universe": 0,
      "channel": 1,
      "length": 3,
      "mapping_type": "range",
      "fields": ["r", "g", "b"]
    },
    {
      "id": 2,
      "device_id": "AA:BB:CC:DD:EE:01",
      "universe": 0,
      "channel": 10,
      "length": 1,
      "mapping_type": "discrete",
      "field": "dimmer",
      "fields": ["dimmer"]
    }
  ],
  "1": [
    {
      "id": 3,
      "device_id": "AA:BB:CC:DD:EE:02",
      "universe": 1,
      "channel": 1,
      "length": 4,
      "mapping_type": "range",
      "fields": ["r", "g", "b", "w"]
    }
  ]
}
```

---

### System

#### Reload Configuration
```
POST /reload
```

Reload configuration and restart listeners without stopping the service.

**Response**: `200 OK`
```json
{
  "status": "reloaded"
}
```

---

## Complete Examples

### Example 1: Set Up RGB Light Strip

```bash
# 1. List devices to find device ID
curl http://127.0.0.1:8000/devices

# 2. Create RGB mapping at channels 1-3
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "start_channel": 1,
    "template": "rgb"
  }'

# 3. Verify mapping
curl http://127.0.0.1:8000/mappings
```

### Example 2: Custom Dimmer + RGB Mapping

```bash
# 1. Map dimmer to channel 1
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "channel": 1,
    "length": 1,
    "mapping_type": "discrete",
    "field": "dimmer"
  }'

# 2. Map RGB to channels 3-5 (skipping channel 2)
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "channel": 3,
    "length": 3,
    "mapping_type": "range"
  }'
```

### Example 3: Multiple Devices on Same Universe

```bash
# Device 1: RGB at channels 1-3
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:01",
    "universe": 0,
    "start_channel": 1,
    "template": "rgb"
  }'

# Device 2: RGBW at channels 10-13
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:02",
    "universe": 0,
    "start_channel": 10,
    "template": "rgbw"
  }'

# Device 3: Dimmer only at channel 20 (discrete field mapping)
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:03",
    "universe": 0,
    "channel": 20,
    "field": "dimmer"
  }'

# Device 4: Power control at channel 25 (discrete field mapping)
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:04",
    "universe": 0,
    "channel": 25,
    "field": "power"
  }'

# View the complete channel map
curl http://127.0.0.1:8000/channel-map
```

### Example 4: Error Handling

```bash
# Try to create incompatible mapping
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "start_channel": 1,
    "template": "dimmer_rgb"
  }'

# Response (if device doesn't support brightness):
# {
#   "detail": "Template 'dimmer_rgb' is incompatible with this device (missing brightness support; supported: color)."
# }

# Solution: Check device capabilities first
curl http://127.0.0.1:8000/devices/AA:BB:CC:DD:EE:FF

# Then use compatible template
curl -X POST http://127.0.0.1:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "universe": 0,
    "start_channel": 1,
    "template": "rgb"
  }'
```

---

## Field Reference

### Supported Fields

| Field | Description | Usage |
|-------|-------------|-------|
| `dimmer` | Master dimmer/brightness (0-255) | Controls overall light intensity |
| `r` | Red channel (0-255) | RGB color component |
| `g` | Green channel (0-255) | RGB color component |
| `b` | Blue channel (0-255) | RGB color component |
| `w` | White channel (0-255) | Dedicated white LED |

### Mapping Types

| Type | Description | Channel Count | Field Assignment |
|------|-------------|---------------|------------------|
| `range` | Consecutive channels for color | Multiple (3 or 4) | Automatic: R, G, B [, W] |
| `discrete` | Single channel for one field | 1 | Manual via `field` parameter |

---

## Rate Limiting

The bridge implements rate limiting to prevent overwhelming Govee devices. Monitor rate limit status via metrics:

- **`govee_rate_limit_tokens`** (gauge): Current available tokens
- **`govee_rate_limit_waits_total{scope="global"}`** (counter): Number of times sends waited for tokens

Configuration:
- `rate_limit_per_second`: Token refill rate
- `rate_limit_burst`: Maximum bucket size

See the main [README.md](README.md) for more details on rate limiting.
