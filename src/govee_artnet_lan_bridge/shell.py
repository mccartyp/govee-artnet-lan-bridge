"""Interactive shell for govee-artnet CLI.

BACKWARD COMPATIBILITY WRAPPER
================================

This module has been refactored into a package structure for better maintainability.
All functionality is now organized in the 'shell' package:

- shell/core.py: Main GoveeShell class and command loop
- shell/ui_components.py: UI components (completer, cache, lexer)
- shell/controllers.py: Real-time controllers (log tail, watch)
- shell/command_handlers/: Command handlers organized by domain
  - devices.py: Device management commands
  - mappings.py: Mapping management commands
  - monitoring.py: Logging and monitoring commands
  - config.py: Configuration and session management

This file now serves as a backward compatibility wrapper to avoid breaking
existing imports. All symbols are re-exported from the shell package.
"""

# Re-export everything from the shell package for backward compatibility
from .shell import (
    GoveeShell,
    run_shell,
    SHELL_VERSION,
)

# Re-export components for any code that might import them directly
from .shell.ui_components import (
    TrailingSpaceCompleter,
    ResponseCache,
    ANSILexer,
    TOOLBAR_STYLE,
    DEFAULT_CACHE_TTL,
    FIELD_DESCRIPTIONS,
)

from .shell.controllers import (
    ConnectionState,
    LogTailController,
    WatchController,
)

__all__ = [
    'GoveeShell',
    'run_shell',
    'SHELL_VERSION',
    'TrailingSpaceCompleter',
    'ResponseCache',
    'ANSILexer',
    'TOOLBAR_STYLE',
    'DEFAULT_CACHE_TTL',
    'FIELD_DESCRIPTIONS',
    'ConnectionState',
    'LogTailController',
    'WatchController',
]
