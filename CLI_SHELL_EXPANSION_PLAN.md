# CLI Shell Expansion Plan

## Overview

This document outlines the plan to expand the `govee-artnet` CLI from a simple command-line tool to a full-featured interactive shell with real-time monitoring, log viewing, and comprehensive management capabilities.

## Current State

### Existing CLI (`cli.py`)
- **Architecture**: Single-shot command execution via `argparse`
- **Commands**:
  - `health` - Check API health
  - `status` - Show API status/metrics
  - `devices` - Device management (list, add, update, enable, disable, test, command)
  - `mappings` - Mapping management (list, get, create, update, delete, channel-map)
- **Output**: JSON or YAML to stdout
- **Authentication**: API key or bearer token support

### Existing API (`api.py`)
- **FastAPI-based REST API** on port 8000
- **Endpoints**:
  - `/health` - Health check
  - `/status` - Status and metrics
  - `/devices/*` - Device CRUD operations
  - `/mappings/*` - Mapping CRUD operations
  - `/channel-map` - Channel mapping visualization
  - `/metrics` - Prometheus metrics
  - `/reload` - Reload configuration

### Logging System (`logging.py`)
- **Output**: Console/stdout only (no file logging currently)
- **Formats**: Plain text or JSON
- **Loggers**: Structured logging for discovery, artnet, sender, API, etc.
- **Levels**: Configurable per subsystem (INFO, DEBUG, WARNING, ERROR)

## Proposed Architecture

### 1. CLI Shell Mode

Create an interactive shell that runs alongside the existing command-line mode:

```python
# New file: src/govee_artnet_lan_bridge/shell.py

class GoveeShell:
    """Interactive shell for govee-artnet management."""

    - REPL loop using cmd or prompt_toolkit
    - Command history and autocomplete
    - Context-aware help system
    - Real-time event streaming
    - Multi-pane UI support (optional: using rich or textual)
```

**Entry Point**:
```bash
# Single command mode (existing)
govee-artnet devices list

# Interactive shell mode (new)
govee-artnet shell
govee-artnet --shell
govee-artnet -i  # interactive
```

### 2. Shell Commands

#### Core Shell Commands

1. **Session Management**
   - `connect [--server-url URL]` - Connect to bridge server
   - `disconnect` - Disconnect from server
   - `reconnect` - Reconnect to server
   - `status` - Show connection status
   - `exit` / `quit` - Exit shell

2. **Device Management** (existing CLI commands available in shell)
   - `devices list` - List devices
   - `devices add ...` - Add device
   - `devices update ...` - Update device
   - `devices enable/disable ID` - Enable/disable device
   - `devices watch` - Watch device changes in real-time
   - `devices monitor ID` - Monitor specific device state

3. **Mapping Management** (existing CLI commands available in shell)
   - `mappings list`
   - `mappings create ...`
   - `mappings update ...`
   - `mappings delete ID`
   - `channel-map` - Show channel map

4. **Log Viewing** (NEW)
   - `logs [--lines N] [--level LEVEL] [--logger NAME]` - View last N log lines
   - `logs tail [--follow] [--level LEVEL]` - Tail logs in real-time
   - `logs search PATTERN [--lines N]` - Search logs
   - `logs clear` - Clear log buffer (client-side)
   - `logs filter --logger NAME --level LEVEL` - Filter logs
   - `logs export --file PATH [--format json|text]` - Export logs

5. **Monitoring** (NEW)
   - `monitor dashboard` - Full system dashboard
   - `monitor artnet` - ArtNet packet stream
   - `monitor devices` - Device state changes
   - `monitor queue` - Message queue depth
   - `monitor rate-limit` - Rate limiter status
   - `monitor health` - Health check status
   - `stats` - Show system statistics
   - `metrics` - Show Prometheus metrics

6. **Configuration** (NEW)
   - `config show` - Show current config
   - `config reload` - Request config reload
   - `config set KEY VALUE` - Set config value (client preferences)

7. **Shell Utilities**
   - `help [COMMAND]` - Show help
   - `history` - Show command history
   - `clear` - Clear screen
   - `output --format [json|yaml|table]` - Set output format
   - `alias NAME COMMAND` - Create command alias
   - `script FILE` - Execute commands from file

## REST API Extensions

To support the new shell features, the following API endpoints need to be added:

### 1. Log Streaming API

**File**: `src/govee_artnet_lan_bridge/api.py`

#### A. Log Buffer Endpoint (Historical Logs)

