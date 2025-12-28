"""UI components for the Govee ArtNet shell.

This module contains UI-related classes for the interactive shell:
- TrailingSpaceCompleter: Custom autocomplete with trailing spaces
- ResponseCache: API response caching with TTL
- ANSILexer: Terminal color code handling
"""

from __future__ import annotations

import time
from typing import Any, Optional

from prompt_toolkit.completion import Completion, Completer, NestedCompleter
from prompt_toolkit.document import Document, Document as PTDocument
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style


# Cache configuration
DEFAULT_CACHE_TTL = 5.0  # Default cache TTL in seconds

# Field name to description mapping for pretty printing
FIELD_DESCRIPTIONS = {
    "r": "Red",
    "g": "Green",
    "b": "Blue",
    "w": "White",
    "brightness": "Brightness",
    "ct": "Color Temp",
}

# Toolbar styling for prompt_toolkit
TOOLBAR_STYLE = Style.from_dict({
    "bottom-toolbar": "fg:#d8dee9 bg:ansibrightblack noreverse",

    "toolbar": "fg:#d8dee9 bg:ansibrightblack",
    "toolbar-border": "fg:ansiwhite bg:ansibrightblack",
    "toolbar-info": "fg:#d8dee9 bg:ansibrightblack",

    "status-connected": "fg:ansigreen bold bg:ansibrightblack",
    "status-disconnected": "fg:ansired bold bg:ansibrightblack",

    "status-healthy": "fg:ansigreen bg:ansibrightblack",
    "status-degraded": "fg:ansiyellow bg:ansibrightblack",

    "device-active": "fg:ansigreen bg:ansibrightblack",
    "device-unconfigured": "fg:ansiyellow bg:ansibrightblack",
    "device-offline": "fg:ansired bg:ansibrightblack",
})


class TrailingSpaceCompleter(Completer):
    """Wrapper completer that adds trailing space to completions with subcommands."""

    def __init__(self, nested_dict: dict):
        """
        Initialize the completer with a nested dictionary.

        Args:
            nested_dict: Dictionary defining the command structure
        """
        self.nested_completer = NestedCompleter.from_nested_dict(nested_dict)
        self.nested_dict = nested_dict

    def get_completions(self, document: PTDocument, complete_event):
        """
        Get completions with trailing spaces for commands with subcommands.

        Args:
            document: Current document
            complete_event: Completion event

        Yields:
            Completion objects with optional trailing spaces
        """
        for completion in self.nested_completer.get_completions(document, complete_event):
            # Check if this completion has subcommands by traversing the nested dict
            words = document.text.split()
            current_level = self.nested_dict

            # Navigate to the current level in the nested dict
            for word in words[:-1]:
                if word in current_level:
                    current_level = current_level[word]
                    if current_level is None:
                        break
                else:
                    break

            # Check if the completed word has subcommands
            completed_word = completion.text
            has_subcommands = False
            if isinstance(current_level, dict) and completed_word in current_level:
                has_subcommands = current_level[completed_word] is not None and isinstance(current_level[completed_word], dict)

            # Add trailing space if has subcommands
            if has_subcommands:
                yield Completion(
                    text=completion.text + ' ',
                    start_position=completion.start_position,
                    display=completion.display,
                    display_meta=completion.display_meta,
                )
            else:
                yield completion


class ResponseCache:
    """Simple response cache with TTL support."""

    def __init__(self, default_ttl: float = DEFAULT_CACHE_TTL):
        """
        Initialize the cache.

        Args:
            default_ttl: Default time-to-live for cache entries in seconds
        """
        self.default_ttl = default_ttl
        self.cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self.stats = {"hits": 0, "misses": 0, "size": 0}

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        if key not in self.cache:
            self.stats["misses"] += 1
            return None

        value, expiry = self.cache[key]
        if time.time() > expiry:
            # Expired, remove from cache
            del self.cache[key]
            self.stats["size"] = len(self.cache)
            self.stats["misses"] += 1
            return None

        self.stats["hits"] += 1
        return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        expiry = time.time() + (ttl if ttl is not None else self.default_ttl)
        self.cache[key] = (value, expiry)
        self.stats["size"] = len(self.cache)

    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
        self.stats["size"] = 0

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return self.stats.copy()


class ANSILexer(Lexer):
    """
    Lexer that interprets ANSI escape codes in buffer text.

    This lexer processes the document text and converts ANSI escape
    sequences to prompt_toolkit styled text fragments.
    """

    def lex_document(self, document: Document):
        """
        Lex the document by converting ANSI codes to styled fragments.

        Args:
            document: The document to lex

        Returns:
            A callable that returns styled text tuples for each line
        """
        # Convert ANSI text to formatted text fragments
        # to_formatted_text() converts the ANSI object to a list of (style, text) tuples
        ansi_formatted = to_formatted_text(ANSI(document.text))

        # Split the formatted text by lines
        lines_with_styles = []
        current_line = []

        for style, text in ansi_formatted:
            # Split text by newlines while preserving style
            parts = text.split('\n')
            for i, part in enumerate(parts):
                if i > 0:
                    # New line encountered, save current and start new
                    lines_with_styles.append(current_line)
                    current_line = []
                if part:  # Add non-empty parts
                    current_line.append((style, part))

        # Don't forget the last line
        if current_line:
            lines_with_styles.append(current_line)

        # Return a callable that gives the styled fragments for each line
        def get_line(line_number):
            if line_number < len(lines_with_styles):
                return lines_with_styles[line_number]
            return []

        return get_line
