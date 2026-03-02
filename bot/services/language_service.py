"""Language detection service — detects user language and manages home language preference.

Uses langdetect (Google's language detection, 55 languages) to explicitly detect
language at the input boundary, rather than relying on the LLM to guess.

Key concept: HOME LANGUAGE — a sticky preference that only changes on sustained,
intentional language switches. One accidental message in another language does NOT
flip the user's language.
"""
import logging
import re

from langdetect import detect_langs, LangDetectException

logger = logging.getLogger(__name__)

# Short/ambiguous tokens that should NOT trigger a language switch
AMBIGUOUS_TOKENS = {
    "ok", "si", "no", "hey", "hi", "hola", "bye", "lol", "haha",
    "yes", "yeah", "nah", "nice", "cool", "thanks", "thx", "ty",
    "good", "bad", "hmm", "wow", "oops", "bruh", "done", "yep",
}

# Minimum text length for reliable detection
MIN_DETECT_LENGTH = 8

# Confidence threshold for counting a message toward a language switch
CONFIDENCE_THRESHOLD = 0.85

# Consecutive messages in a different language required to auto-switch
CONSECUTIVE_SWITCH_COUNT = 3

# Per-user tracking: {user_id: {"lang": "es", "count": 2}}
# Resets on restart — acceptable, only affects auto-switch momentum
_consecutive_lang_tracker: dict[int, dict] = {}

# Explicit language switch phrases (user intentionally requests a language)
EXPLICIT_SWITCH_PATTERNS = {
    "en": [r"\benglish\s+please\b", r"\bin\s+english\b", r"\bspeak\s+english\b"],
    "es": [r"\ben\s+español\b", r"\bhabla\s+en\s+español\b", r"\bresponde\s+en\s+español\b"],
    "pt": [r"\bem\s+português\b", r"\bfala\s+em\s+português\b", r"\bresponde\s+em\s+português\b"],
    "fr": [r"\ben\s+français\b", r"\bparle\s+en\s+français\b"],
    "de": [r"\bauf\s+deutsch\b", r"\bsprich\s+deutsch\b"],
}

# ISO 639-1 codes to display names
SUPPORTED_LANGUAGES = {
    "en": "English", "es": "Spanish", "pt": "Portuguese",
    "fr": "French", "de": "German", "it": "Italian",
    "nl": "Dutch", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh-cn": "Chinese", "zh-tw": "Chinese",
    "ar": "Arabic", "hi": "Hindi", "tr": "Turkish",
    "pl": "Polish", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay",
    "tl": "Tagalog", "uk": "Ukrainian", "el": "Greek",
    "he": "Hebrew", "ca": "Catalan", "hr": "Croatian",
    "sk": "Slovak", "sl": "Slovenian", "bg": "Bulgarian",
    "sr": "Serbian", "fa": "Persian", "sw": "Swahili",
    "af": "Afrikaans",
}

# Whisper language names → ISO 639-1 codes
WHISPER_TO_ISO = {
    "english": "en", "spanish": "es", "portuguese": "pt",
    "french": "fr", "german": "de", "italian": "it",
    "dutch": "nl", "russian": "ru", "japanese": "ja",
    "korean": "ko", "chinese": "zh-cn", "arabic": "ar",
    "hindi": "hi", "turkish": "tr", "polish": "pl",
    "swedish": "sv", "danish": "da", "norwegian": "no",
    "finnish": "fi", "czech": "cs", "romanian": "ro",
    "hungarian": "hu", "thai": "th", "vietnamese": "vi",
    "indonesian": "id", "malay": "ms", "tagalog": "tl",
    "ukrainian": "uk", "greek": "el", "hebrew": "he",
    "catalan": "ca", "croatian": "hr", "slovak": "sk",
    "slovenian": "sl", "bulgarian": "bg", "serbian": "sr",
    "persian": "fa", "swahili": "sw", "afrikaans": "af",
}


def detect_explicit_switch(text: str) -> str | None:
    """Check if user explicitly requests a language switch.

    Returns ISO code if explicit request found, None otherwise.
    """
    lower = text.lower().strip()
    for lang_code, patterns in EXPLICIT_SWITCH_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return lang_code
    return None


