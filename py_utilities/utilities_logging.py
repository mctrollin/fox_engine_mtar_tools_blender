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
from bpy.types import Operator


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

# Progress UI state (throttling & lifecycle)
_progress_active: bool = False
_last_redraw_time: float = 0.0
_redraw_min_interval: float = 0.5  # seconds (throttled to reduce UI load)
_redraw_count: int = 0  # instrument how many redraws we issued (for debugging)


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

    This function begins and ends the Blender progress lifecycle automatically
    and throttles UI redraws to avoid starving Blender's event loop during
    long-running operations.

    Args:
        value: Progress value from 0 to 100
        text: Optional status text to display
    """
    global _progress_active, _last_redraw_time
    try:
        wm = bpy.context.window_manager

        # Begin progress if not already active (best-effort)
        try:
            if not _progress_active:
                if hasattr(wm, 'progress_begin'):
                    try:
                        wm.progress_begin(0.0, 100.0)
                    except Exception:
                        try:
                            # Some builds accept no args
                            wm.progress_begin()
                        except Exception:
                            pass
                _progress_active = True
        except Exception:
            # Ignore issues probing window manager
            pass

        # Update the window manager progress value
        try:
            if hasattr(wm, 'progress_update'):
                wm.progress_update(value)
        except Exception:
            pass

        # Update UI panel progress property if available
        try:
            if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
                props = bpy.context.scene.mtar_properties
                exec_props = props.execution_props
                if hasattr(exec_props, 'progress'):
                    exec_props.progress = value / 100.0
                if hasattr(exec_props, 'status'):
                    exec_props.status = text
        except Exception:
            pass

        # Throttled redraw so we don't flood the UI thread
        try:
            now = time.time()
            if now - _last_redraw_time >= _redraw_min_interval:
                try:
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                    # Instrumentation: count redraws
                    try:
                        _redraw_count += 1
                    except Exception:
                        pass
                    # Tiny yield so the UI thread can process events
                    try:
                        time.sleep(0.001)
                    except Exception:
                        pass
                except Exception:  # noqa: E722
                    pass
                _last_redraw_time = now
        except Exception:
            pass

        # End progress if value indicates completion
        try:
            if _progress_active and value >= 100.0:
                if hasattr(wm, 'progress_end'):
                    try:
                        wm.progress_end()
                    except Exception:
                        pass
                _progress_active = False
        except Exception:
            pass

    except (ImportError, AttributeError, RuntimeError):
        # If we can't access Blender context, do nothing
        pass


def update_progress_status(text: str) -> None:
    """Update only the status text of the progress bar without changing the progress value.
    
    Args:
        text: Status text to display
    """
    global _last_redraw_time
    try:
        try:
            if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
                props = bpy.context.scene.mtar_properties
                exec_props = props.execution_props
                if hasattr(exec_props, 'status'):
                    exec_props.status = text
        except Exception:
            pass

        # Throttled redraw
        now = time.time()
        try:
            if now - _last_redraw_time >= _redraw_min_interval:
                try:
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                    # Instrumentation: count redraws
                    try:
                        _redraw_count += 1
                        try:
                            if _redraw_count % 50 == 0 and _should_log_timers():
                                Debug.log(f"[REDRAW] Count={_redraw_count}, last_interval={(now - _last_redraw_time):.3f}s")
                        except Exception:
                            pass
                    except Exception:
                        pass
                    # Tiny yield so the UI thread can process events
                    try:
                        time.sleep(0.001)
                    except Exception:
                        pass
                except Exception:  # noqa: E722
                    pass
                _last_redraw_time = now
        except Exception:
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
        except Exception:  # Best-effort: UI may not be available (background/headless mode)
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

    @staticmethod
    def report_and_log(operator: Operator, level: str, message: str) -> None:
        """Report a message to the Blender operator and also log it via Debug.

        Args:
            operator: Blender operator instance (must expose report())
            level: One of 'INFO', 'WARNING', 'ERROR'
            message: Message to report and log
        """
        # Report to the Blender operator UI (best-effort)
        try:
            operator.report({level}, message)
        except Exception:
            # Operator might not be available or report may fail in some contexts
            pass

        # Also log using the existing Debug logging methods so it's recorded
        if level == 'ERROR':
            Debug.log_error(message)
        elif level == 'WARNING':
            Debug.log_warning(message)
        else:
            Debug.log(message)