```python
@app.get("/logs")
async def get_logs(
    lines: int = Query(default=100, ge=1, le=10000),
    level: Optional[str] = Query(default=None),
    logger: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
) -> LogResponse:
    """
    Get recent log entries from in-memory buffer.

    Query Parameters:
    - lines: Number of log lines to return (default: 100, max: 10000)
    - level: Filter by log level (DEBUG, INFO, WARNING, ERROR)
    - logger: Filter by logger name (e.g., 'govee.discovery')
    - offset: Skip first N lines (for pagination)

    Returns:
    {
        "total": 1523,
        "offset": 0,
        "lines": 100,
        "logs": [
            {
                "ts": "2025-12-26T10:30:45.123Z",
                "level": "INFO",
                "logger": "govee.discovery",
                "message": "Device discovered",
                "extra": {...}
            },
            ...
        ]
    }
    """
```

#### B. Log Search Endpoint

```python
@app.get("/logs/search")
async def search_logs(
    pattern: str = Query(...),
    lines: int = Query(default=100, ge=1, le=10000),
    regex: bool = Query(default=False),
    case_sensitive: bool = Query(default=False),
) -> LogResponse:
    """
    Search logs by pattern.

    Query Parameters:
    - pattern: Search pattern (string or regex if regex=true)
    - lines: Max results to return
    - regex: Use regex matching
    - case_sensitive: Case-sensitive search
    """
```

#### C. WebSocket Log Stream

```python
@app.websocket("/logs/stream")
async def stream_logs(
    websocket: WebSocket,
    level: Optional[str] = Query(default=None),
    logger: Optional[str] = Query(default=None),
):
    """
    Stream logs in real-time via WebSocket.

    Client sends filter updates:
    {"action": "filter", "level": "INFO", "logger": "govee.discovery"}

    Server sends log entries:
    {
        "ts": "2025-12-26T10:30:45.123Z",
        "level": "INFO",
        "logger": "govee.discovery",
        "message": "Device discovered",
        "extra": {...}
    }
    """
```

### 2. Event Streaming API

**Purpose**: Real-time notifications for device changes, mapping updates, etc.

```python
@app.websocket("/events/stream")
async def stream_events(websocket: WebSocket):
    """
    Stream system events in real-time.

    Events:
    - device_discovered
    - device_updated
    - device_offline
    - mapping_created
    - mapping_updated
    - mapping_deleted
    - artnet_packet_received
    - queue_depth_changed
    - rate_limit_triggered

    Message format:
    {
        "event": "device_discovered",
        "timestamp": "2025-12-26T10:30:45.123Z",
        "data": {...}
    }
    """
```

### 3. Enhanced Status API

```python
@app.get("/status/detailed")
async def detailed_status() -> dict:
    """
    Extended status with more granular metrics.

    Returns:
    {
        "uptime_seconds": 3600,
        "discovery": {
            "last_scan": "2025-12-26T10:30:00Z",
            "devices_discovered": 5,
            "devices_online": 4,
            "devices_offline": 1
        },
        "artnet": {
            "packets_received": 15234,
            "packets_processed": 15230,
            "packets_dropped": 4,
            "universes_active": [0, 1],
            "last_packet": "2025-12-26T10:30:45Z"
        },
        "queue": {
            "depth": 23,
            "max_depth": 1000,
            "enqueued_total": 45234,
            "processed_total": 45211
        },
        "rate_limit": {
            "tokens_available": 15.5,
            "tokens_max": 20,
            "refill_rate": 10.0,
            "throttled_count": 12
        },
        "health": {
            "status": "ok",
            "subsystems": {...}
        }
    }
    """
```

### 4. Configuration API

```python
@app.get("/config")
async def get_config() -> dict:
    """Get current server configuration (sanitized)."""

@app.post("/config/validate")
async def validate_config(config: dict) -> dict:
    """Validate configuration without applying it."""
```

### 5. Metrics History API

```python
@app.get("/metrics/history")
async def metrics_history(
    metric: str,
    duration: int = Query(default=3600, ge=60, le=86400),
    resolution: int = Query(default=60, ge=1, le=3600),
) -> dict:
    """
    Get historical metrics data.

    Parameters:
    - metric: Metric name (e.g., 'artnet_packets_total')
    - duration: Time window in seconds
    - resolution: Sample interval in seconds

    Returns:
    {
        "metric": "artnet_packets_total",
        "start": "2025-12-26T09:30:00Z",
        "end": "2025-12-26T10:30:00Z",
        "resolution": 60,
        "samples": [
            {"timestamp": "2025-12-26T09:30:00Z", "value": 1234},
            {"timestamp": "2025-12-26T09:31:00Z", "value": 1250},
            ...
        ]
    }
    """
```

## Implementation Components

### 1. Log Buffer Service

