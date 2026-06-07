"""jobs/research/language_detector.py — Detect the language of any text."""
import logging

log = logging.getLogger(__name__)

_LANG_NAMES = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "kn": "Kannada", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian",
    "mk": "Macedonian", "ml": "Malayalam", "mr": "Marathi", "ne": "Nepali",
    "nl": "Dutch", "no": "Norwegian", "pa": "Punjabi", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak",
    "sl": "Slovenian", "so": "Somali", "sq": "Albanian", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tl": "Filipino", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh-cn": "Chinese (Simplified)", "zh-tw": "Chinese (Traditional)",
}


def detect(text: str) -> str:
    from langdetect import detect as ld_detect, DetectorFactory
    DetectorFactory.seed = 0
    try:
        code = ld_detect(text)
        name = _LANG_NAMES.get(code, code)
        return f"{name} ({code})"
    except Exception as exc:
        log.error("language detect failed: %s", exc)
        return f"Detection failed: {exc}"


def run(message: str = None) -> str:
    if not message:
        return "Language detector ready."
    result = detect(message)
    return f"Detected language: {result}"
