"""
ENCODING FIX - MUST BE IMPORTED FIRST BEFORE ANY OTHER MODULES

This module fixes UnicodeEncodeError issues in Docker/Railway environments
where stdout/stderr default to ASCII encoding.

Solution based on:
- https://bugs.python.org/issue19977
- https://wiki.python.org/moin/PrintFails
- https://github.com/docker-library/python/issues/147
"""
import sys
import os
import io

# Force UTF-8 mode via environment
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8:surrogateescape'
os.environ['LANG'] = 'C.UTF-8'
os.environ['LC_ALL'] = 'C.UTF-8'

def _safe_reconfigure():
    """
    Reconfigure stdout/stderr to handle encoding errors gracefully.
    Uses 'surrogateescape' which replaces unencodable characters instead of raising.
    """
    try:
        # Method 1: Use reconfigure if available (Python 3.7+)
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='surrogateescape')
        elif hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding='utf-8',
                errors='surrogateescape',
                line_buffering=True
            )
    except Exception:
        pass

    try:
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='surrogateescape')
        elif hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer,
                encoding='utf-8',
                errors='surrogateescape',
                line_buffering=True
            )
    except Exception:
        pass

# Apply fix immediately when module is imported
_safe_reconfigure()

def disable_httpx_logging():
    """
    Disable all httpx and httpcore logging which can cause encoding issues
    when logging non-ASCII response data.
    """
    import logging

    # Disable httpx logging completely
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore.http11").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore.connection").setLevel(logging.CRITICAL)

    # Disable anthropic SDK logging
    logging.getLogger("anthropic").setLevel(logging.CRITICAL)
    logging.getLogger("anthropic._base_client").setLevel(logging.CRITICAL)

    # Disable notion client logging
    logging.getLogger("notion_client").setLevel(logging.CRITICAL)

# Also configure logging handlers to use surrogateescape
def configure_safe_logging():
    """Configure logging to never raise UnicodeEncodeError."""
    import logging

    class SafeStreamHandler(logging.StreamHandler):
        """A StreamHandler that never raises encoding errors."""
        def emit(self, record):
            try:
                msg = self.format(record)
                # Force ASCII output, replace any problematic characters
                safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
                stream = self.stream
                stream.write(safe_msg + self.terminator)
                self.flush()
            except Exception:
                pass  # Never raise

    # Replace root handler
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, logging.StreamHandler):
            root.removeHandler(handler)

    safe_handler = SafeStreamHandler(sys.stdout)
    safe_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    root.addHandler(safe_handler)
