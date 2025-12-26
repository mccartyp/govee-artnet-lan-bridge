# Govee ArtNet CLI Interactive Shell

The `govee-artnet` CLI now includes a powerful interactive shell mode that provides real-time monitoring, log viewing, and comprehensive management capabilities.

## Features Overview

The interactive shell includes the following major features (developed across 5 phases):

### Phase 1: Core Shell & Log Viewing
- ✅ Interactive shell with command history
- ✅ Log buffer with in-memory storage (10,000 entries)
- ✅ Log viewing with filters (level, logger, lines)
- ✅ Log search with pattern matching and regex
- ✅ Event bus for pub/sub system events
- ✅ REST API endpoints for logs (`/logs`, `/logs/search`)

### Phase 2: WebSocket Streaming & Real-time Monitoring
- ✅ WebSocket log streaming (`/logs/stream`)
- ✅ WebSocket event streaming (`/events/stream`)
- ✅ Real-time log tailing (`logs tail`)
- ✅ Interactive monitoring commands (`monitor dashboard`, `monitor stats`)
- ✅ Client-side log filtering

### Phase 3: Enhanced UI with Rich Formatting
- ✅ Tab autocomplete for all commands
- ✅ Persistent command history (`~/.govee_artnet/shell_history`)
- ✅ Rich formatted tables with colors and borders
- ✅ Enhanced help system with examples
- ✅ Loading spinners and status indicators
- ✅ Color-coded output (green/red status, warnings, errors)
- ✅ Table output format option

### Phase 4: Advanced Productivity Features
- ✅ **Bookmarks**: Save device IDs and server URLs
- ✅ **Aliases**: Create command shortcuts
- ✅ **Watch Mode**: Continuous monitoring with auto-refresh
- ✅ **Batch Execution**: Run commands from files
- ✅ **Session Management**: Save and restore shell configuration

### Phase 5: Polish & User Experience
- ✅ Version command with feature list
- ✅ Tips command with helpful hints
- ✅ Improved welcome message with quick tips
- ✅ Better error handling and user feedback
- ✅ Comprehensive documentation

## Table of Contents