**File**: `src/govee_artnet_lan_bridge/log_buffer.py`

```python
class LogBuffer:
    """In-memory circular buffer for recent log entries."""

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self.buffer = deque(maxlen=max_size)
        self.subscribers = []  # WebSocket subscribers

    def append(self, log_entry: LogEntry) -> None:
        """Add log entry and notify subscribers."""

    def query(
        self,
        lines: int = 100,
        level: Optional[str] = None,
        logger: Optional[str] = None,
        offset: int = 0,
    ) -> List[LogEntry]:
        """Query log entries with filters."""

    def search(
        self,
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> List[LogEntry]:
        """Search log entries."""

    async def subscribe(self, callback: Callable) -> None:
        """Subscribe to new log entries."""
```

**Integration**: Add custom logging handler that feeds the buffer:

```python
class BufferHandler(logging.Handler):
    """Logging handler that feeds LogBuffer."""

    def __init__(self, buffer: LogBuffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        entry = self._format_entry(record)
        self.buffer.append(entry)
```

### 2. Event Bus

**File**: `src/govee_artnet_lan_bridge/events.py`

```python
class EventBus:
    """Pub/sub event bus for system events."""

    def __init__(self):
        self.subscribers = defaultdict(list)

    async def publish(self, event_type: str, data: Any) -> None:
        """Publish event to subscribers."""

    async def subscribe(
        self,
        event_type: str,
        callback: Callable,
    ) -> Callable[[], None]:
        """Subscribe to event type. Returns unsubscribe function."""
```

**Integration Points**:
- Device discovery: Publish `device_discovered`, `device_updated`
- Mapping changes: Publish `mapping_created`, `mapping_deleted`
- ArtNet: Publish `artnet_packet_received`
- Queue: Publish `queue_depth_changed`

### 3. Interactive Shell

**File**: `src/govee_artnet_lan_bridge/shell.py`

```python
import cmd
import asyncio
from typing import Optional
import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.table import Table

class GoveeShell(cmd.Cmd):
    """Interactive shell for govee-artnet."""

    intro = "Govee ArtNet Bridge Shell. Type 'help' for commands."
    prompt = "govee> "

    def __init__(self, config: ClientConfig):
        super().__init__()
        self.config = config
        self.client = httpx.Client(
            base_url=config.server_url,
            headers=self._build_headers(),
        )
        self.console = Console()
        self.log_ws = None
        self.event_ws = None

    # Command implementations
    def do_devices(self, arg: str) -> None:
        """Manage devices: list, add, update, enable, disable."""

    def do_logs(self, arg: str) -> None:
        """View logs: logs [--lines N] [--tail]."""

    def do_monitor(self, arg: str) -> None:
        """Monitor system: dashboard, artnet, devices, queue."""

    async def _tail_logs(self, filters: dict) -> None:
        """Stream logs via WebSocket."""

    def _display_table(self, data: list, columns: list) -> None:
        """Display data as a formatted table using Rich."""
```

**Dependencies**:
- `prompt_toolkit` - Enhanced REPL with autocomplete
- `rich` - Terminal formatting and tables
- `websockets` - WebSocket client (or httpx with WebSocket support)

### 4. Configuration Updates

**File**: `src/govee_artnet_lan_bridge/config.py`

Add new configuration options:

```python
@dataclass(frozen=True)
class Config:
    # ... existing fields ...

    # Log buffer settings
    log_buffer_size: int = 10000
    log_buffer_enabled: bool = True

    # Event streaming settings
    event_bus_enabled: bool = True

    # Metrics history settings
    metrics_history_enabled: bool = False
    metrics_history_duration: int = 3600
    metrics_history_resolution: int = 60
```

## Shell Features in Detail

### 1. Log Viewing Features

#### Basic Log Viewing
```bash
govee> logs
# Shows last 100 log lines

govee> logs --lines 500
# Shows last 500 lines

govee> logs --level ERROR
# Shows only ERROR level logs

govee> logs --logger govee.discovery
# Shows logs from discovery subsystem only
```

#### Real-time Tail
```bash
govee> logs tail
# Start tailing logs (like tail -f)
# Press Ctrl+C to stop

govee> logs tail --level INFO --logger govee.artnet
# Tail filtered logs
```

#### Log Search
```bash
govee> logs search "Device discovered"
# Search for specific text

govee> logs search "error.*timeout" --regex
# Regex search
```

#### Export Logs
```bash
govee> logs export --file /tmp/logs.txt
# Export logs to file

govee> logs export --file /tmp/logs.json --format json
# Export as JSON
```

### 2. Real-time Monitoring

