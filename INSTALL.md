# Installation guide

This repository ships helper assets for running the Govee Artnet LAN bridge with systemd. Use the provided installer script or Make targets to install the Python package, stage configuration files, and register the correct unit for either a system-wide or per-user deployment.

## Requirements

- Python 3.10+
- `pip` and `systemctl`
- systemd with capability support (for optional `cap_net_bind_service`)

## System service (`govee-bridge.service`)

The system unit runs as the dedicated `govee-bridge` user, stores configuration in `/etc/govee-bridge`, and keeps its SQLite database in `/var/lib/govee-bridge/bridge.sqlite3`.

Install and start:

```bash
make install-system          # uses python3 by default
# or with explicit options:
# START=0 SETCAP=1 PYTHON=/usr/bin/python3.11 make install-system
```

What the installer does:

- Installs the Python package globally via `pip`.
- Creates the `govee-bridge` system user (home `/var/lib/govee-bridge`, shell `/usr/sbin/nologin`).
- Ensures `/etc/govee-bridge/config.toml` and `/var/lib/govee-bridge/` exist (preserving an existing config).
- Installs `packaging/systemd/govee-bridge.service`, reloads systemd, and enables/starts the service (unless `START=0`).
- Optionally applies `cap_net_bind_service` to the `govee-artnet-bridge` entrypoint when `SETCAP=1`.

Service highlights:

- `Restart=on-failure` with a short delay.
- `AmbientCapabilities=CAP_NET_BIND_SERVICE` / `CapabilityBoundingSet=CAP_NET_BIND_SERVICE` to permit binding UDP 6454 without root.
- State and configuration directories are managed via `StateDirectory`/`ConfigurationDirectory`.
- Uncomment `Type=notify` and `NotifyAccess=main` in the unit file if you wrap the process with `systemd-notify` for sd_notify readiness semantics.

Uninstall (leaves config/data intact):

```bash
make uninstall-system
```

## User service (`govee-bridge-user.service`)

The user unit uses XDG paths:

- Config: `${XDG_CONFIG_HOME:-$HOME/.config}/govee-bridge/config.toml`
- Data: `${XDG_DATA_HOME:-$HOME/.local/share}/govee-artnet-lan-bridge/bridge.sqlite3`

Install and start:

```bash
make install-user          # installs with pip --user and enables/starts the unit
# START=0 PYTHON=/usr/bin/python3.11 make install-user
```

What the installer does:

- Installs the Python package for the current user (`pip --user`).
- Writes a user-specific config template (preserving existing files) pointing at the XDG data path.
- Installs `packaging/systemd/govee-bridge-user.service`, reloads the user daemon, and enables/starts it (unless `START=0`).

Start at boot by enabling linger for the account:

```bash
sudo loginctl enable-linger "$USER"
```

Uninstall (keeps config/data):

```bash
make uninstall-user
```

## Unit selection

- Use `govee-bridge.service` for multi-user hosts or where a dedicated service account is required.
- Use `govee-bridge-user.service` for per-user installs without root privileges.

Both units expose commented `Type=notify`/`NotifyAccess=main` lines for optional sd_notify readiness signaling. Leave them commented unless the bridge is wrapped with `systemd-notify` or gains native sd_notify support.
