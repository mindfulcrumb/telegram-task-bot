"""Telegram Task Bot package."""
# Import encoding fix FIRST to handle Docker/Railway ASCII encoding issues
from bot import encoding_fix
encoding_fix.disable_httpx_logging()