#### Dashboard View
```bash
govee> monitor dashboard
┌─────────────────────────────────────────────┐
│ Govee ArtNet Bridge - System Dashboard     │
├─────────────────────────────────────────────┤
│ Status: OK                   Uptime: 2h 15m │
├─────────────────────────────────────────────┤
│ Devices:                                    │
│   Online:    4 / 5                          │
│   Offline:   1 / 5                          │
│                                             │
│ ArtNet:                                     │
│   Packets:   15,234                         │
│   Universes: [0, 1]                         │
│   Last:      2s ago                         │
│                                             │
│ Queue:                                      │
│   Depth:     23 / 1000                      │
│   Processed: 45,211                         │
│                                             │
│ Rate Limit:                                 │
│   Tokens:    15.5 / 20                      │
│   Throttled: 12                             │
└─────────────────────────────────────────────┘
Press Ctrl+C to exit
```

#### Device Monitoring
```bash
govee> devices watch
# Watch device state changes in real-time
[10:30:45] Device AA:BB:CC:DD:EE:FF came online
[10:31:12] Device AA:BB:CC:DD:EE:01 brightness changed: 128 -> 255
[10:31:45] Device AA:BB:CC:DD:EE:FF went offline
```

#### ArtNet Stream
```bash
govee> monitor artnet
# Show ArtNet packets as they arrive
[10:30:45] Universe 0, Ch 1-3: [255, 128, 64]
[10:30:46] Universe 0, Ch 10-13: [200, 100, 50, 128]
[10:30:47] Universe 1, Ch 1-4: [255, 0, 0, 255]
```

### 3. Command History and Autocomplete

- Command history saved to `~/.govee_shell_history`
- Tab completion for commands and arguments
- Reverse search with Ctrl+R
- Persistent across sessions

### 4. Output Formatting

```bash
govee> output --format table
# Switch to table format

govee> output --format json
# Switch to JSON format

govee> output --format yaml
# Switch to YAML format
```

### 5. Scripting Support

```bash
# Create script file
$ cat > monitor.govee <<EOF
connect
devices list
logs --lines 50
monitor dashboard
EOF

# Run script
govee> script monitor.govee
# Or from command line:
$ govee-artnet shell --script monitor.govee
```

### 6. Aliases

```bash
govee> alias dl "devices list"
govee> dl
# Executes "devices list"

govee> alias lt "logs tail"
govee> lt
# Executes "logs tail"
```

## Additional CLI Shell Features

### 1. Context Management

```bash
govee> context set device AA:BB:CC:DD:EE:FF
# Set default device for subsequent commands

govee> context set universe 0
# Set default universe

govee> context show
# Show current context

govee> context clear
# Clear context
```

### 2. Bookmarks

```bash
govee> bookmark add dev1 --device-id AA:BB:CC:DD:EE:FF
# Bookmark a device

govee> bookmark list
# List bookmarks

govee> devices command @dev1 --on --brightness 200
# Use bookmark in commands
```

### 3. Batch Operations

```bash
govee> devices enable @all
# Enable all devices

govee> devices command @online --brightness 128
# Send command to all online devices

govee> mappings delete --universe 0
# Delete all mappings in universe 0
```

### 4. Watch Mode

```bash
govee> watch devices list
# Continuously refresh device list every 2 seconds

govee> watch --interval 5 stats
# Watch stats with 5-second interval
```

### 5. Notifications

```bash
govee> notify on device_offline
# Enable notifications for offline devices

govee> notify on rate_limit_triggered
# Notify when rate limit is hit

govee> notify off device_discovered
# Disable discovery notifications
```

### 6. Shell Settings

```bash
govee> set prompt "bridge> "
# Change prompt

govee> set color on
# Enable colored output

govee> set timestamps on
# Show timestamps in output

govee> set paging on
# Enable paging for long output

govee> settings save
# Save settings to ~/.govee_shell_config
```

### 7. Debugging Tools

```bash
govee> debug artnet --universe 0 --channel 1-10
# Debug ArtNet channel values

govee> debug device AA:BB:CC:DD:EE:FF
# Show device debug info

govee> debug queue
# Show queue details

govee> trace on
# Enable request tracing

govee> trace off
# Disable request tracing
```

### 8. Performance Tools

```bash
govee> benchmark device AA:BB:CC:DD:EE:FF --count 100
# Send 100 test commands and measure latency

govee> stats --since 1h
# Show statistics for last hour

govee> stats reset
# Reset statistics
```

### 9. Session Management

