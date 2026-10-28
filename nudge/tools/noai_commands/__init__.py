"""Common requests understood without any AI: a grammar over a lexicon of the
Arabic dialects and English. See ``grammar.parse``."""
from . import agenda, bridge, fuzzy, grammar, lexicon, normalise, thanks
from .grammar import THRESHOLD, Parse, parse

__all__ = ["agenda", "bridge", "fuzzy", "grammar", "lexicon", "normalise", "parse", "Parse", "thanks", "THRESHOLD"]
