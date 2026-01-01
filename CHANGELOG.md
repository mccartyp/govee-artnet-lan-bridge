# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] - 2026-01-01

### Added
- Debian package (.deb) build system for Ubuntu 24.04 and Debian 13
- GitHub Actions workflow for building and testing Debian packages
- Comprehensive configuration documentation (govee-bridge.toml.example)
- Automated pytest testing in package installation workflow
- Systemd service integration testing in CI
- Installation badges in README and INSTALL.md

### Changed
- Lowered dependency requirements to match Ubuntu 24.04 LTS packages
  - FastAPI: 0.110.0 → 0.101.0
  - httpx: 0.27.0 → 0.26.0
- Enhanced configuration file with extensive inline documentation
- Updated INSTALL.md with Debian package installation as primary method
- Improved python-app.yml workflow to use editable install

### Removed
- CAP_NET_BIND_SERVICE capability from systemd service (not needed for unprivileged ports)

## [1.0.1] - 2026-01-01

### Added
- WebSocket event notifications for device and health status changes
- GitHub Actions workflow for Python application
- requirements.txt file for dependency management

### Changed
- Updated API documentation for WebSocket events and mapping_count field

### Fixed
- Fixed failing API tests
- Removed unused global declarations in cli.py

## [1.0.0] - 2024-12-30

### Added
- Initial release
