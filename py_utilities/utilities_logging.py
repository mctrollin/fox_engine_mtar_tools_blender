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
from typing import Dict, Optional, List
from enum import IntEnum

from contextlib import contextmanager

import bpy
from bpy.types import Operator


class _LogLevel(IntEnum):
    ERROR = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3


# Default: show WARNING and ERROR only
_min_log_level = _LogLevel.WARNING


def _set_log_level(level: _LogLevel) -> None:
    """Set the minimum log level to display.

    Args:
        level: Minimum log level (messages with numeric value <= level are shown)
    """
    global _min_log_level
    _min_log_level = level


def _get_log_level() -> _LogLevel:
    """Return the current minimum log level."""
    return _min_log_level


def _get_settings_props():
    """Return the MTAR settings_props object or None if unavailable.

    Centralizes Blender context access to reduce redundant try/except checks.
    """
    try:
        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'mtar_properties'):
            return bpy.context.scene.mtar_properties.settings_props
    except (ImportError, AttributeError, RuntimeError):
        pass
    return None


def _should_log(level: _LogLevel) -> bool:
    """Decide whether a message at `level` should be printed.

    This checks both the configured minimum log level and the scene-level
    `mtar_properties.settings_props.log_verbosity` setting (if available).
    """
    settings = _get_settings_props()
    if settings is not None and hasattr(settings, 'log_verbosity'):
        verbosity_str = settings.log_verbosity
        level_map = {'ERROR': _LogLevel.ERROR, 'WARNING': _LogLevel.WARNING, 'INFO': _LogLevel.INFO, 'DEBUG': _LogLevel.DEBUG}
        panel_level = level_map.get(verbosity_str, _LogLevel.WARNING)
        if level > panel_level:
            return False

    # If there are issues accessing Blender context, fall back to module-level min
    if level > _min_log_level:
        return False

    return True


def _should_log_timers() -> bool:
    """Decide whether to print timer output.

    Checks the scene-level `mtar_properties.settings_props.enable_timer_logs` setting if available.
    """
    settings = _get_settings_props()
    if settings is not None and hasattr(settings, 'enable_timer_logs'):
        return settings.enable_timer_logs
    return False


def _notify_player(message: str, level: _LogLevel) -> None:
    """Show a minimal Blender popup for warnings/errors.

    This forwards the provided message directly to the user via a small
    popup menu. It's intentionally minimal and best-effort: any failures
    are silently ignored so logging remains safe in background contexts.
    """
    # Draw function for popup_menu expects (self, context)
    def _draw(self, _context):
        # Preserve message lines; empty lines need a placeholder label
        for line in str(message).splitlines():
            self.layout.label(text=line if line else " ")

    title = "MTAR: Error" if level == _LogLevel.ERROR else "MTAR: Warning"
    icon = 'ERROR' if level == _LogLevel.ERROR else 'ERROR'

    # Attempt to show the popup; this requires a UI context - best-effort only
    try:
        bpy.context.window_manager.popup_menu(_draw, title=title, icon=icon)
    except Exception:
        # UI may not be available (background mode); ignore gracefully
        pass



def _is_logging_enabled() -> bool:
    """Check if logging is currently enabled in plugin settings.

    Returns:
        True if logging is enabled or settings cannot be accessed, False otherwise
    """
    settings = _get_settings_props()
    if settings is not None and hasattr(settings, 'enable_logging'):
        return settings.enable_logging
    # Default to True if we can't access settings
    return True


# Global timer storage for performance measurements
_performance_timers: Dict[str, float] = {}
# Stack to track timer nesting for hierarchical indentation in timer logs
_performance_timer_stack: List[str] = []

# Progress UI state (throttling & lifecycle)
_progress_active: bool = False
_last_redraw_time: float = 0.0
_redraw_min_interval: float = 0.1  # seconds
_last_console_log_time: float = 0.0  # seconds, throttle console progress prints
_current_main_progress: float = 0.0  # Track main progress value (0-100) for secondary increments