```bash
govee> session save my_session
# Save current session (context, aliases, settings)

govee> session load my_session
# Load saved session

govee> session list
# List saved sessions

govee> session delete my_session
# Delete session
```

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Create log buffer service
- [ ] Add log buffer API endpoints (`/logs`, `/logs/search`)
- [ ] Implement basic shell with `cmd` module
- [ ] Add core commands: devices, mappings, logs (basic)
- [ ] Test log viewing functionality

### Phase 2: Real-time Features (Week 3-4)
- [ ] Implement WebSocket log streaming
- [ ] Create event bus
- [ ] Add event streaming API
- [ ] Implement `logs tail` command
- [ ] Add `monitor` commands (dashboard, devices, artnet)

### Phase 3: Enhanced UI (Week 5-6)
- [ ] Integrate `prompt_toolkit` for autocomplete
- [ ] Add `rich` for formatted tables and colors
- [ ] Implement multi-pane monitoring views
- [ ] Add command history and persistent settings
- [ ] Create help system with examples

### Phase 4: Advanced Features (Week 7-8)
- [ ] Implement metrics history API
- [ ] Add batch operations
- [ ] Implement context and bookmarks
- [ ] Add watch mode
- [ ] Implement scripting support
- [ ] Add session management

### Phase 5: Polish and Testing (Week 9-10)
- [ ] Comprehensive testing
- [ ] Documentation
- [ ] Performance optimization
- [ ] Error handling improvements
- [ ] User testing and feedback

## Testing Strategy

### Unit Tests
- Log buffer operations
- Event bus pub/sub
- API endpoint responses
- Shell command parsing

### Integration Tests
- WebSocket connections
- Log streaming end-to-end
- Event propagation
- API + Shell integration

### Performance Tests
- Log buffer with 10k+ entries
- Multiple WebSocket clients
- High-frequency event publishing
- Memory usage under load

## Documentation Requirements

### User Documentation
- Shell command reference
- Interactive tutorial
- Example workflows
- Troubleshooting guide

### Developer Documentation
- API reference for new endpoints
- Log buffer architecture
- Event bus design
- WebSocket protocol spec

## Dependencies to Add

```toml
# Add to pyproject.toml
dependencies = [
    # ... existing deps ...
    "prompt_toolkit>=3.0.0",  # Enhanced REPL
    "rich>=13.0.0",           # Terminal formatting
    "websockets>=12.0",       # WebSocket support (or use httpx websockets)
]
```

## Configuration Example

```toml
# config.toml additions
[shell]
history_file = "~/.govee_shell_history"
history_size = 1000
enable_autocomplete = true
enable_colors = true
default_output_format = "table"

[logging]
buffer_enabled = true
buffer_size = 10000
stream_enabled = true

[events]
bus_enabled = true
stream_enabled = true

[metrics]
history_enabled = true
history_duration = 3600
history_resolution = 60
```

## Security Considerations

1. **Log Redaction**: Ensure sensitive data (API keys, tokens) are redacted in logs
2. **WebSocket Authentication**: Require auth for WebSocket connections
3. **Rate Limiting**: Apply rate limits to log/event streaming endpoints
4. **Buffer Size Limits**: Enforce max buffer sizes to prevent memory exhaustion
5. **Access Control**: Restrict certain shell features based on permissions

## Performance Considerations

1. **Log Buffer**: Use circular buffer with configurable max size
2. **Event Bus**: Async pub/sub to avoid blocking
3. **WebSocket Backpressure**: Handle slow consumers gracefully
4. **Metrics History**: Optional feature, disabled by default
5. **Memory Management**: Monitor and limit buffer growth

## Migration Path

1. **Backward Compatibility**: All existing CLI commands work as-is
2. **Gradual Adoption**: Shell mode is opt-in via `govee-artnet shell`
3. **Feature Flags**: New API endpoints can be disabled via config
4. **Documentation**: Clear migration guide for users

## Success Metrics

1. Shell response time < 100ms for interactive commands
2. Log streaming latency < 50ms
3. Support for 100+ concurrent WebSocket clients
4. Memory usage < 100MB for 10k log buffer
5. 95%+ user satisfaction in feedback

## Future Enhancements

1. **TUI Mode**: Full terminal UI with split panes (using `textual`)
2. **Plugin System**: Allow custom shell commands
3. **Remote Shell**: Shell-to-shell communication for distributed debugging
4. **AI Assistant**: Natural language command interpretation
5. **Visual Graphs**: ASCII/Unicode charts for metrics
6. **Export Reports**: Generate PDF/HTML reports from shell

## Conclusion

This plan transforms the `govee-artnet` CLI from a basic command-line tool into a powerful interactive shell with real-time monitoring, log analysis, and comprehensive system management capabilities. The phased approach allows for incremental development and testing while maintaining backward compatibility with the existing CLI.
