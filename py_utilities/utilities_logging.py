"""Logging utilities for MTAR plugin.

Provides a small Unity-like Debug API and a simple verbosity filter.

Warnings and errors are also shown to the Blender user as a popup for improved visibility.

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
    `mtar_properties.settings_props.log_verbosity` setting (if available).
    """

    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            settings_props = props.settings_props
            # Check panel log_verbosity setting (replaces old enable_logging)
            if hasattr(settings_props, 'log_verbosity'):
                verbosity_str = settings_props.log_verbosity
                # Convert string to _LogLevel enum
                level_map = {'ERROR': _LogLevel.ERROR, 'WARNING': _LogLevel.WARNING, 'INFO': _LogLevel.INFO, 'DEBUG': _LogLevel.DEBUG}
                panel_level = level_map.get(verbosity_str, _LogLevel.WARNING)
                if level > panel_level:
                    return False
           
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, default to printing
        if level > _min_log_level:
            return False

    return True


def _should_log_timers() -> bool:
    """Decide whether to print timer output.
    
    Checks the scene-level `mtar_properties.settings_props.enable_timer_logs` setting if available.
    """
    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            settings_props = props.settings_props
            if hasattr(settings_props, 'enable_timer_logs'):
                return settings_props.enable_timer_logs
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, default to not logging
        pass
    
    return False


def _notify_player(message: str, level: _LogLevel) -> None:
    """Show a minimal Blender popup for warnings/errors.

    This forwards the provided message directly to the user via a small
    popup menu. It's intentionally minimal and best-effort: any failures
    are silently ignored so logging remains safe in background contexts.
    """
    try:
        # Draw function for popup_menu expects (self, context)
        def _draw(self, _context):
            # Preserve message lines; empty lines need a placeholder label
            for line in str(message).splitlines():
                self.layout.label(text=line if line else " ")

        title = "MTAR: Error" if level == _LogLevel.ERROR else "MTAR: Warning"
        icon = 'ERROR' if level == _LogLevel.ERROR else 'ERROR'

        # Attempt to show the popup; this requires a UI context
        try:
            bpy.context.window_manager.popup_menu(_draw, title=title, icon=icon)
        except Exception:
            # UI may not be available (background mode); ignore gracefully
            pass
    except Exception:
        # Never raise from the logger - keep it robust in all contexts
        pass



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
            Debug.log_warning(f"[TIMER] No timer started for '{block_name}'")
        return 0.0
    
    start_time = _performance_timers.pop(block_name)
    elapsed = time.time() - start_time
    
    if _should_log_timers():
        print(f"[TIMER] {block_name}: {elapsed:.3f} seconds")
    
    return elapsed


def update_progress(value: float, text: str = "") -> None:
    """Update the Blender progress bar and the UI progress property.
    
    Args:
        value: Progress value from 0 to 100
        text: Optional status text to display
    """
    try:
        # Update status bar progress
        wm = bpy.context.window_manager
        wm.progress_update(value)
        
        # Update UI panel progress property if available
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            exec_props = props.execution_props
            if hasattr(exec_props, 'progress'):
                exec_props.progress = value / 100.0
            
            if hasattr(exec_props, 'status'):
                exec_props.status = text
                
            # Force UI redraw so the progress bar in the panel updates
            # This is necessary because long-running operators block the UI thread
            try:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            except Exception:  # noqa: E722
                pass
    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, do nothing
        pass


def update_progress_status(text: str) -> None:
    """Update only the status text of the progress bar without changing the progress value.
    
    Args:
        text: Status text to display
    """
    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            props = bpy.context.scene.mtar_properties
            exec_props = props.execution_props
            
            if hasattr(exec_props, 'status'):
                exec_props.status = text
                
            # Force UI redraw
            try:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            except Exception:  # noqa: E722
                pass
    except (ImportError, AttributeError, RuntimeError):
        pass


from contextlib import contextmanager


def set_busy_cursor(enabled: bool) -> None:
    """Set or clear the busy/wait cursor in Blender (best-effort).

    This is intentionally non-throwing and best-effort so it can be used
    from background contexts, threads, or places where the UI may not be
    available. Use ``set_busy_cursor(True)`` at the start of a long-running
    operation and ``set_busy_cursor(False)`` in a finally block.

    Args:
        enabled: True to set busy/wait cursor, False to restore default cursor
    """
    try:
        # Prefer the window-level cursor API when available
        try:
            bpy.context.window.cursor_set('WAIT' if enabled else 'DEFAULT')
            return
        except Exception:
            # Fallback to window manager or ignore
            pass

        try:
            wm = bpy.context.window_manager
            # Some Blender builds may provide cursor methods on window manager
            # (this is defensive; cursor_set on window is more common)
            if hasattr(wm, 'cursor_modal_set'):
                if enabled:
                    wm.cursor_modal_set('WAIT')
                else:
                    # cursor_modal_set(None) clears modal cursor, but may not exist
                    try:
                        wm.cursor_modal_restore()
                    except Exception:
                        pass
            elif hasattr(wm, 'cursor_set'):
                wm.cursor_set('WAIT' if enabled else 'DEFAULT')
        except Exception:
            # Ignore issues probing window manager methods
            pass
    except Exception:
        # Never raise from utility functions used by UI code
        pass


@contextmanager
def busy_cursor():
    """Context manager that sets a busy cursor for the duration of the context.

    Usage:
        with busy_cursor():
            long_running_task()
    """
    set_busy_cursor(True)
    try:
        yield
    finally:
        set_busy_cursor(False)


# Convenience passthrough on Debug for callers elsewhere in the codebase
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
        """Log a warning message (WARNING). Also notify Blender user via popup."""
        # Notify the user with a popup regardless of panel verbosity (per request)
        try:
            _notify_player(message, _LogLevel.WARNING)
        except Exception:
            pass
        if not _should_log(_LogLevel.WARNING):
            return
        print(f"[WARNING] {message}")

    @staticmethod
    def log_error(message: str) -> None:
        """Log an error message (ERROR). Also notify Blender user via popup."""
        # Notify the user with a popup regardless of panel verbosity (per request)
        try:
            _notify_player(message, _LogLevel.ERROR)
        except Exception:
            pass
        if not _should_log(_LogLevel.ERROR):
            return
        print(f"[ERROR] {message}")

    @staticmethod
    def set_busy_cursor(enabled: bool) -> None:
        """Convenience wrapper for set_busy_cursor defined at module level."""
        set_busy_cursor(enabled)

    @staticmethod
    def busy_cursor():
        """Return a context manager that sets a busy cursor for the duration of the context."""
        return busy_cursor()
