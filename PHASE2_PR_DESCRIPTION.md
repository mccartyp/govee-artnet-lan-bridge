## Phase 2: WebSocket Streaming and Real-time Monitoring

**⚠️ Depends on:** Phase 1 PR - This PR builds on Phase 1's log buffer and event bus infrastructure.

**Base branch should be:** `claude/plan-cli-shell-expansion-NsJ3w`

### Overview
This PR implements Phase 2 of the [CLI Shell Expansion Plan](./CLI_SHELL_EXPANSION_PLAN.md), adding real-time WebSocket streaming for logs and events, plus interactive monitoring commands.

### New Features

#### 1. 🔌 WebSocket Log Streaming API
- **Endpoint**: `WebSocket /logs/stream`
- Real-time log delivery to connected clients
- Client-side filtering by `level` and `logger`
- Automatic ping/pong keepalive (30s timeout)
- Graceful disconnect handling
- Subscribes to Phase 1's log buffer for live updates

```bash
# Connect via WebSocket and stream logs
ws://localhost:8000/logs/stream

# Send filters:
{"level": "ERROR", "logger": "govee.discovery"}
```

#### 2. 📡 WebSocket Event Streaming API
- **Endpoint**: `WebSocket /events/stream`
- Stream system events in real-time
- Events: `device_discovered`, `mapping_created`, etc.
- Wildcard subscription to all event types
- Ping/pong keepalive

#### 3. 📝 Interactive Log Tailing (Shell)
```bash
# Real-time log streaming (like tail -f)
govee> logs tail
govee> logs tail --level ERROR
govee> logs tail --logger govee.discovery

# Output:
[2025-12-26T10:30:45.123Z] INFO    | govee.discovery           | Device discovered
[2025-12-26T10:30:46.234Z] ERROR   | govee.artnet              | Failed to process packet
```

Features:
- WebSocket-based real-time streaming
- Formatted, human-readable output
- Level and logger filtering
- Keyboard interrupt (Ctrl+C) to stop
- Graceful error handling if `websockets` not installed

#### 4. 📊 Monitor Commands (Shell)
```bash
# Live system dashboard
govee> monitor dashboard
============================================================
  Govee ArtNet Bridge - Dashboard
============================================================

Status: ✓ OK

Devices:
  Discovered: 5
  Manual:     2
  Total:      7

Message Queue:
  Current depth: 12

Subsystems:
  ✓ discovery      ok
  ✓ artnet         ok
  ✓ sender         ok
  ✓ api            ok
============================================================

# Detailed statistics
govee> monitor stats
```

### Technical Implementation

**API Changes** (`api.py`):
- Added `WebSocket`, `WebSocketDisconnect` imports from FastAPI
- `/logs/stream` endpoint with async log delivery
  - Uses subscriber pattern from Phase 1's log buffer
  - Dynamic filtering based on client messages
  - 30-second timeout with automatic ping messages
- `/events/stream` endpoint with async event delivery
  - Wildcard subscription to event bus
  - Ping/pong for keepalive
- Proper cleanup via `unsubscribe()` on disconnect

**Shell Changes** (`shell.py`):
- `_logs_tail()` method for WebSocket log streaming
  - Uses `websockets.sync.client` for synchronous WebSocket
  - Formats logs for console output: `[timestamp] LEVEL | logger | message`
  - Parses `--level` and `--logger` filters
- `do_monitor()` command dispatcher
- `_monitor_dashboard()` for formatted status display
  - Visual indicators (✓/✗) for status
  - Device counts, queue depth, subsystem health
- `_monitor_stats()` for raw statistics output
- Error handling for missing `websockets` library

**Dependencies** (`pyproject.toml`):
- Added `websockets>=12.0` for WebSocket client support

### Example Session

```bash
$ govee-artnet shell
Connected to http://127.0.0.1:8000
Govee ArtNet Bridge Shell. Type 'help' or '?' for commands, 'exit' to quit.

govee> logs tail --level INFO
Streaming logs (Press Ctrl+C to stop)...
  Level filter: INFO

[2025-12-26T10:30:45.123Z] INFO    | govee.api                 | Handled request
[2025-12-26T10:30:46.234Z] INFO    | govee.discovery           | Running discovery cycle
[2025-12-26T10:30:47.345Z] INFO    | govee.artnet              | Packet received
^C
Stopped tailing logs

govee> monitor dashboard
Fetching dashboard data...

============================================================
  Govee ArtNet Bridge - Dashboard
============================================================

Status: ✓ OK

Devices:
  Discovered: 3
  Manual:     1
  Total:      4

Message Queue:
  Current depth: 5

Subsystems:
  ✓ discovery      ok
  ✓ artnet         ok
  ✓ sender         ok
  ✓ api            ok
  ✓ poller         ok

============================================================

govee> exit
Goodbye!
```

### Testing

**Manual Testing Performed:**
- ✅ WebSocket log streaming endpoint
- ✅ WebSocket event streaming endpoint
- ✅ `logs tail` command with filters
- ✅ `monitor dashboard` command
- ✅ `monitor stats` command
- ✅ Graceful handling when `websockets` not installed
- ✅ Keyboard interrupt (Ctrl+C) handling

**Integration:**
- ✅ Works with Phase 1's log buffer
- ✅ Works with Phase 1's event bus
- ✅ Backward compatible with existing CLI commands

### Files Changed
- `src/govee_artnet_lan_bridge/api.py` (+117 lines)
- `src/govee_artnet_lan_bridge/shell.py` (+160 lines)
- `pyproject.toml` (+1 line)

**Total:** +284 lines of new functionality

### Performance & Scalability
- WebSocket connections are async and non-blocking
- Subscriber callbacks execute without holding locks
- Ping/pong prevents idle timeout issues
- Graceful handling of slow/disconnected clients

### Security Considerations
- WebSocket endpoints respect existing auth (no new vulnerabilities)
- No sensitive data in WebSocket streams (logs already redacted)
- Connection cleanup prevents resource leaks

### Documentation
- Updated function docstrings with WebSocket protocol details
- Clear error messages for missing dependencies
- Help text updated for new `logs tail` and `monitor` commands

### Related
- **Builds on:** Phase 1 PR - Log buffer and event bus
- **Implements:** [CLI_SHELL_EXPANSION_PLAN.md](./CLI_SHELL_EXPANSION_PLAN.md) Phase 2
- **Next:** Phase 3 will add enhanced UI with `prompt_toolkit` and `rich`

### Checklist
- [x] WebSocket log streaming implemented
- [x] WebSocket event streaming implemented
- [x] Interactive log tailing working
- [x] Monitor commands functional
- [x] Dependencies added
- [x] Manual testing completed
- [x] Backward compatible
- [x] Documentation updated
- [x] Clean commit history

---

**Ready for review!** This adds powerful real-time monitoring capabilities while maintaining full backward compatibility. 🚀
