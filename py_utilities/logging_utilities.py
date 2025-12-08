"""Logging utilities for MTAR plugin.

Provides a small Unity-like Debug API and a simple verbosity filter.

API:
  Debug.log(msg)         -> informational message (INFO)
  Debug.log_warning(msg) -> warning message (WARNING)
  Debug.log_error(msg)   -> error message (ERROR)

You can control the minimum shown level with set_log_level(). By default
only WARNING and ERROR are shown.
"""
import time
from typing import Dict
from enum import IntEnum

import bpy


class _LogLevel(IntEnum):
    ERROR = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3


# Default: show WARNING and ERROR only
_min_log_level = _LogLevel.WARNING


def set_log_level(level: _LogLevel) -> None:
    """Set the minimum log level to display.

    Args:
        level: Minimum log level (messages with numeric value <= level are shown)
    """
    global _min_log_level
    _min_log_level = level


def get_log_level() -> _LogLevel:
    """Return the current minimum log level."""
    return _min_log_level


def _should_log(level: _LogLevel) -> bool:
    """Decide whether a message at `level` should be printed.

    This checks both the configured minimum log level and the scene-level
    `mtar_properties.log_verbosity` setting (if available).
    """
    if level > _min_log_level:
        return False

    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            # Check panel log_verbosity setting (replaces old enable_logging)
            if hasattr(props, 'log_verbosity'):
                verbosity_str = props.log_verbosity
                # Convert string to _LogLevel enum
                level_map = {'ERROR': _LogLevel.ERROR, 'WARNING': _LogLevel.WARNING, 'INFO': _LogLevel.INFO, 'DEBUG': _LogLevel.DEBUG}
                panel_level = level_map.get(verbosity_str, _LogLevel.WARNING)
                if level > panel_level:
                    return False
           
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, default to printing
        pass

    return True


def _should_log_timers() -> bool:
    """Decide whether to print timer output.
    
    Checks the scene-level `mtar_properties.enable_timer_logs` setting if available.
    """
    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            if hasattr(props, 'enable_timer_logs'):
                return props.enable_timer_logs
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, default to not logging
        pass
    
    return False


class Debug:
    """Static logging helpers.

    Usage:
        Debug.log("info")
        Debug.log_warning("warn")
        Debug.log_error("err")
    """

    @staticmethod
    def log(message: str) -> None:
        """Log an informational message (INFO).

        These messages are shown when the global level is set to INFO or DEBUG.
        """
        if not _should_log(_LogLevel.INFO):
            return
        print(message)

    @staticmethod
    def log_warning(message: str) -> None:
        """Log a warning message (WARNING)."""
        if not _should_log(_LogLevel.WARNING):
            return
        print(f"[WARNING] {message}")

    @staticmethod
    def log_error(message: str) -> None:
        """Log an error message (ERROR)."""
        if not _should_log(_LogLevel.ERROR):
            return
        print(f"[ERROR] {message}")



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
    
    Args:
        block_name: Name of the code block being timed
    """
    _performance_timers[block_name] = time.time()


def stop_timer(block_name: str) -> float:
    """Stop a performance timer and log elapsed time if timer logging is enabled.
    
    Args:
        block_name: Name of the code block being timed
        
    Returns:
        Elapsed time in seconds
    """
    if block_name not in _performance_timers:
        if _should_log_timers():
            print(f"[TIMER] Warning: No timer started for '{block_name}'")
        return 0.0
    
    start_time = _performance_timers.pop(block_name)
    elapsed = time.time() - start_time
    
    if _should_log_timers():
        print(f"[TIMER] {block_name}: {elapsed:.3f} seconds")
    
    return elapsed
