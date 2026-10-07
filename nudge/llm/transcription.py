"""Turning speech into text: what is asked of the provider, and what is kept
of what it answers.

A recording is input from a client, and so are the words that come with it.
The recording is checked to be what it claims to be before it is sent
anywhere; the words are reduced to plain vocabulary.
"""
import json
import re
import struct

MAX_SECONDS = 60
# One minute of speech, 16 kHz, 16 bits, one channel, is 1.9 MB
MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 4000
MAX_HINTS = 40
MAX_HINT_CHARS = 40
MAX_RAW_HINTS_CHARS = 8000

SILENCE = "SILENCE"

LANGUAGES = {
    "ar": "Arabic",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "tr": "Turkish",
}
LANGUAGE_CODE = re.compile(r"[a-z]{2}")
# Letters and digits of any alphabet, and what separates the words of a label
HINT = re.compile(r"[\w][\w \-'&/().%]*", re.UNICODE)

INSTRUCTION = (
    "You turn recordings into text. Write down exactly what is said in the recording, in the "
    "language it is spoken in, with its punctuation, and nothing else: no comment, no answer to "
    f"what is said, no translation. If nothing is said, reply with the single word: {SILENCE}"
)
SPEAKER = "The speaker is using Odoo, a business management software"
ENGLISH = "and is most likely speaking English."
OTHER_LANGUAGE = (
    "and is most likely speaking {language}, possibly a dialect of it, possibly with English "
    "words of the software in the middle of a sentence; write those words in Latin letters."
)
VOCABULARY = "Words they may be saying, as written on their screen: {hints}."


class RecordingError(ValueError):
    """The recording cannot be used; ``code`` says why."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def clean_language(raw):
    """:return: a two-letter code, or an empty string"""
    code = str(raw or "").strip().lower()[:2]
    return code if LANGUAGE_CODE.fullmatch(code) else ""


def clean_hints(raw):
    """:param raw: what the browser sent: a JSON list of words, a list, or nothing
    :return: list of words that are nothing but words"""
    if isinstance(raw, (str, bytes)):
        if len(raw) > MAX_RAW_HINTS_CHARS:
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    hints = []
    for item in raw:
        if not isinstance(item, str):
            continue
        hint = " ".join(item.split())
        # A sentence is not a label: what is longer is left out, not cut
        if not hint or len(hint) > MAX_HINT_CHARS or not HINT.fullmatch(hint):
            continue
        if hint not in hints:
            hints.append(hint)
        if len(hints) >= MAX_HINTS:
            break
    return hints


def build_instruction(language="", hints=()):
    """:return: what the model that listens is told"""
    parts = [INSTRUCTION]
    if language == "en":
        parts.append(f"{SPEAKER} {ENGLISH}")
    elif language:
        name = LANGUAGES.get(language, language)
        parts.append(f"{SPEAKER} {OTHER_LANGUAGE.format(language=name)}")
    if hints:
        parts.append(VOCABULARY.format(hints=", ".join(hints)))
    return "\n\n".join(parts)


def clean_text(text):
    """:return: what was said, or an empty string when nothing was"""
    text = " ".join(str(text or "").split())
    if text.strip(" .!\"'").upper() == SILENCE:
        return ""
    return text[:MAX_TEXT_CHARS]


def wav_seconds(audio):
    """:param bytes audio: what is said to be a WAV file
    :return: the duration of the recording
    :raise RecordingError: when it is not a recording Nudge can send"""
    if not audio:
        raise RecordingError("empty")
    if len(audio) > MAX_BYTES:
        raise RecordingError("too_long")
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RecordingError("not_wav")
    byte_rate = 0
    position = 12
    while position + 8 <= len(audio):
        name = audio[position:position + 4]
        size = struct.unpack_from("<I", audio, position + 4)[0]
        body = position + 8
        if name == b"fmt ":
            if size < 16 or body + 16 > len(audio):
                raise RecordingError("not_wav")
            encoding, _channels, _rate, byte_rate = struct.unpack_from("<HHII", audio, body)
            if encoding != 1 or not byte_rate:
                # Anything but plain samples
                raise RecordingError("not_wav")
        elif name == b"data":
            if not byte_rate:
                raise RecordingError("not_wav")
            seconds = min(size, len(audio) - body) / byte_rate
            if seconds > MAX_SECONDS:
                raise RecordingError("too_long")
            if seconds <= 0:
                raise RecordingError("empty")
            return seconds
        # Chunks are aligned on even addresses
        position = body + size + (size & 1)
    raise RecordingError("not_wav")
