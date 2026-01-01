# Installation Guide

[![Latest Release](https://img.shields.io/github/v/release/mccartyp/govee-artnet-lan-bridge)](https://github.com/mccartyp/govee-artnet-lan-bridge/releases/latest)
[![Download DEB](https://img.shields.io/badge/download-.deb-blue)](https://github.com/mccartyp/govee-artnet-lan-bridge/releases/latest)
[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

This repository provides multiple installation methods for the Govee ArtNet LAN bridge. The Debian package (.deb) method is recommended for Ubuntu 24.04 and Debian 13 systems.

---

## Requirements

- **Python**: 3.10 or higher
- **Operating System**: Ubuntu 24.04 LTS, Debian 13 (Trixie), or compatible Linux distribution
- **systemd**: For service management
- **Network**: Local network access to Govee devices

---

## Method 1: Install from Debian Package (Recommended)

**Supported Distributions**: Ubuntu 24.04 LTS, Debian 13 (Trixie)

This is the easiest and recommended installation method for supported distributions. All dependencies are automatically installed from apt repositories.

### 1. Download the .deb Package

Download the latest release from GitHub:

```bash
# Visit https://github.com/mccartyp/govee-artnet-lan-bridge/releases/latest
# Or download latest version dynamically:
LATEST_VERSION=$(curl -s https://api.github.com/repos/mccartyp/govee-artnet-lan-bridge/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
wget https://github.com/mccartyp/govee-artnet-lan-bridge/releases/download/${LATEST_VERSION}/govee-artnet-bridge_${LATEST_VERSION#v}_all.deb
```

### 2. Install the Package

```bash
sudo dpkg -i govee-artnet-bridge_*.deb
```

If there are missing dependencies, install them with:

```bash
sudo apt-get install -f
```

### 3. Verify Installation

The service is automatically started after installation:

```bash
# Check service status
sudo systemctl status govee-bridge.service

# View service logs
sudo journalctl -u govee-bridge.service -f
```

### 4. Configure (Optional)

Edit the configuration file:

```bash
sudo nano /etc/govee-bridge/config.toml
```

After making changes, reload the configuration:

```bash
sudo systemctl reload govee-bridge.service
```

For detailed configuration options, see:
- `/usr/share/doc/govee-artnet-bridge/govee-bridge.toml.example`

### What Gets Installed

- **Binaries**: `/usr/bin/govee-artnet-bridge`, `/usr/bin/govee-artnet-cli`
- **Python Package**: `/usr/lib/python3/dist-packages/govee_artnet_lan_bridge/`
- **Systemd Service**: `/lib/systemd/system/govee-bridge.service`
- **Configuration**: `/etc/govee-bridge/config.toml`
- **Data Directory**: `/var/lib/govee-bridge/`
- **Documentation**: `/usr/share/doc/govee-artnet-bridge/`

### Uninstall

Remove the package (keeps configuration and data):

```bash
sudo apt remove govee-artnet-bridge
```

Completely remove including configuration and data (purge):

```bash
sudo apt purge govee-artnet-bridge
```

---

## Method 2: Build Debian Package from Source

If you want to build the .deb package yourself:

```bash
# Clone repository
git clone https://github.com/mccartyp/govee-artnet-lan-bridge.git
cd govee-artnet-lan-bridge

# Build .deb package
make deb

# Install
sudo dpkg -i dist/govee-artnet-bridge_*.deb
```

---

## Method 3: System Service (install.sh script)

This method uses the included installer script to set up a system-wide service.

### Install

```bash
# Clone repository
git clone https://github.com/mccartyp/govee-artnet-lan-bridge.git
cd govee-artnet-lan-bridge

# Install as system service
make install-system
```

The installer will:
- Install the Python package globally via `pip`
- Create the `govee-bridge` system user
- Set up `/etc/govee-bridge/config.toml` and `/var/lib/govee-bridge/`
- Install and start the systemd service

### Service Management

```bash
# Check status
sudo systemctl status govee-bridge.service

# Restart service
sudo systemctl restart govee-bridge.service

# View logs
sudo journalctl -u govee-bridge.service -f

# Reload configuration
sudo systemctl reload govee-bridge.service
```

### Uninstall

```bash
make uninstall-system
```

**Note**: Configuration and data are preserved. Manually remove `/etc/govee-bridge` and `/var/lib/govee-bridge` if needed.

---

## Method 4: User Service (install.sh script)

For per-user installation without root privileges:

### Install

```bash
git clone https://github.com/mccartyp/govee-artnet-lan-bridge.git
cd govee-artnet-lan-bridge

make install-user
```

### Configuration and Data Locations

- **Config**: `~/.config/govee-bridge/config.toml`
- **Data**: `~/.local/share/govee-artnet-lan-bridge/`
- **Service**: `~/.config/systemd/user/govee-bridge-user.service`

### Service Management

```bash
# Check status
systemctl --user status govee-bridge-user.service

# Restart
systemctl --user restart govee-bridge-user.service

# View logs
journalctl --user -u govee-bridge-user.service -f
```

### Start at Boot

Enable linger for your user account:

```bash
sudo loginctl enable-linger "$USER"
```

### Uninstall

```bash
make uninstall-user
```

---

## Method 5: Manual pip Installation

For other distributions (including Ubuntu 22.04) or development:

### Install

```bash
# Clone repository
git clone https://github.com/mccartyp/govee-artnet-lan-bridge.git
cd govee-artnet-lan-bridge

# Install dependencies
python3 -m pip install --upgrade pip

# Install package in development mode
pip3 install -e .
```

### Run Manually

```bash
# Run bridge server
govee-artnet-bridge --config config.toml

# Or use the CLI
govee-artnet-cli --help
```

### Uninstall

```bash
pip3 uninstall govee-artnet-lan-bridge
```

---

## Ubuntu 22.04 Support

Ubuntu 22.04 **does not have recent enough package versions** in its apt repositories to satisfy dependencies via .deb installation. For Ubuntu 22.04, use **Method 5 (Manual pip Installation)** instead.

Required package versions not available in Ubuntu 22.04:
- `python3-fastapi` (need ≥0.101.0, has 0.63.0)
- `python3-rich` (not available)
- `python3-pytest` (need ≥8.3.0, has 6.2.5)

With pip, you can install the latest versions manually:

```bash
pip3 install --break-system-packages \\
  fastapi>=0.101.0 \\
  httpx>=0.26.0 \\
  uvicorn>=0.23.0 \\
  prometheus-client>=0.20.0 \\
  PyYAML>=6.0.0 \\
  rich>=13.0.0 \\
  pytest>=8.3.0 \\
  pytest-asyncio>=0.23.0
```

Then follow Method 3 or 5 for installation.

---

## Post-Installation

### Access the API

The REST API runs on port 8000 by default:

```bash
# Check health
curl http://localhost:8000/health

# List devices
curl http://localhost:8000/devices

# API documentation (if enabled)
firefox http://localhost:8000/docs
```

### Device Discovery

Devices are automatically discovered via multicast. Check logs to see discovered devices:

```bash
sudo journalctl -u govee-bridge.service | grep discovered
```

### Configuration Examples

See `/usr/share/doc/govee-artnet-bridge/govee-bridge.toml.example` for comprehensive configuration documentation.

---

## Next Steps

After installation:

1. **Verify service is running**: `sudo systemctl status govee-bridge.service`
2. **Check devices are discovered**: `govee-artnet-cli` or check API
3. **Configure ArtNet mapping**: See main [README.md](README.md) for usage
4. **Set up authentication**: Edit `/etc/govee-bridge/config.toml` to add `api_key`

For usage instructions and ArtNet mapping examples, see the main [README.md](README.md).
