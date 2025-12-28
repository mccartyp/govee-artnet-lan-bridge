"""UI layout builder for the Govee ArtNet shell.

This module handles the construction of the prompt_toolkit UI layout,
including windows, containers, and the application instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl

from .ui_components import ANSILexer, TOOLBAR_STYLE

if TYPE_CHECKING:
    from .core import GoveeShell


class LayoutBuilder:
    """Builds the prompt_toolkit UI layout for the shell."""

    def __init__(self, shell: GoveeShell):
        """
        Initialize the layout builder.

        Args:
            shell: Reference to the GoveeShell instance
        """
        self.shell = shell

    def build_layout_and_app(self, key_bindings: KeyBindings) -> Application:
        """
        Build the complete UI layout and application.

        Args:
            key_bindings: Configured key bindings for the application

        Returns:
            Configured Application instance
        """
        # Create conditional containers for switching between normal and log tail views
        self.shell.normal_output_window = Window(
            content=BufferControl(
                buffer=self.shell.output_buffer,
                lexer=ANSILexer(),
                focusable=False,  # Keep focus on input for typing
            ),
            wrap_lines=False,
        )

        self.shell.log_tail_window = Window(
            content=BufferControl(
                buffer=self.shell.log_tail_buffer,
                lexer=ANSILexer(),
                focusable=False,  # Keep focus on input for typing
            ),
            wrap_lines=False,
        )

        self.shell.watch_window = Window(
            content=BufferControl(
                buffer=self.shell.watch_buffer,
                lexer=ANSILexer(),
                focusable=False,  # Keep focus on input for typing
            ),
            wrap_lines=False,
        )

        # Build root container with all UI elements
        self.shell.root_container = HSplit([
            # Conditionally show normal output, log tail, or watch based on mode
            ConditionalContainer(
                content=self.shell.normal_output_window,
                filter=Condition(lambda: not self.shell.in_log_tail_mode and not self.shell.in_watch_mode),
            ),
            ConditionalContainer(
                content=self.shell.log_tail_window,
                filter=Condition(lambda: self.shell.in_log_tail_mode),
            ),
            ConditionalContainer(
                content=self.shell.watch_window,
                filter=Condition(lambda: self.shell.in_watch_mode),
            ),
            Window(height=1, char='─'),
            # Hide input in log tail or watch mode, show in normal mode
            ConditionalContainer(
                content=Window(
                    content=BufferControl(
                        buffer=self.shell.input_buffer,
                        input_processors=[],
                    ),
                    height=1,
                    get_line_prefix=lambda line_number, wrap_count: f"{self.shell.prompt}",
                ),
                filter=Condition(lambda: not self.shell.in_log_tail_mode and not self.shell.in_watch_mode),
            ),
            # Show log tail prompt in log tail mode
            ConditionalContainer(
                content=Window(
                    height=1,
                    content=FormattedTextControl(
                        text=lambda: "[Log Tail Mode - Press Esc/q to exit, End to jump to bottom, f for filters]"
                    ),
                ),
                filter=Condition(lambda: self.shell.in_log_tail_mode),
            ),
            # Show watch prompt in watch mode
            ConditionalContainer(
                content=Window(
                    height=1,
                    content=FormattedTextControl(
                        text=lambda: f"[Watch Mode - {self.shell.watch_controller.watch_target if self.shell.watch_controller and self.shell.watch_controller.watch_target else 'N/A'} - Press Esc/q to exit, +/- to adjust interval]"
                    ),
                ),
                filter=Condition(lambda: self.shell.in_watch_mode),
            ),
            Window(height=1, char='─'),
            Window(
                content=FormattedTextControl(
                    text=self.shell._get_bottom_toolbar,
                ),
                height=3,
                style="class:bottom-toolbar",
            ),
        ])

        # Create and return Application
        return Application(
            layout=Layout(self.shell.root_container),
            key_bindings=key_bindings,
            style=TOOLBAR_STYLE,
            full_screen=True,
            mouse_support=True,
        )
