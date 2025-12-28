"""Toolbar management for the Govee ArtNet shell.

This module handles the bottom toolbar display, including status updates
and formatting of the two-line toolbar with device counts and health info.
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING

from .controllers import ConnectionState

if TYPE_CHECKING:
    from .core import GoveeShell


class ToolbarManager:
    """Manages the bottom toolbar display and status updates."""

    def __init__(self, shell: GoveeShell):
        """
        Initialize the toolbar manager.

        Args:
            shell: Reference to the GoveeShell instance
        """
        self.shell = shell

        # Toolbar status tracking (updated periodically)
        self.status = {
            "active_devices": 0,
            "unconfigured_devices": 0,
            "offline_devices": 0,
            "health_status": "unknown",
            "last_update": None,
        }

    def update_status(self) -> None:
        """Update toolbar status information from bridge API."""
        if not self.shell.client:
            return

        try:
            # Fetch health status
            health_response = self.shell.client.get("/health", timeout=1.0)
            if health_response.status_code == 200:
                health_data = health_response.json()
                self.status["health_status"] = health_data.get("status", "unknown")

            # Fetch device counts
            devices_response = self.shell.client.get("/devices", timeout=1.0)
            if devices_response.status_code == 200:
                devices = devices_response.json()
                if isinstance(devices, list):
                    # Active: online (not offline), configured, and enabled
                    active = sum(
                        1 for d in devices
                        if d.get("enabled") and d.get("configured") and not d.get("offline")
                    )
                    # Unconfigured: online (not offline) but not configured (enabled doesn't matter for visibility)
                    unconfigured = sum(
                        1 for d in devices
                        if not d.get("configured") and not d.get("offline")
                    )
                    # Offline: offline and enabled
                    offline = sum(
                        1 for d in devices
                        if d.get("enabled") and d.get("offline")
                    )

                    self.status["active_devices"] = active
                    self.status["unconfigured_devices"] = unconfigured
                    self.status["offline_devices"] = offline

            self.status["last_update"] = time.time()
        except Exception:
            # Silently ignore errors - toolbar is non-critical
            pass

    def get_toolbar_fragments(self) -> list[tuple[str, str]]:
        """
        Get formatted toolbar fragments for display.

        Returns:
            List of (style, text) tuples for prompt_toolkit formatted text
        """
        try:
            from prompt_toolkit.utils import get_cwidth
        except Exception:  # pragma: no cover
            def get_cwidth(s: str) -> int:
                return len(s)

        width = shutil.get_terminal_size(fallback=(80, 24)).columns

        # Update status if stale
        if (
            self.status["last_update"] is None
            or time.time() - self.status["last_update"] > 5
        ):
            self.update_status()

        BASE = "class:bottom-toolbar"

        def S(cls: str) -> str:
            """Apply style class with base toolbar class."""
            return f"{BASE} class:{cls}"

        def fit_line(fragments: list[tuple[str, str]], target_width: int) -> list[tuple[str, str]]:
            """Fit line to terminal width with ellipsis if needed."""
            out: list[tuple[str, str]] = []
            used = 0

            def add(style: str, text: str) -> None:
                nonlocal used
                if not text or used >= target_width:
                    return
                remaining = target_width - used
                w = get_cwidth(text)
                if w <= remaining:
                    out.append((style, text))
                    used += w
                    return

                ell = "…"
                ell_w = get_cwidth(ell)
                keep = remaining - ell_w if remaining > ell_w else remaining

                t = text
                while t and get_cwidth(t) > keep:
                    t = t[:-1]

                if keep > 0 and remaining > ell_w:
                    out.append((style, t + ell))
                elif keep > 0:
                    out.append((style, t))
                used = target_width

            for s, t in fragments:
                add(s, t)

            if used < target_width:
                out.append((S("toolbar"), " " * (target_width - used)))

            return out

        parts: list[tuple[str, str]] = []

        # Border line
        parts.append((S("toolbar-border"), "─" * width + "\n"))

        # Line 1: Connection + devices
        line1: list[tuple[str, str]] = []
        if self.shell.client:
            line1.append((S("status-connected"), "● Connected"))
        else:
            line1.append((S("status-disconnected"), "○ Disconnected"))

        line1.extend([
            (S("toolbar-info"), " │ Devices: "),
            (S("toolbar-info"), "Active "),
            (S("device-active"), str(self.status["active_devices"])),
            (S("toolbar-info"), " | Unconfigured "),
            (S("device-unconfigured"), str(self.status["unconfigured_devices"])),
            (S("toolbar-info"), " | Offline "),
            (S("device-offline"), str(self.status["offline_devices"])),
        ])

        parts.extend(fit_line(line1, width))
        parts.append((S("toolbar"), "\n"))

        # Line 2: Health + server + updated (or log tail status if in log tail mode)
        line2: list[tuple[str, str]] = []

        if self.shell.in_log_tail_mode and self.shell.log_tail_controller:
            # Show log tail status instead
            state = self.shell.log_tail_controller.state
            if state == ConnectionState.CONNECTED:
                state_style, state_icon = S("status-connected"), "● "
                state_text = "Connected"
            elif state == ConnectionState.CONNECTING:
                state_style, state_icon = S("toolbar-info"), "○ "
                state_text = "Connecting..."
            elif state == ConnectionState.RECONNECTING:
                state_style, state_icon = S("status-degraded"), "◐ "
                state_text = "Reconnecting..."
            else:
                state_style, state_icon = S("status-disconnected"), "○ "
                state_text = "Disconnected"

            line2.append((S("toolbar-info"), "Log Tail: "))
            line2.append((state_style, f"{state_icon}{state_text}"))

            # Show active filters
            if self.shell.log_tail_controller.level_filter or self.shell.log_tail_controller.logger_filter:
                line2.append((S("toolbar-info"), " │ Filters: "))
                if self.shell.log_tail_controller.level_filter:
                    line2.append((S("toolbar-info"), f"Level={self.shell.log_tail_controller.level_filter}"))
                if self.shell.log_tail_controller.logger_filter:
                    if self.shell.log_tail_controller.level_filter:
                        line2.append((S("toolbar-info"), ", "))
                    line2.append((S("toolbar-info"), f"Logger={self.shell.log_tail_controller.logger_filter}"))
            else:
                line2.append((S("toolbar-info"), " │ Filters: None"))

            # Show follow-tail status
            follow_status = "ON" if self.shell.log_tail_controller.follow_tail else "OFF"
            follow_style = S("status-healthy") if self.shell.log_tail_controller.follow_tail else S("status-degraded")
            line2.append((S("toolbar-info"), " │ Follow: "))
            line2.append((follow_style, follow_status))
        else:
            # Normal status line
            health = self.status["health_status"]
            if health == "ok":
                h_style, h_icon = S("status-healthy"), "✓"
            elif health == "degraded":
                h_style, h_icon = S("status-degraded"), "⚠"
            else:
                h_style, h_icon = S("toolbar-info"), "?"

            last_update = self.status["last_update"]
            age_txt = f"{int(time.time() - last_update)}s ago" if last_update else "n/a"

            line2 = [
                (S("toolbar-info"), "Health: "),
                (h_style, f"{h_icon} {health}"),
                (S("toolbar-info"), " │ Server: "),
                (S("toolbar-info"), self.shell.config.server_url),
                (S("toolbar-info"), " │ Updated: "),
                (S("toolbar-info"), age_txt),
            ]

        parts.extend(fit_line(line2, width))
        return parts
