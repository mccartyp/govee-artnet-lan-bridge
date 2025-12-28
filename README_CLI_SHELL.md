# Govee ArtNet CLI Shell Guide

The Govee ArtNet CLI includes a powerful interactive shell mode that provides real-time monitoring, log viewing, and enhanced usability features for managing your bridge.

## Table of Contents

- [Getting Started](#getting-started)
- [Core Features](#core-features)
- [Shell Commands](#shell-commands)
- [Advanced Features](#advanced-features)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Tips and Tricks](#tips-and-tricks)

## Getting Started

### Launching the Shell

```bash
# Start interactive shell
govee-artnet shell

# Or with custom server URL
govee-artnet --server-url http://192.168.1.100:8000 shell
```

### First Steps

```
govee> help            # Show available commands
govee> tips            # Show helpful tips
govee> status          # Check bridge connection status
govee> devices list    # List discovered devices
```

## Core Features

### 📊 Real-time Monitoring

Watch your system in action with live monitoring commands:

```bash
govee> monitor dashboard    # Full system dashboard with live metrics
govee> devices watch        # Watch device state changes in real-time
govee> watch devices list   # Auto-refresh devices list every 2 seconds
```

**Monitor Dashboard** shows:
- Bridge status and uptime
- Device counts (online/offline)
- ArtNet packet statistics
- Queue depth and processing stats
- Rate limiter status

### 📝 Log Viewing & Streaming

View and search logs without leaving the shell:

```bash
govee> logs                      # Show last 50 log lines
govee> logs --lines 200          # Show last 200 lines
govee> logs tail                 # Stream logs in real-time (Ctrl+C to stop)
govee> logs search "discovered"  # Search logs for pattern
```

**Log filtering:**
```bash
govee> logs --level ERROR        # Show only error-level logs
govee> logs --logger discovery   # Show logs from discovery subsystem
```

### ⌨️ Command History & Autocomplete

- **Tab completion** - Press Tab to autocomplete commands
- **History navigation** - Use ↑/↓ arrows to navigate command history
- **Persistent history** - Command history saved to `~/.govee_artnet/shell_history`
- **Reverse search** - Press Ctrl+R to search command history

### 🔖 Bookmarks

Save frequently used device IDs with friendly names:

```bash
govee> bookmark add kitchen "AA:BB:CC:DD:EE:FF"
govee> bookmark add bedroom "11:22:33:44:55:66"
govee> bookmark list

# Use bookmarks in commands
govee> devices enable @kitchen
govee> mappings create --device-id @bedroom --universe 0 --template rgb
```

**Bookmark commands:**
- `bookmark add <name> <value>` - Create a new bookmark
- `bookmark list` - Show all bookmarks
- `bookmark delete <name>` - Remove a bookmark
- `bookmark clear` - Remove all bookmarks

### 🏷️ Aliases

Create shortcuts for frequently used commands:

```bash
govee> alias dl "devices list"
govee> alias ds "devices"
govee> alias ml "mappings list"

# Use aliases
govee> dl           # Executes "devices list"
govee> ds enable @kitchen   # Executes "devices enable @kitchen"
```

**Alias commands:**
- `alias <name> "<command>"` - Create a new alias
- `alias list` - Show all aliases
- `alias delete <name>` - Remove an alias
- `alias clear` - Remove all aliases

## Shell Commands

### Connection Management

```bash
govee> connect              # Connect to the bridge server
govee> disconnect           # Disconnect from server
govee> status              # Show connection status
```

### Device Management

```bash
govee> devices list                           # List all devices (simplified view)
govee> devices list detailed                  # Show detailed device information
govee> devices list --state active            # Filter by state (active, disabled, offline)
govee> devices list --id AA:BB:CC             # Filter by device ID (MAC address)
govee> devices list --ip 192.168.1.100        # Filter by IP address
govee> devices list detailed --state offline  # Detailed view with filters
govee> devices enable <device_id>             # Enable a device
govee> devices disable <device_id>            # Disable a device
govee> devices set-name <device_id> "Name"    # Set device name
govee> devices set-capabilities <device_id> --brightness true --color true  # Set capabilities
govee> devices command <device_id> [options]  # Send control commands
```

#### Device Control Commands

Send control commands to devices directly from the shell:

```bash
# Turn device on/off
govee> devices command AA:BB:CC:DD:EE:FF --on
govee> devices command AA:BB:CC:DD:EE:FF --off

# Set brightness (0-255)
govee> devices command AA:BB:CC:DD:EE:FF --brightness 200

# Set RGB color (hex format)
govee> devices command AA:BB:CC:DD:EE:FF --color #FF00FF
govee> devices command AA:BB:CC:DD:EE:FF --color ff8800
govee> devices command AA:BB:CC:DD:EE:FF --color F0F    # Shorthand expands to FF00FF

# Set color temperature (0-255)
govee> devices command AA:BB:CC:DD:EE:FF --ct 128
govee> devices command AA:BB:CC:DD:EE:FF --kelvin 200  # Same as --ct

# Combine multiple commands
govee> devices command AA:BB:CC:DD:EE:FF --on --brightness 200 --color #FF00FF
govee> devices command AA:BB:CC:DD:EE:FF --color ff8800 --brightness 128

# Use bookmarks for convenience
govee> bookmark add kitchen "AA:BB:CC:DD:EE:FF"
govee> devices command @kitchen --on --color #00FF00
```

### Mapping Management

```bash
govee> mappings list                          # List all mappings
govee> mappings get <id>                      # Get mapping details
govee> mappings delete <id>                   # Delete a mapping
govee> mappings channel-map                   # Show channel map
```

### Monitoring Commands

```bash
govee> monitor dashboard                      # Full system dashboard
govee> logs                                   # View recent logs
govee> logs tail                              # Tail logs in real-time
govee> logs search <pattern>                  # Search logs
```

### Output Control

```bash
govee> output --format json    # Switch to JSON output
govee> output --format yaml    # Switch to YAML output
govee> output --format table   # Switch to table output (default)
```

### Shell Utilities

```bash
govee> help                    # Show all commands
govee> help <command>          # Show help for specific command
govee> version                 # Show shell version
govee> tips                    # Show helpful tips
govee> clear                   # Clear the screen
govee> exit                    # Exit the shell (or Ctrl+D)
```

## Advanced Features

### 🎬 Batch Execution

Execute multiple commands from a file:

```bash
# Create a script file
$ cat > setup.govee <<EOF
connect
devices list
logs --lines 50
monitor dashboard
