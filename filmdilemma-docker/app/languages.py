"""ISO 639-1-koder <-> engelska språknamn (frontenden visar namn)."""
NAMES = {
    "en": "English", "sv": "Swedish", "no": "Norwegian", "nb": "Norwegian", "da": "Danish", "fi": "Finnish",
    "is": "Icelandic", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "nl": "Dutch", "pl": "Polish", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian",
    "bg": "Bulgarian", "el": "Greek", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "sr": "Serbian",
    "hr": "Croatian", "bs": "Bosnian", "sl": "Slovenian", "et": "Estonian", "lv": "Latvian", "lt": "Lithuanian",
    "ka": "Georgian", "hy": "Armenian", "he": "Hebrew", "ar": "Arabic", "fa": "Persian", "ku": "Kurdish",
    "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "kn": "Kannada",
    "mr": "Marathi", "pa": "Punjabi", "ur": "Urdu", "zh": "Chinese", "cn": "Cantonese", "ja": "Japanese",
    "ko": "Korean", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "tl": "Tagalog",
    "la": "Latin", "ga": "Irish", "cy": "Welsh", "eu": "Basque", "ca": "Catalan", "gl": "Galician",
    "af": "Afrikaans", "sw": "Swahili", "zu": "Zulu", "xh": "Xhosa", "am": "Amharic", "yo": "Yoruba",
    "mn": "Mongolian", "kk": "Kazakh", "uz": "Uzbek", "sq": "Albanian", "mk": "Macedonian", "be": "Belarusian",
    "xx": "No Language", "eo": "Esperanto", "sh": "Serbo-Croatian", "yi": "Yiddish", "iu": "Inuktitut",
}
CODES = {v.lower(): k for k, v in NAMES.items()}
CODES["norwegian"] = "no"


def lang_name(code):
    if not code:
        return code
    return NAMES.get(code, code)


def lang_code(value):
    if not value:
        return value
    return CODES.get(str(value).lower(), value)