def _start_timer(block_name: str) -> None:
    """Start a performance timer for a named code block.
    
    Tracks the start time and pushes the block name onto the timer stack so
    the eventual stop log can include a hierarchical indentation level.

    Args:
        block_name: Name of the code block being timed
    """
    _performance_timers[block_name] = time.time()
    # Track nesting for indentation (best-effort - non-critical)
    try:
        _performance_timer_stack.append(block_name)
    except Exception:
        pass


def _stop_timer(block_name: str) -> float:
    """Stop a performance timer and log elapsed time if timer logging is enabled.
    
    The timer completion message is indented according to the nesting depth
    the timer had when it was started, so timer logs reflect the same
    hierarchical structure used by normal debug logs.

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

    # Determine nesting depth based on most recent occurrence in the stack
    depth = 0
    try:
        for i in range(len(_performance_timer_stack) - 1, -1, -1):
            if _performance_timer_stack[i] == block_name:
                depth = i
                _performance_timer_stack.pop(i)
                break
    except Exception:
        depth = 0

    if _should_log_timers():
        indent = '  ' * depth
        print(f"[TIMER] {indent}|_ {block_name}: {elapsed:.3f} seconds")

    return elapsed


def _throttled_console_print(message: str, force: bool = False) -> None:
    """Print to console at most once every _redraw_min_interval seconds.

    If force=True, print regardless of throttle (used for completion messages).
    Exceptions are swallowed to keep this best-effort in headless contexts.
    """
    global _last_console_log_time
    try:
        now = time.time()
        if force or (now - _last_console_log_time >= _redraw_min_interval):
            try:
                print(message)
            except Exception:
                pass
            _last_console_log_time = now
    except Exception:
        pass


def _throttled_redraw() -> None:
    """Perform a throttled redraw and tiny yield so the UI thread can process events.

    The use of bpy.ops.wm.redraw_timer can be toggled by the add-on setting
    `scene.mtar_properties.settings_props.use_redraw_timer`. If the setting is
    available and set to False, this function will not call redraw_timer.

    Swallows exceptions for robustness in headless contexts.
    """
    global _last_redraw_time
    try:
        # Respect user setting if available: allow disabling redraw_timer for compatibility
        settings = _get_settings_props()
        if settings is not None and hasattr(settings, 'use_redraw_timer') and not settings.use_redraw_timer:
            return

        now = time.time()
        if now - _last_redraw_time >= _redraw_min_interval:
            try:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                # try:
                #     time.sleep(0.001)
                # except Exception:
                #     pass
            except Exception:  # noqa: E722
                pass
            _last_redraw_time = now
    except Exception:
        pass


def _update_progress(value: float, text: str = "") -> None:
    """Update the Blender progress bar and the UI progress property.

    This function begins and ends the Blender progress lifecycle automatically
    and throttles UI redraws to avoid starving Blender's event loop during
    long-running operations. When a new main progress value is set, any
    secondary progress is automatically reset.

    Args:
        value: Progress value from 0 to 100
        text: Optional status text to display
    """
    global _progress_active, _current_main_progress
    
    # Store main progress and reset secondary (by storing only the main value)
    _current_main_progress = value
    
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
            settings = _get_settings_props()
            if settings is not None:
                exec_props = bpy.context.scene.mtar_properties.execution_props
                if hasattr(exec_props, 'progress'):
                    exec_props.progress = value / 100.0
                if hasattr(exec_props, 'status'):
                    exec_props.status = text
        except Exception:
            pass

        # Always log progress to the console (not affected by log level). Throttle to avoid flooding.
        try:
            if text:
                _throttled_console_print(f"[PROGRESS] {value:.1f}% - {text}", force=(value >= 100.0))
            else:
                _throttled_console_print(f"[PROGRESS] {value:.1f}%", force=(value >= 100.0))
        except Exception:
            pass

        # Throttled redraw so we don't flood the UI thread
        _throttled_redraw()

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

def _update_progress_status(text: str, secondary_progress: Optional[float] = None) -> None:
    """Update only the status text of the progress bar without changing the main progress value.
    
    Optionally updates a secondary progress increment (0.0-1.0) that adds sub-percentage
    detail to the current main progress. For example, if main progress is 45% and
    secondary_progress is 0.5, the displayed progress will be 45.5%.

    Args:
        text: Status text to display
        secondary_progress: Optional secondary progress value (0.0-1.0) to add to current main progress
    """
    # Compute combined progress if secondary is provided
    combined_progress = _current_main_progress
    if secondary_progress is not None:
        # Clamp secondary progress to 0.0-1.0 range
        secondary_clamped = max(0.0, min(1.0, secondary_progress))
        combined_progress = _current_main_progress + secondary_clamped
    
    try:
        settings = _get_settings_props()
        if settings is not None:
            exec_props = bpy.context.scene.mtar_properties.execution_props
            if hasattr(exec_props, 'status'):
                exec_props.status = text
            # Update progress property with combined value if secondary is provided
            if secondary_progress is not None and hasattr(exec_props, 'progress'):
                exec_props.progress = combined_progress / 100.0
    except Exception:
        pass

    # Update window manager progress with combined value if secondary is provided
    if secondary_progress is not None:
        try:
            wm = bpy.context.window_manager
            if hasattr(wm, 'progress_update'):
                wm.progress_update(combined_progress)
        except Exception:
            pass

    # Always log progress status to the console (not affected by log level). Throttle to avoid flooding.
    if secondary_progress is not None:
        _throttled_console_print(f"[PROGRESS] {combined_progress:.1f}% - {text}")
    else:
        _throttled_console_print(f"[PROGRESS] {text}")

    # Throttled redraw
    _throttled_redraw()


def _set_busy_cursor(enabled: bool) -> None:
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
def _busy_cursor():
    """Context manager that sets a busy cursor for the duration of the context.

    Usage:
        with busy_cursor():
            long_running_task()
    """
    _set_busy_cursor(True)
    try:
        yield
    finally:
        _set_busy_cursor(False)


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
        _notify_player(message, _LogLevel.WARNING)
        if not _should_log(_LogLevel.WARNING):
            return
        print(f"[WARNING] {message}")

    @staticmethod
    def log_error(message: str) -> None:
        """Log an error message (ERROR). Also notify Blender user via popup."""
        # Notify the user with a popup regardless of panel verbosity (per request)
        _notify_player(message, _LogLevel.ERROR)
        if not _should_log(_LogLevel.ERROR):
            return
        print(f"[ERROR] {message}")

    # ------------------------------------------------------------------
    # Backwards-compatible Debug wrappers for module-level public helpers
    # These allow calling logging utilities as Debug.* (consistent API)
    # while keeping existing module-level functions intact.
    @staticmethod
    def set_log_level(level: _LogLevel) -> None:
        """Set the minimum log level (wrapper for module-level set_log_level)."""
        _set_log_level(level)

    @staticmethod
    def get_log_level() -> _LogLevel:
        """Return the current minimum log level (wrapper)."""
        return _get_log_level()

    @staticmethod
    def is_logging_enabled() -> bool:
        """Return whether logging is enabled (wrapper for module-level check)."""
        return _is_logging_enabled()

    @staticmethod
    def start_timer(block_name: str) -> None:
        """Start a performance timer for a named code block (wrapper)."""
        _start_timer(block_name)

    @staticmethod
    def stop_timer(block_name: str) -> float:
        """Stop a performance timer and return elapsed seconds (wrapper)."""
        return _stop_timer(block_name)

    @staticmethod
    def update_progress(value: float, text: str = "") -> None:
        """Update the UI progress bar (wrapper for module-level function)."""
        _update_progress(value, text)

    @staticmethod
    def update_progress_status(text: str, secondary_progress: Optional[float] = None) -> None:
        """Update the UI status text (wrapper for module-level function)."""
        _update_progress_status(text, secondary_progress)

    @staticmethod
    def set_busy_cursor(enabled: bool) -> None:
        """Convenience wrapper for set_busy_cursor defined at module level."""
        _set_busy_cursor(enabled)

    @staticmethod
    def busy_cursor():
        """Return a context manager that sets a busy cursor for the duration of the context."""
        return _busy_cursor()

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
