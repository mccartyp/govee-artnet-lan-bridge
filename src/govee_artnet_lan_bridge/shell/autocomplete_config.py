"""Autocomplete configuration for the Govee ArtNet shell.

This module defines the command structure for multi-level autocomplete
in the interactive shell interface.
"""

from __future__ import annotations


def get_completer_dict() -> dict:
    """
    Get the autocomplete command structure dictionary.

    Returns:
        Dictionary defining the nested command structure for autocomplete
    """
    return {
        'connect': None,
        'disconnect': None,
        'status': None,
        'health': {'detailed': None},
        'devices': {
            'list': {
                'detailed': None,
                '--id': None,
                '--ip': None,
                '--state': {'active': None, 'disabled': None, 'offline': None}
            },
            'enable': None,
            'disable': None,
            'set-name': None,
            'set-capabilities': {
                '--brightness': None,
                '--color': None,
                '--color-temp': None
            },
            'command': {
                '--on': None,
                '--off': None,
                '--brightness': None,
                '--color': None,
                '--ct': None,
                '--kelvin': None
            }
        },
        'mappings': {
            'list': None,
            'get': None,
            'create': {
                '--device-id': None,
                '--universe': None,
                '--template': {'RGB': None, 'RGBCT': None, 'DimRGBCT': None, 'DimCT': None},
                '--start-channel': None,
                '--channel': None,
                '--length': None,
                '--type': {'range': None, 'discrete': None},
                '--field': {'power': None, 'brightness': None, 'r': None, 'red': None, 'g': None, 'green': None, 'b': None, 'blue': None, 'ct': None, 'color_temp': None},
                '--allow-overlap': None,
                '--help': None,
            },
            'delete': None,
            'channel-map': None
        },
        'channels': {'list': None},
        'logs': {
            'stats': None,
            'tail': {
                '--level': None,
                '--logger': None,
            },
            'search': {
                '--regex': None,
                '--case-sensitive': None,
                '--lines': None,
            },
        },
        'monitor': {'status': None, 'dashboard': None},
        'output': {'json': None, 'table': None, 'yaml': None},
        'bookmark': {'add': None, 'list': None, 'delete': None, 'use': None},
        'alias': {'add': None, 'list': None, 'delete': None, 'clear': None},
        'watch': {
            'devices': {'--interval': None},
            'mappings': {'--interval': None},
            'logs': {'--interval': None},
            'dashboard': {'--interval': None},
        },
        'batch': {'load': None},
        'session': {'save': None, 'list': None, 'delete': None},
        'help': None,
        '?': None,
        'version': None,
        'tips': None,
        'clear': None,
        'exit': None,
        'quit': None,
    }