- [Getting Started](#getting-started)
- [Shell Modes](#shell-modes)
- [Core Commands](#core-commands)
- [Log Viewing](#log-viewing)
- [Real-time Monitoring](#real-time-monitoring)
- [Advanced Features](#advanced-features)
- [Configuration](#configuration)
- [Examples](#examples)

## Getting Started

### Entering the Shell

Start the interactive shell:

```bash
# Interactive shell mode
govee-artnet shell

# Alternative syntax
govee-artnet --shell
govee-artnet -i
```

### Quick Start

```bash
# Connect to your bridge (uses default http://127.0.0.1:8000)
govee> connect

# List devices
govee> devices list

# View recent logs
govee> logs --lines 50

# Tail logs in real-time
govee> logs tail

# Monitor system dashboard
govee> monitor dashboard

# Get help
govee> help

# Exit
govee> exit
```

## Shell Modes

### Command-Line Mode (Existing)

Single-shot command execution:

```bash
govee-artnet devices list
govee-artnet mappings create --device-id AA:BB:CC:DD:EE:FF --universe 0 --template rgb
govee-artnet status
```

### Interactive Shell Mode (New)

Persistent session with REPL:

```bash
govee-artnet shell
govee> devices list
govee> logs tail
govee> monitor dashboard
```

All existing CLI commands work in both modes!

## Core Commands

### Session Management

Connect and manage your connection to the bridge server:

```bash
# Connect to bridge server
govee> connect
govee> connect --server-url http://192.168.1.100:8000

# Check connection status
govee> status

# Reconnect
govee> reconnect

# Disconnect
govee> disconnect

# Exit shell
govee> exit
govee> quit
```

### Device Management

All existing device commands work in the shell:

```bash
# List all devices
govee> devices list

# Add a device
govee> devices add --id AA:BB:CC:DD:EE:FF --ip 192.168.1.10 --model-number H6160

# Update device
govee> devices update AA:BB:CC:DD:EE:FF --description "Living Room Strip"

# Enable/disable device
govee> devices enable AA:BB:CC:DD:EE:FF
govee> devices disable AA:BB:CC:DD:EE:FF

# Send command
govee> devices command AA:BB:CC:DD:EE:FF --on --brightness 200 --color ff8800

# Watch device changes in real-time
govee> devices watch
```

### Mapping Management

Manage DMX channel mappings:

```bash
# List mappings
govee> mappings list

# Create mapping with template
govee> mappings create --device-id AA:BB:CC:DD:EE:FF --universe 0 --start-channel 1 --template rgb

# Update mapping
govee> mappings update 1 --channel 10

# Delete mapping
govee> mappings delete 1

# View channel map
govee> channel-map
```

## Log Viewing

One of the most powerful features of the shell is real-time log viewing and searching.

### Basic Log Viewing

```bash
# View last 100 log lines (default)
govee> logs

# View specific number of lines
govee> logs --lines 500

# Filter by log level
govee> logs --level ERROR
govee> logs --level WARNING
govee> logs --level INFO
govee> logs --level DEBUG

# Filter by logger/subsystem
govee> logs --logger govee.discovery
govee> logs --logger govee.artnet
govee> logs --logger govee.sender

# Combine filters
govee> logs --lines 200 --level ERROR --logger govee.discovery
```

### Real-time Log Tailing

Stream logs in real-time (like `tail -f`):

```bash
# Tail all logs
govee> logs tail

# Tail with filters
govee> logs tail --level INFO
govee> logs tail --logger govee.artnet

# Press Ctrl+C to stop tailing
```

### Log Search

Search through logs with pattern matching:

```bash
# Simple text search
govee> logs search "Device discovered"

# Case-sensitive search
govee> logs search "ERROR" --case-sensitive

# Regex search
govee> logs search "error.*timeout" --regex
govee> logs search "192\.168\.1\.\d+" --regex

# Limit results
govee> logs search "device" --lines 50
```

### Export Logs

Save logs to file for analysis:

```bash
# Export to text file
govee> logs export --file /tmp/bridge-logs.txt

# Export as JSON
govee> logs export --file /tmp/bridge-logs.json --format json

# Export filtered logs
govee> logs --level ERROR export --file /tmp/errors.txt
```

## Real-time Monitoring

Monitor your bridge system in real-time with various monitoring views.

### System Dashboard

Full system overview with live updates:

```bash
govee> monitor dashboard
```

Output:
```
┌─────────────────────────────────────────────┐
│ Govee ArtNet Bridge - System Dashboard     │
├─────────────────────────────────────────────┤
│ Status: OK                   Uptime: 2h 15m │
├─────────────────────────────────────────────┤
│ Devices:                                    │
│   Online:    4 / 5                          │
│   Offline:   1 / 5                          │
│   Enabled:   5 / 5                          │
│                                             │
│ ArtNet:                                     │
│   Packets:   15,234 received                │
│   Universes: [0, 1]                         │
│   Last:      2 seconds ago                  │
│                                             │
│ Message Queue:                              │
│   Depth:     23 / 1000                      │
│   Enqueued:  45,234                         │
│   Processed: 45,211                         │
│                                             │
│ Rate Limiter:                               │
│   Tokens:    15.5 / 20                      │
│   Throttled: 12 times                       │
│                                             │
│ Health:                                     │
│   Discovery:  ✓ OK                          │
│   ArtNet:     ✓ OK                          │
│   Sender:     ✓ OK                          │
│   API:        ✓ OK                          │
└─────────────────────────────────────────────┘
Press Ctrl+C to exit
```

### Device Monitoring

Watch device state changes as they happen:

```bash
govee> devices watch

# Output shows real-time events:
[10:30:45] Device AA:BB:CC:DD:EE:FF came online
[10:31:12] Device AA:BB:CC:DD:EE:FF brightness: 128 -> 255
[10:31:45] Device AA:BB:CC:DD:EE:01 color: #ff0000 -> #00ff00
[10:32:00] Device AA:BB:CC:DD:EE:FF went offline
```

### ArtNet Stream Monitor

Watch ArtNet packets in real-time:

```bash
govee> monitor artnet

# Shows live DMX data:
[10:30:45] Universe 0, Ch 1-3: [255, 128, 64] (RGB)
[10:30:46] Universe 0, Ch 10-13: [200, 100, 50, 128] (RGBW)
[10:30:47] Universe 1, Ch 1-4: [255, 0, 0, 255]
```

### Queue Monitoring

Monitor message queue depth:

```bash
govee> monitor queue

# Shows queue statistics:
Queue Depth: 45 / 1000
Enqueue Rate: 12.5 msg/s
Process Rate: 12.3 msg/s
Oldest Message: 2.3s
```

### Health Monitoring

Track system health:

```bash
govee> monitor health

# Shows subsystem status:
Discovery:  ✓ OK (last success: 5s ago)
ArtNet:     ✓ OK (last success: 1s ago)
Sender:     ✓ OK (last success: 2s ago)
API:        ✓ OK (last success: 0s ago)
Poller:     ⚠ WARNING (3 consecutive failures)
```

### Statistics

View system statistics:

```bash
# Current statistics
govee> stats

# Statistics since specific time
govee> stats --since 1h
govee> stats --since 30m

# Reset statistics
govee> stats reset
```

## Advanced Features

### Command History

The shell maintains command history across sessions:

- **Up/Down arrows**: Navigate through command history
- **Ctrl+R**: Reverse search through history
- **history**: Show full command history

```bash
govee> history

# History is saved to ~/.govee_shell_history
```

### Tab Completion

Press **Tab** to autocomplete commands and arguments:

```bash
govee> dev<TAB>
devices

govee> devices li<TAB>
devices list

govee> logs --le<TAB>
logs --level
```

### Output Formatting

Change output format on the fly:

```bash
# Table format (default, uses Rich library)
govee> output --format table
govee> devices list
# Shows nicely formatted table

# JSON format
govee> output --format json
govee> devices list
# Shows JSON output

# YAML format
govee> output --format yaml
govee> devices list
# Shows YAML output
```

### Context Management

Set defaults to avoid repetitive typing:

```bash
# Set default device
govee> context set device AA:BB:CC:DD:EE:FF

# Now you can omit device ID
govee> devices command --on --brightness 200

# Set default universe
govee> context set universe 0

# View current context
govee> context show
Device: AA:BB:CC:DD:EE:FF
Universe: 0

# Clear context
govee> context clear
```

### Bookmarks

Save frequently used devices with friendly names:

```bash
# Create bookmark
govee> bookmark add living-room --device-id AA:BB:CC:DD:EE:FF

# Use bookmark in commands
govee> devices command @living-room --on --brightness 200

# List bookmarks
govee> bookmark list
living-room     -> AA:BB:CC:DD:EE:FF
bedroom         -> AA:BB:CC:DD:EE:01
kitchen         -> AA:BB:CC:DD:EE:02

# Delete bookmark
govee> bookmark delete living-room
```

### Aliases

Create shortcuts for frequently used commands:

```bash
# Create alias
govee> alias dl "devices list"
govee> alias lt "logs tail"
govee> alias mon "monitor dashboard"

# Use alias
govee> dl
# Executes "devices list"

# List aliases
govee> alias list
dl    -> devices list
lt    -> logs tail
mon   -> monitor dashboard

# Delete alias
govee> alias delete dl
```

### Watch Mode

Continuously refresh command output:

```bash
# Watch device list (refreshes every 2 seconds)
govee> watch devices list

# Custom refresh interval
govee> watch --interval 5 stats

# Watch with filters
govee> watch logs --lines 20 --level ERROR

# Press Ctrl+C to stop watching
```

### Batch Operations

Perform operations on multiple devices:

```bash
# Enable all devices
govee> devices enable @all

# Send command to all online devices
govee> devices command @online --brightness 128

# Send command to all offline devices
govee> devices command @offline --on

# Delete all mappings in universe
govee> mappings delete --universe 0
```

### Scripting

Execute commands from a file:

```bash
# Create script file
$ cat > startup.govee <<EOF
connect
devices list
logs --lines 50 --level ERROR
monitor dashboard
EOF

# Run script from shell
govee> script startup.govee

# Or from command line
$ govee-artnet shell --script startup.govee
```

### Session Management

Save and restore shell sessions:

```bash
# Save current session (context, aliases, settings)
govee> session save my-session

# Load saved session
govee> session load my-session

# List sessions
govee> session list
my-session        (saved: 2025-12-26 10:30:00)
monitoring        (saved: 2025-12-25 15:20:00)

# Delete session
govee> session delete my-session
```

### Notifications

Get notified about important events:

```bash
# Enable notification for device offline events
govee> notify on device_offline

# Enable rate limit notifications
govee> notify on rate_limit_triggered

# Disable notifications
govee> notify off device_discovered

# List active notifications
govee> notify list
✓ device_offline
✓ rate_limit_triggered
✗ device_discovered
```

### Debugging Tools

Advanced debugging and troubleshooting:

```bash
# Debug ArtNet channels
govee> debug artnet --universe 0 --channel 1-10

# Debug specific device
govee> debug device AA:BB:CC:DD:EE:FF
Device: AA:BB:CC:DD:EE:FF
  IP: 192.168.1.10
  Model: H6160
  Status: online
  Last seen: 5s ago
  Queue depth: 2
  Send rate: 8.5 msg/s
  Failures: 0

# Debug queue
govee> debug queue

# Enable request tracing
govee> trace on
govee> devices list
[TRACE] GET /devices -> 200 OK (45ms)
govee> trace off

# Benchmark device commands
govee> benchmark device AA:BB:CC:DD:EE:FF --count 100
Sent 100 commands
Avg latency: 23.5ms
Min latency: 18.2ms
Max latency: 45.1ms
Success rate: 100%
```

### Shell Settings

Customize shell behavior:

```bash
# Change prompt
govee> set prompt "bridge> "
bridge>

# Enable colored output
govee> set color on

# Show timestamps in output
govee> set timestamps on

# Enable paging for long output
govee> set paging on

# Set default output format
govee> set output-format table

# View all settings
govee> settings show

# Save settings to file
govee> settings save

# Load settings from file
govee> settings load
```

## Configuration

### Shell Configuration File

The shell can be configured via `~/.govee_shell_config`:

```toml
[shell]
# Command history
history_file = "~/.govee_shell_history"
history_size = 1000

# UI settings
enable_autocomplete = true
enable_colors = true
default_output_format = "table"
prompt = "govee> "

# Behavior
enable_paging = true
page_size = 50
show_timestamps = false

[connection]
# Default server
default_server_url = "http://127.0.0.1:8000"

# Authentication (can also use environment variables)
# api_key = "your-api-key"
# api_bearer_token = "your-token"

[notifications]
# Enable notifications by default
device_offline = true
rate_limit_triggered = true
device_discovered = false
```

### Environment Variables

All CLI environment variables work in shell mode:

```bash
export GOVEE_ARTNET_SERVER_URL=http://192.168.1.100:8000
export GOVEE_ARTNET_API_KEY=your-api-key
export GOVEE_ARTNET_OUTPUT=json

govee-artnet shell
```

## Examples

### Example 1: Initial Setup

```bash
# Start shell
$ govee-artnet shell

# Connect and discover devices
govee> connect
Connected to http://127.0.0.1:8000

govee> devices list
┌──────────────────┬───────────────┬────────┬─────────┬─────────┐
│ ID               │ IP            │ Model  │ Enabled │ Status  │
├──────────────────┼───────────────┼────────┼─────────┼─────────┤
│ AA:BB:CC:DD:EE:FF│ 192.168.1.10  │ H6160  │ ✓       │ Online  │
│ AA:BB:CC:DD:EE:01│ 192.168.1.11  │ H6163  │ ✓       │ Online  │
└──────────────────┴───────────────┴────────┴─────────┴─────────┘

# Create mappings
govee> mappings create --device-id AA:BB:CC:DD:EE:FF --universe 0 --start-channel 1 --template rgb
Mapping created (ID: 1)

# Test device
govee> devices command AA:BB:CC:DD:EE:FF --on --brightness 255 --color ff0000
Command queued
```

### Example 2: Troubleshooting

```bash
# Check logs for errors
govee> logs --level ERROR --lines 100

# Search for specific issue
govee> logs search "timeout" --regex

# Monitor real-time logs
govee> logs tail --level WARNING

# Check system health
govee> monitor health

# Debug specific device
govee> debug device AA:BB:CC:DD:EE:FF
```

### Example 3: Monitoring Session

```bash
# Set up monitoring aliases
govee> alias mon-dash "monitor dashboard"
govee> alias mon-artnet "monitor artnet"
govee> alias mon-logs "logs tail --level INFO"

# Save session
govee> session save monitoring

# Later: restore session
govee> session load monitoring
govee> mon-dash
```

### Example 4: Batch Device Control

```bash
# Create bookmarks for groups
govee> bookmark add living-room-1 --device-id AA:BB:CC:DD:EE:FF
govee> bookmark add living-room-2 --device-id AA:BB:CC:DD:EE:01

# Turn on all devices
govee> devices command @all --on

# Set all to same color
govee> devices command @all --brightness 200 --color 00ff00

# Turn off specific rooms
govee> devices command @living-room-1 --off
govee> devices command @living-room-2 --off
```

### Example 5: Automated Monitoring Script

Create `monitor.govee`:
```bash
connect
output --format table
devices list
echo "=== Device Status ==="
logs --level ERROR --lines 20
echo "=== Recent Errors ==="
stats
echo "=== Statistics ==="
health
echo "=== Health Check ==="
```

Run it:
```bash
$ govee-artnet shell --script monitor.govee
```

## Tips and Tricks

1. **Quick Help**: Type `help` or `help <command>` for command-specific help

2. **Keyboard Shortcuts**:
   - `Ctrl+C`: Stop current operation
   - `Ctrl+D`: Exit shell
   - `Ctrl+L`: Clear screen
   - `Ctrl+R`: Reverse search history
   - `Tab`: Autocomplete
   - `Up/Down`: Navigate history

3. **Paging**: For long output, enable paging: `set paging on`

4. **Save Time**: Use aliases and bookmarks for frequently used commands

5. **Monitoring**: Leave `logs tail` or `monitor dashboard` running in a separate terminal

6. **Scripting**: Automate repetitive tasks with script files

7. **Context**: Set context for device and universe to avoid repetitive typing

8. **Export**: Export logs before they rotate out of the buffer

9. **Notifications**: Enable notifications for important events

10. **Sessions**: Save your setup with `session save` for quick restoration

## Troubleshooting

### Shell Won't Connect

```bash
govee> connect
Error: Connection refused

# Check if bridge is running
$ systemctl status govee-artnet-bridge

# Try explicit URL
govee> connect --server-url http://127.0.0.1:8000
```

### No Logs Appearing

```bash
# Check if log buffer is enabled in bridge config
# Verify with:
govee> config show

# If disabled, enable in bridge config.toml:
[logging]
buffer_enabled = true
buffer_size = 10000
```

### Autocomplete Not Working

```bash
# Enable autocomplete in settings
govee> set autocomplete on

# Or in ~/.govee_shell_config:
[shell]
enable_autocomplete = true
```

### Commands Running Slow

```bash
# Check connection
govee> status

# Check server health
govee> health

# Check network latency
govee> benchmark device AA:BB:CC:DD:EE:FF --count 10
```

## See Also

- [Main README](README.md) - General bridge documentation
- [USAGE.md](USAGE.md) - Detailed usage guide
- [INSTALL.md](INSTALL.md) - Installation instructions
- [CLI_SHELL_EXPANSION_PLAN.md](CLI_SHELL_EXPANSION_PLAN.md) - Technical implementation plan

## Contributing

Found a bug or have a feature request for the shell? Please open an issue on GitHub!

## License

See [LICENSE](LICENSE) file for details.
