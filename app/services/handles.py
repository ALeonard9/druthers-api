# pylint: disable=missing-function-docstring
"""
Profanity detection for claimed handles (#278).

A handle is the profile URL (``update_visibility`` in
``app/router/v1/router_visibility.py`` refuses to let anything leave
``private`` without one), so it needs to be rejectable the same way a
malformed or reserved handle already is — at claim time, with a 422.

Everything here runs against the wordlist ``better-profanity`` ships inside
its own installed package. Nothing profane is vendored, copied, or typed
into this repository: the list below is read straight out of the dependency
at import time and cached for the life of the process. If the package is
ever uninstalled, so is every profane term this module ever touched.

Handles have no spaces, so a plain word-boundary matcher misses a lot: a
compound like ``xxwordxx`` or ``word123`` never trips one on its own.
Detection is two layers for that reason, each normalizing the handle
differently because they're looking for different things:

1. **Word-boundary pass.** The handle is split into tokens on every hyphen
   *and* every digit — digits are ``ALLOWED_CHARACTERS`` to the library
   itself, so it never splits ``word123`` into ``word`` on its own; doing
   that split ourselves before handing tokens to
   ``better_profanity.profanity`` is what turns a 0% catch rate on
   ``<word>123``-shaped handles into a full one. Each token is then checked
   as-is (the library's own ``VaryingString`` matching already tolerates
   leetspeak *within* a token). This is what catches ``the-<word>``,
   ``<word>-fan``, and ``<word>123``.
2. **Substring pass.** Separately, leetspeak digits with an unambiguous
   letter reading (0/1/3/4/5/7 -> o/i/e/a/s/t — the entire leetspeak
   alphabet available inside a handle, since ``HANDLE_RE`` allows nothing
   but ``[a-z0-9-]``) are folded back to letters, and whatever's left that
   isn't a letter (hyphens, plus digits with no letter reading) is stripped
   rather than split on, gluing the handle into one string. That string is
   checked for any wordlist entry of ``MIN_SUBSTRING_LENGTH`` characters or
   more appearing anywhere inside it. This is what catches glued compounds
   (``<word>fan``, ``xx<word>``) that never reach a boundary for pass 1 to
   find. (Folding leet digits into letters *before* splitting on them, the
   way pass 1 does, was tried and discarded: a padding run like ``123``
   partially resolves to letters — ``1``/``3`` do, ``2`` doesn't — which
   fragments the split instead of cleanly separating it. Pass 1 sidesteps
   that by never folding digits to letters in the first place.)

``MIN_SUBSTRING_LENGTH`` is measured, not guessed: against a 126k-word
English dictionary (``pyspellchecker``, already a dependency), a 6-character
floor catches 61.2% of glued compounds built from this library's own list,
at a 0.26% collateral rate against unrelated dictionary words. Shorter
floors catch more but the collateral climbs fast (4 chars: 2.57%, including
common words like "accuracy").

No wordlist is complete or neutral: this will still miss creative spellings
and will still catch a handful of legitimate handles (the measured
collateral above — e.g. words like "asexual" or "amorally" contain a
shorter profane word as a substring). That's why ``HANDLE_PROFANITY_ALLOWLIST``
exists (``app/config.py``) — Adam's per-handle override for exactly that
case, applied at the call site in ``router_visibility.py`` rather than here,
so this module stays a pure "is this profane" check.
"""

import os
import re
from typing import FrozenSet, List

import better_profanity
from better_profanity import profanity

# The measured recommendation (see module docstring). Named so it can be
# retuned without hunting through the substring-pass logic below.
MIN_SUBSTRING_LENGTH = 6

# Handles are restricted to [a-z0-9-] (HANDLE_RE in router_visibility.py),
# so hyphens and digits are the only non-letter characters a token pass
# ever needs to split on.
_NON_LETTER_RE = re.compile(r'[^a-z]+')

# Leetspeak digits with an unambiguous single-letter reading, used only by
# the substring pass (see module docstring for why the word-boundary pass
# deliberately does not fold these).
_LEET_TRANSLATION = str.maketrans(
    {'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't'}
)


def _tokens(handle: str) -> List[str]:
    """Split on every hyphen and digit — the word-boundary pass's input."""
    return [token for token in _NON_LETTER_RE.split(handle.lower()) if token]


def _glued_and_unleeted(handle: str) -> str:
    """
    Fold leetspeak digits to letters, strip whatever's left — the substring
    pass's input.
    """
    return _NON_LETTER_RE.sub('', handle.lower().translate(_LEET_TRANSLATION))


def _load_substring_words() -> FrozenSet[str]:
    """
    Wordlist entries at ``MIN_SUBSTRING_LENGTH`` or longer, folded the same
    way a handle is, for the substring pass.

    Read as plain text straight from the file the installed package ships
    (``profanity_wordlist.txt``) rather than via ``profanity.CENSOR_WORDSET``
    — those are ``VaryingString`` objects, which are neither sortable nor
    comparable, so a plain-string read is the only way to get something
    ``in``-able for substring containment. Multi-word entries (the list has
    a few, e.g. phrases) can't appear inside a single handle token and are
    dropped.
    """
    wordlist_path = os.path.join(
        os.path.dirname(better_profanity.__file__), 'profanity_wordlist.txt'
    )
    words = set()
    with open(wordlist_path, encoding='utf-8') as wordlist_file:
        for line in wordlist_file:
            word = line.strip().lower()
            if not word or ' ' in word:
                continue
            folded = _glued_and_unleeted(word)
            if len(folded) >= MIN_SUBSTRING_LENGTH:
                words.add(folded)
    return frozenset(words)


# Read once, at import time, and kept for the life of the process — see the
# module docstring for why nothing profane ends up committed anywhere.
profanity.load_censor_words()
SUBSTRING_WORDS: FrozenSet[str] = _load_substring_words()


def is_profane(handle: str) -> bool:
    """
    True if ``handle`` trips either detection layer.

    Only ever called at claim time (``update_visibility``); a handle already
    on file is never re-checked, so this cannot retroactively invalidate an
    existing one.
    """
    if any(profanity.contains_profanity(token) for token in _tokens(handle)):
        return True
    glued = _glued_and_unleeted(handle)
    return any(word in glued for word in SUBSTRING_WORDS)
