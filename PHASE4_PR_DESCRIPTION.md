# Phase 4: Security and Robustness Improvements

This PR implements Phase 4 of the CLI Framework Improvement Plan, focusing on security, validation, and robustness enhancements for better error handling and reliability.

## Changes Implemented

### 1. JSON Schema Validation for User Data

**Added comprehensive validation functions:**

- **`_validate_capabilities()`** - Validates device capabilities JSON structure
  - Ensures capabilities is a dictionary
  - Validates keys: `color`, `brightness`, `temperature`
  - Ensures all values are boolean
  - Clear error messages with valid key suggestions

- **`_validate_device_payload()`** - Validates device payloads
  - Required fields for create: `id`, `ip`
  - IPv4 address format validation
  - Numeric field validation (all must be > 0)
  - Supports both "create" and "update" operations

- **`_validate_mapping_payload()`** - Validates mapping payloads
  - Required fields for create: `device_id`, `universe`
  - Universe validation (0-32767 range)
  - Channel validation (1-512 range)
  - Length validation (>= 1)
  - Template validation: `rgb`, `rgbw`, `brightness`, `temperature`

**Enhanced JSON parsing error messages:**
```python
# Before
raise CliError("Failed to parse JSON argument")

# After
raise CliError(f"Failed to parse JSON argument: {exc.msg} at position {exc.pos}")
```

**Example validation errors:**
```bash
$ govee-artnet devices add --capabilities '{"invalid_key": true}'
Error: Invalid capability key 'invalid_key'. Valid keys: brightness, color, temperature

$ govee-artnet devices add --id test --ip 999.999.999.999
Error: Invalid IP address: 999.999.999.999 (octet out of range)

$ govee-artnet mappings create --universe 99999
Error: Universe must be between 0 and 32767, got: 99999
```

---

### 2. Connection Pooling and Retry Logic

**Enhanced HTTP client with connection pooling:**

```python
limits = httpx.Limits(
    max_connections=10,              # Maximum total connections
    max_keepalive_connections=5,     # Maximum idle connections
    keepalive_expiry=30.0,           # Keepalive timeout
)

transport = httpx.HTTPTransport(
    retries=3,    # Retry failed connections up to 3 times
    limits=limits,
)
```

**Benefits:**
- ✅ Connection reuse for better performance
- ✅ Automatic retry of failed connections (up to 3 attempts)
- ✅ Proper resource limits prevent exhaustion
- ✅ Keepalive connections reduce latency
- ✅ Automatic redirect following

---

### 3. Graceful API Endpoint Detection

**Added API availability checking:**

```python
def _check_api_available(client):
    """Check if API is responding."""
    try:
        response = client.get("/health", timeout=5.0)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return False

def _ensure_api_available(client, config):
    """Ensure API is available, raise friendly error if not."""
    if not _check_api_available(client):
        raise CliError(
            f"Unable to connect to the bridge API at {config.server_url}. "
            "Please check that the bridge is running and the URL is correct. "
            f"You can verify with: curl {config.server_url}/health"
        )
```

**Integrated into command flow:**
- Checks API availability before executing any command (except health check)
- Provides actionable error messages with troubleshooting steps
- Prevents cryptic error messages from failed requests

**Enhanced error handling:**

```bash
# Before
HTTP request failed: ConnectError(...)

# After
Unable to connect to the bridge API at http://127.0.0.1:8000.
Please check that the bridge is running and the URL is correct.
You can verify with: curl http://127.0.0.1:8000/health
```

---

## Security Improvements

**Input Validation:**
- ✅ All user-provided JSON validated before use
- ✅ IP addresses validated for correct format
- ✅ Numeric ranges enforced (universe, channel, lengths)
- ✅ Template/capability keys validated against allowed sets
- ✅ Prevents injection attacks through validation

**Error Handling:**
- ✅ Detailed error messages with actionable information
- ✅ Validation errors include valid ranges/options
- ✅ Connection errors provide troubleshooting steps
- ✅ Clear distinction between error types

**Robustness:**
- ✅ Connection pooling prevents resource exhaustion
- ✅ Automatic retries handle transient failures
- ✅ API availability check prevents cryptic errors
- ✅ Graceful degradation when API unavailable

---

## Code Quality Improvements

**Maintainability:**
- ✅ Centralized validation in dedicated functions
- ✅ Reusable validation components
- ✅ Clear function documentation
- ✅ Consistent error formatting

**User Experience:**
- ✅ Clear, actionable error messages
- ✅ Suggestions for valid options
- ✅ Troubleshooting hints for connection issues
- ✅ Fast failure with helpful feedback

**Performance:**
- ✅ Connection pooling reduces overhead
- ✅ Keepalive reduces latency
- ✅ Automatic retries prevent user intervention

---

## Examples

### JSON Validation
```bash
# Invalid capabilities structure
$ govee-artnet devices add --id AA:BB --ip 192.168.1.10 \
  --capabilities '{"invalid": true}'
Error: Invalid capability key 'invalid'. Valid keys: brightness, color, temperature

# Invalid IP address
$ govee-artnet devices add --id AA:BB --ip 192.168.1.999
Error: Invalid IP address: 192.168.1.999 (octet out of range)

# Invalid universe range
$ govee-artnet mappings create --device-id AA:BB --universe 50000
Error: Universe must be between 0 and 32767, got: 50000
```

### API Availability
```bash
# Bridge not running
$ govee-artnet devices list
Unable to connect to the bridge API at http://127.0.0.1:8000.
Please check that the bridge is running and the URL is correct.
You can verify with: curl http://127.0.0.1:8000/health
```

### Connection Errors
```bash
# Connection timeout
$ govee-artnet status
Request timeout: Timeout('Request timeout')
The bridge at http://127.0.0.1:8000 is not responding
```

---

## Testing

- [x] Python syntax validation passed
- [x] All changes are backward compatible
- [x] Validation functions provide clear error messages
- [x] Connection pooling configured appropriately
- [x] API availability check prevents execution when API down
- [x] Error messages tested for clarity

---

## Impact

**Files Modified:** 1 (cli.py)  
**Lines Added:** ~200  
**Validation Functions:** 3 new  
**Commands Enhanced:** 4 (devices add/update, mappings create/update)  
**Error Handling:** 5+ improvements  

---

## Related

- Part of CLI Framework Improvement Plan (Phase 4)
- Focus: Security & Robustness
- Estimated effort: 6-8 hours ✅ Completed

---

## Next Steps

After Phase 4 merge:
- **Phase 5:** Configuration & Documentation
- **Phase 6:** Performance Optimizations
- **Phase 7:** Advanced Features
