POLISH_TRANSLATED_TEXT = "translated_text_polish"
POLISH_SOURCE_TEXT = "source_text_polish"

POLISHING_MODE_CHOICES = [
    POLISH_TRANSLATED_TEXT,
    POLISH_SOURCE_TEXT,
]

POLISHING_MODE_ALIASES = {
    "translated": POLISH_TRANSLATED_TEXT,
    "translation": POLISH_TRANSLATED_TEXT,
    "translated_text": POLISH_TRANSLATED_TEXT,
    "translated-text": POLISH_TRANSLATED_TEXT,
    "translated_text_polish": POLISH_TRANSLATED_TEXT,
    "translated-text-polish": POLISH_TRANSLATED_TEXT,
    "target": POLISH_TRANSLATED_TEXT,
    "draft": POLISH_TRANSLATED_TEXT,
    "source": POLISH_SOURCE_TEXT,
    "source_text": POLISH_SOURCE_TEXT,
    "source-text": POLISH_SOURCE_TEXT,
    "source_text_polish": POLISH_SOURCE_TEXT,
    "source-text-polish": POLISH_SOURCE_TEXT,
    "original": POLISH_SOURCE_TEXT,
}


def normalize_polishing_mode(value: object, default: str = POLISH_TRANSLATED_TEXT) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return default
    normalized = raw_value.lower().replace(" ", "_")
    return POLISHING_MODE_ALIASES.get(normalized, default)


def polishing_mode_i18n_key(mode: object) -> str:
    normalized = normalize_polishing_mode(mode)
    return f"choice_{normalized}"
