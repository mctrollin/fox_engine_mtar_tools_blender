"""
Logging utilities for MTAR plugin.

This module provides a centralized logging function that respects
the plugin's logging settings, as well as performance timing utilities
for measuring block execution times.
"""
import time
from typing import Dict

import bpy


def log_message(message: str, level: str = 'INFO') -> None:
    """Print a log message if logging is enabled in plugin settings.
    
    Args:
        message: The message to log
        level: Log level ('INFO', 'WARNING', 'ERROR', 'DEBUG')
    """
    try:
        # Try to get the plugin settings
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            if hasattr(props, 'enable_logging') and not props.enable_logging:
                return  # Logging is disabled
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context (e.g., during testing), always log
        pass
    
    # Print with level prefix
    if level.upper() == 'ERROR':
        print(f"[ERROR] {message}")
    elif level.upper() == 'WARNING':
        print(f"[WARNING] {message}")
    elif level.upper() == 'DEBUG':
        print(f"[DEBUG] {message}")
    else:
        print(message)


def is_logging_enabled() -> bool:
    """Check if logging is currently enabled in plugin settings.
    
    Returns:
        True if logging is enabled or settings cannot be accessed, False otherwise
    """
    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            if hasattr(props, 'enable_logging'):
                return props.enable_logging
    except (ImportError, AttributeError, RuntimeError):
        pass
    
    # Default to True if we can't access settings
    return True


# Global timer storage for performance measurements
_performance_timers: Dict[str, float] = {}


def start_timer(block_name: str) -> None:
    """Start a performance timer for a named code block.
    
    Always logs timing info regardless of log output setting.
    
    Args:
        block_name: Name of the code block being timed
    """
    _performance_timers[block_name] = time.time()


def stop_timer(block_name: str) -> float:
    """Stop a performance timer and log elapsed time.
    
    Always logs timing info regardless of log output setting.
    
    Args:
        block_name: Name of the code block being timed
        
    Returns:
        Elapsed time in seconds
    """
    if block_name not in _performance_timers:
        print(f"[TIMER] Warning: No timer started for '{block_name}'")
        return 0.0
    
    start_time = _performance_timers.pop(block_name)
    elapsed = time.time() - start_time
    print(f"[TIMER] {block_name}: {elapsed:.3f} seconds")
    return elapsed
