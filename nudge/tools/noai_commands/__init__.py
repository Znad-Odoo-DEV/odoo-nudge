"""Common requests understood without any AI: a grammar over a lexicon of the
Arabic dialects and English. See ``grammar.parse``."""
from . import fuzzy, grammar, lexicon, normalise
from .grammar import THRESHOLD, Parse, parse

__all__ = ["fuzzy", "grammar", "lexicon", "normalise", "parse", "Parse", "THRESHOLD"]
