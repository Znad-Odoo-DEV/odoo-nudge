"""Words made comparable: the same word spelled as people spell it in every
Arabic dialect, in Latin letters, or with different digits meets the same
form. No state, no database."""
import re

from ...llm import arabic

# The marks, the letters and the article are those of ``llm.arabic``, shared with the
# answer memory, the import and the profile
ARABIC_MARKS = arabic.MARKS
LETTERS = arabic.LETTERS
DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"
    "۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789")
PUNCTUATION = str.maketrans({"٫": ".", "٬": ",", "،": ",", "؟": "?", "؛": ";"})
REPEATED = re.compile(r"(.)\1{2,}")
# "ل" or "ب" written onto a number: "ل050..."
PREFIX_ON_DIGIT = re.compile(r"(?<![\w])([لب])(?=\d)")
TIMES_BETWEEN = re.compile(r"(\d)\s*[x×*]\s*(?=[^\d\s])", re.IGNORECASE)
TIMES_BEFORE = re.compile(r"(?<![\w])[x×](?=\d)", re.IGNORECASE)
TOKEN = re.compile(r"[\w@.+\-/]+|[×*]")
ARABIC = re.compile("[؀-ۿ]")
CLITICS = ("وال", "بال", "كال", "فال", "لل")


class Token:
    """One word of the sentence: as written (digits made Latin), as compared,
    and as stemmed"""
    __slots__ = ("raw", "norm", "stem")

    def __init__(self, raw):
        self.raw = raw
        self.norm = fold(raw)
        self.stem = stem(self.norm)

    def __repr__(self):
        return f"Token({self.raw!r})"


def fold(text):
    """:return: ``text`` lower case, no marks, one form of each letter, Latin
    digits, a letter repeated three times or more written once"""
    return REPEATED.sub(r"\1", arabic.fold(text))


def stem(word):
    """:return: ``word`` without what is written onto it: the Arabic "al" and
    the particles before it ("بالفاتورة", "للعملاء"), an English plural"""
    if not word:
        return word
    if ARABIC.search(word):
        for clitic in CLITICS:
            if word.startswith(clitic) and len(word) - len(clitic) >= 2:
                word = word[len(clitic):]
                if clitic == "لل":
                    return word
                break
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        return word
    if len(word) > 4 and word.isascii() and word.isalpha():
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith(("ses", "xes", "ches", "shes")):
            return word[:-2]
        if word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
    return word


def prepare(text):
    """:return: ``text`` with the digits Latin and the glued marks apart"""
    text = ARABIC_MARKS.sub("", text or "").translate(DIGITS).translate(PUNCTUATION)
    text = PREFIX_ON_DIGIT.sub(r"\1 ", text)
    text = TIMES_BETWEEN.sub(r"\1 x ", text)
    return TIMES_BEFORE.sub("x ", text)


def tokenize(text):
    """:return: the :class:`Token` of ``text``, in order"""
    tokens = []
    for raw in TOKEN.findall(prepare(text)):
        if "@" not in raw:
            raw = raw.strip(".-/")
        else:
            raw = raw.rstrip(".")
        if raw:
            tokens.append(Token(raw))
    return tokens


def phrase_key(phrase):
    """:return: the tuple of stems a lexicon phrase is looked up by"""
    return tuple(token.stem for token in tokenize(phrase))
