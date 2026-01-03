# Architecture Changes: dmx-lan-bridge Multi-Input Protocol Support

## Summary

This document outlines the architectural refactoring to support multiple DMX input protocols (ArtNet, sACN, etc.) with priority-based merging.

## Changes Made

### 1. New File: `src/artnet_lan_bridge/dmx.py`

Created unified DMX abstraction layer:

- **`DmxFrame`**: Protocol-agnostic DMX data structure
  - 512 DMX channels (0-255)
  - Universe number
  - Priority (0-200)
  - Source protocol identifier
  - Timestamp for timeout detection

- **`PriorityMerger`**: Handles priority-based source merging
  - sACN sources: Use native priority (0-200)
  - **ArtNet sources: Fixed priority 50** (below sACN default of 100)
  - Automatic timeout (2.5 seconds per E1.31 spec)
  - Logs priority changes

- **`DmxMappingService`**: Protocol-agnostic mapping service
  - Receives DmxFrames from any input protocol
  - Applies priority merging
  - Performs DMX→Device mapping
  - Handles debouncing and change detection
  - Subscribes to mapping change events

### 2. Refactored: `src/artnet_lan_bridge/artnet.py`

Simplified **`ArtNetService`** to be input-protocol only:

**Before:**
- Parsed ArtNet packets
- **Loaded and managed DMX mappings**
- **Applied mappings to generate device updates**
- **Handled debouncing and change detection**
- **Subscribed to mapping events**

**After:**
- Parses Art Net packets
- Converts to `DmxFrame` with priority=50
- Forwards to `DmxMappingService`
- **All mapping logic removed** (now in DmxMappingService)

**Key Changes:**
- Constructor now takes `dmx_mapper` parameter
- Removed: `_universe_mappings`, `_last_payloads`, `_pending_updates`, etc.
- Removed: `_reload_mappings()`, `_schedule_update()`, `_flush_after()`, etc.
- Simplified `handle_packet()`: Just convert and forward

### 3. Still TODO: Update `src/artnet_lan_bridge/__main__.py`

Need to wire up the new architecture:

```python
# Add to RunningServices
@dataclass
class RunningServices:
    ...
    dmx_mapping: Optional[DmxMappingService] = None  # NEW
    artnet: Optional[ArtNetService] = None

# NEW: DMX mapping loop
async def _dmx_mapping_loop(...):
    from .dmx import DmxMappingService
    service = DmxMappingService(config, store, artnet_state, event_bus)
    await service.start()
    services.dmx_mapping = service
    try:
        await stop_event.wait()
    finally:
        await service.stop()

# UPDATED: ArtNet loop
async def _artnet_loop(...):
    # Wait for DMX mapping service to be ready
    dmx_mapper = services.dmx_mapping
    if not dmx_mapper:
        raise RuntimeError("DMX mapping service must start first")

    service = ArtNetService(config, dmx_mapper=dmx_mapper)
    await service.start()
    ...

# UPDATED: Task creation order
tasks = [
    protocol_task,
    asyncio.create_task(_dmx_mapping_loop(...)),  # NEW - Start BEFORE artnet
    asyncio.create_task(_artnet_loop(...)),        # Uses dmx_mapping
    ...
]
```

## Priority Merging Behavior

### When Both ArtNet and sACN Active

**Scenario 1: Default Configuration**
```
sACN Console (priority 100) → Universe 0, RGB values
ArtNet Console (priority 50) → Universe 0, different RGB values

Result: sACN wins (100 > 50)
Devices show sACN colors
```

**Scenario 2: User Lowers sACN Priority**
```
sACN Console (priority 25)  → Universe 0
ArtNet Console (priority 50) → Universe 0

Result: ArtNet wins (50 > 25)
Devices show ArtNet colors
```

**Scenario 3: Separate Universes**
```
sACN Console → Universe 0
ArtNet Console → Universe 1

Result: No conflict, both active
Universe 0 devices controlled by sACN
Universe 1 devices controlled by ArtNet
```

### Priority Levels

```
200: Emergency override (sACN only)
150: Primary console (sACN)
100: sACN default          ← Most sACN consoles
 50: ArtNet (fixed)        ← All ArtNet traffic
 25: Backup console (sACN)
  0: Lowest priority (sACN)
```

## Benefits

### 1. Clean Separation of Concerns
- **Input protocols** (ArtNet, sACN): Parse packets, create DmxFrames
- **Mapping service**: DMX→Device conversion (protocol-agnostic)
- **Device protocols** (Govee, LIFX): Device control

### 2. Easy to Add New Input Protocols
To add sACN (future):
```python
# src/artnet_lan_bridge/sacn.py
class SacnService:
    def __init__(self, dmx_mapper):
        self.dmx_mapper = dmx_mapper

    def handle_packet(self, packet: SacnPacket):
        frame = DmxFrame(
            universe=packet.universe,
            data=packet.data,
            priority=packet.priority,  # Native sACN priority!
            source_protocol="sacn",
            ...
        )
        await self.dmx_mapper.process_dmx_frame(frame)
```

### 3. Priority-Based Control
- Professional users get priority control (sACN)
- Hobbyist users don't need to think about it (ArtNet just works)
- Graceful failover when primary source fails
- Multiple consoles can coexist

### 4. Single Mapping Configuration
- Users create mappings once
- Works with ArtNet, sACN, or both
- No duplicate configuration needed

## Testing Considerations

### Unit Tests Needed
1. **DmxFrame validation**: Test 512-byte requirement, priority range
2. **PriorityMerger**: Test priority selection, timeout behavior
3. **DmxMappingService**: Test mapping application, debouncing
4. **ArtNetService**: Test packet parsing, DmxFrame conversion

### Integration Tests Needed
1. **ArtNet → Devices**: End-to-end with real mappings
2. **Priority merging**: Simulate multiple sources
3. **Timeout behavior**: Verify 2.5-second timeout
4. **Mapping reload**: Test dynamic mapping updates

## Migration Path

### Backwards Compatibility
- Existing ArtNet setups continue to work
- No breaking changes to API or configuration
- DMX mappings remain unchanged
- State snapshots compatible

### Future: Adding sACN
1. Implement `SacnService` (similar to ArtNetService)
2. Add `_sacn_loop()` in __main__.py
3. Update config to enable/disable protocols
4. No changes to existing ArtNet or mapping code

## Configuration (Future Enhancement)

```toml
[input.artnet]
enabled = true
port = 6454
priority = 50  # Fixed, not configurable

[input.sacn]
enabled = false  # Not implemented yet
port = 5568
# Uses native sACN priority from packets
```

## Summary of Files Changed

- ✅ **NEW**: `src/artnet_lan_bridge/dmx.py` (unified DMX layer)
- ✅ **MODIFIED**: `src/artnet_lan_bridge/artnet.py` (simplified to input-only)
- ⏳ **TODO**: `src/artnet_lan_bridge/__main__.py` (wire up services)
- ⏳ **TODO**: Update tests
- ⏳ **TODO**: Update documentation

## Next Steps

1. Update `__main__.py` to create DmxMappingService
2. Test with existing ArtNet setups
3. Rename package from `artnet_lan_bridge` → `dmx_lan_bridge`
4. Update pyproject.toml with legacy aliases
5. Update README/docs to reflect multi-protocol support
6. (Future) Implement sACN support using same pattern