def detect_language(text: str) -> tuple[str | None, float]:
    """Detect language of input text.

    Returns: (iso_code, confidence) or (None, 0.0) if detection fails/ambiguous.
    """
    if not text or not text.strip():
        return None, 0.0

    cleaned = text.strip()

    # Strip URLs, mentions, commands before detection
    words = cleaned.split()
    content_words = [
        w for w in words
        if not w.startswith(("http", "@", "/")) and not w.startswith("#")
    ]

    if not content_words:
        return None, 0.0

    # Check if all words are ambiguous
    if all(w.lower().rstrip(".,!?") in AMBIGUOUS_TOKENS for w in content_words):
        return None, 0.0

    # Check minimum length
    content_text = " ".join(content_words)
    if len(content_text) < MIN_DETECT_LENGTH:
        return None, 0.0

    try:
        results = detect_langs(content_text)
        if results:
            top = results[0]
            return str(top.lang), top.prob
        return None, 0.0
    except LangDetectException:
        return None, 0.0


def resolve_language(
    user_input: str,
    stored_lang: str | None,
    language_hint: str | None = None,
    user_id: int | None = None,
) -> tuple[str, bool]:
    """Resolve the effective language for a response.

    Uses a CONSECUTIVE MESSAGE COUNTER to prevent one-off messages from
    flipping the user's home language. A single Malay message among 474
    English messages will NOT cause a switch — the user must send 3+
    consecutive messages in the new language (or use an explicit phrase
    like "en español" / "English please").

    Args:
        user_input: The user's message text.
        stored_lang: User's stored preferred_language from DB (or None).
        language_hint: Optional Whisper-detected language name (e.g., "english").
        user_id: Internal DB user ID (for consecutive message tracking).

    Returns:
        (effective_language_code, should_update_db)
    """
    # 1. Check for explicit language switch request — always wins
    explicit = detect_explicit_switch(user_input)
    if explicit:
        _reset_tracker(user_id)
        return explicit, True

    # 2. Detect language from text
    detected_lang, confidence = detect_language(user_input)

    # 3. If Whisper provided a hint and text detection failed, use Whisper
    if not detected_lang and language_hint:
        whisper_code = WHISPER_TO_ISO.get(language_hint.lower())
        if whisper_code:
            detected_lang = whisper_code
            confidence = 0.7  # Whisper is reliable but not as precise

    # 4. First-ever message (no stored preference)
    if stored_lang is None:
        if detected_lang and confidence > 0.5:
            return detected_lang, True
        return "en", True  # Default to English for brand-new users

    # 5. Detection failed or ambiguous — use stored preference
    if not detected_lang:
        return stored_lang, False

    # 6. Same language as stored — reset any switch momentum, no change needed
    if detected_lang == stored_lang:
        _reset_tracker(user_id)
        return stored_lang, False

    # 7. Different language detected — require CONSECUTIVE high-confidence messages
    # This is the key fix for the Mar 2 Malay incident: one message never flips
    if confidence >= CONFIDENCE_THRESHOLD and user_id is not None:
        count = _track_consecutive(user_id, detected_lang)
        if count >= CONSECUTIVE_SWITCH_COUNT:
            logger.info(
                f"Language switch after {count} consecutive messages: "
                f"{stored_lang} -> {detected_lang} (confidence={confidence:.2f})"
            )
            _reset_tracker(user_id)
            return detected_lang, True
        else:
            logger.debug(
                f"Language switch momentum: {stored_lang} -> {detected_lang} "
                f"({count}/{CONSECUTIVE_SWITCH_COUNT}, confidence={confidence:.2f})"
            )
            # Respond in stored language but DON'T update DB
            return stored_lang, False

    # 8. Low confidence different language — stay with home language
    return stored_lang, False


def _track_consecutive(user_id: int, detected_lang: str) -> int:
    """Track consecutive messages in a different language for a user.

    Returns the current count after this message.
    """
    tracker = _consecutive_lang_tracker.get(user_id)
    if tracker and tracker["lang"] == detected_lang:
        tracker["count"] += 1
    else:
        _consecutive_lang_tracker[user_id] = {"lang": detected_lang, "count": 1}
    return _consecutive_lang_tracker[user_id]["count"]


def _reset_tracker(user_id: int | None):
    """Reset the consecutive language tracker for a user."""
    if user_id is not None:
        _consecutive_lang_tracker.pop(user_id, None)


def get_language_name(iso_code: str) -> str:
    """Convert ISO code to display name for system prompts."""
    return SUPPORTED_LANGUAGES.get(iso_code, iso_code.upper())
