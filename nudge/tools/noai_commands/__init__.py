"""Common requests understood without any AI: a grammar over a lexicon of the
Arabic dialects and English. See ``grammar.parse``."""
from . import grammar, lexicon, normalise
from .grammar import THRESHOLD, Parse, parse

__all__ = ["grammar", "lexicon", "normalise", "parse", "Parse", "THRESHOLD"]
