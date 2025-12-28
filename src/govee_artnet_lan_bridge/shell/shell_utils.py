"""Utility functions for the Govee ArtNet shell.

This module contains standalone utility functions for common operations
like JSON file handling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(file_path: Path, default: Any) -> Any:
    """
    Load JSON data from file with fallback to default.

    Args:
        file_path: Path to the JSON file
        default: Default value to return if file doesn't exist or can't be loaded

    Returns:
        Loaded JSON data or the default value
    """
    try:
        if file_path.exists():
            with open(file_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(file_path: Path, data: Any) -> None:
    """
    Save JSON data to file.

    Args:
        file_path: Path to save the JSON file
        data: Data to serialize as JSON

    Raises:
        Exception: If the file cannot be written
    """
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
