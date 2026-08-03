# pylint: disable=missing-module-docstring, missing-function-docstring
"""
Tests for the two-layer handle profanity check (#278).

No profane term is ever typed into this file. Every case below is built at
runtime from the wordlist ``better-profanity`` ships in its own installed
package — the same source ``app/services/handles.py`` reads from — so
these assertions exercise the real list without a copy of it living here.
"""

import os

import better_profanity
import pytest

from app.services import handles
from app.services.handles import MIN_SUBSTRING_LENGTH, is_profane

# Digit -> letter, the inverse of handles._LEET_TRANSLATION. Used to build
# leetspeak variants of library words at runtime.
_TO_LEET = str.maketrans({'e': '3', 'a': '4', 'i': '1', 'o': '0', 's': '5', 't': '7'})


def _library_words():
    """Single-token, alphabetic entries straight from the installed package."""
    path = os.path.join(
        os.path.dirname(better_profanity.__file__), 'profanity_wordlist.txt'
    )
    with open(path, encoding='utf-8') as wordlist_file:
        for line in wordlist_file:
            word = line.strip().lower()
            if word and word.isalpha():
                yield word


# A handle is capped at 30 chars (HANDLE_RE), so pad-able words need room
# left for a prefix/suffix. Capped at 40 words so the parametrized cases
# below stay fast; that's already a two-figure sample of the library's own
# ~900-entry list.
_SHORT_WORDS = sorted({w for w in _library_words() if 3 <= len(w) <= 10})[:40]
_LONG_WORDS = sorted({w for w in _library_words() if len(w) >= MIN_SUBSTRING_LENGTH})[
    :40
]


def test_wordlist_was_actually_loaded():
    # Guards against a silent zero-word list making every test below vacuous.
    assert len(_SHORT_WORDS) > 0
    assert len(_LONG_WORDS) > 0


@pytest.mark.parametrize('word', _SHORT_WORDS)
def test_bare_word_is_rejected(word):
    assert is_profane(word) is True


@pytest.mark.parametrize('word', _SHORT_WORDS)
def test_hyphen_separated_word_is_rejected(word):
    assert is_profane(f'{word}-fan') is True
    assert is_profane(f'the-{word}') is True


@pytest.mark.parametrize('word', _SHORT_WORDS)
def test_digit_separated_word_is_rejected(word):
    # A pure library `contains_profanity()` call on this glued string misses
    # it (digits are ALLOWED_CHARACTERS to the library, so they don't
    # tokenize on their own) — this is the gap the word-boundary pass closes
    # by splitting on digits itself before handing tokens to the library.
    assert is_profane(f'{word}123') is True


@pytest.mark.parametrize('word', _LONG_WORDS)
def test_glued_compound_is_caught_by_the_substring_pass(word):
    # Long enough that the substring pass (>= MIN_SUBSTRING_LENGTH) applies.
    # A plain word-boundary check misses both of these outright.
    assert is_profane(f'{word}fan') is True
    assert is_profane(f'xx{word}') is True


@pytest.mark.parametrize('word', _LONG_WORDS)
def test_leetspeak_digits_are_normalized_before_matching(word):
    # Caught via the substring pass: folding the leet digits back to
    # letters reconstructs the original (>= MIN_SUBSTRING_LENGTH) word.
    leeted = word.translate(_TO_LEET)
    # A word with no leet-able letters isn't exercising anything.
    if leeted == word:
        pytest.skip('word has no leetspeak-able characters')
    assert is_profane(leeted) is True
    assert is_profane(f'{leeted}-fan') is True


@pytest.mark.parametrize(
    'handle',
    [
        'moviefan',
        'the-office-fan',
        'book-lover',
        'adam123',
        'xxfilmxx',
        'watch-list-99',
        'druthers-user',
        'popcorn-times',
    ],
)
def test_ordinary_handles_are_not_rejected(handle):
    assert is_profane(handle) is False


def test_min_substring_length_is_the_measured_recommendation():
    # 6 chars is the recorded 61.2% catch / 0.26% collateral tradeoff (see
    # module docstring in app/services/handles.py); pinned here so a change
    # to the constant is a deliberate, reviewed edit rather than a drive-by.
    assert MIN_SUBSTRING_LENGTH == 6


def test_short_word_below_substring_floor_is_not_caught_when_glued():
    # Documents the known gap rather than hiding it: a glued compound built
    # from a word shorter than the floor can slip past both layers. This is
    # the tradeoff the six-character threshold was chosen to accept.
    short_words = [w for w in _SHORT_WORDS if len(w) < MIN_SUBSTRING_LENGTH]
    assert short_words, 'expected at least one library word below the floor'
    word = short_words[0]
    glued = f'xx{word}xx'
    # pylint: disable-next=protected-access
    if handles._tokens(glued) != [glued]:
        pytest.skip('this word tokenizes on its own, not the gap being documented')
    assert is_profane(glued) is False


def test_tokens_lowercase_and_split_on_hyphens_and_every_digit():
    # pylint: disable=protected-access
    assert handles._tokens('The-Word-Fan') == ['the', 'word', 'fan']
    assert handles._tokens('word9fan') == ['word', 'fan']
    assert handles._tokens('word') == ['word']
    # The regression case: a pure numeric suffix must disappear entirely,
    # not partially resolve to letters and fragment the split (see the
    # module docstring in app/services/handles.py for why the word-boundary
    # pass never leet-folds before splitting).
    assert handles._tokens('word123') == ['word']


def test_glued_and_unleeted_folds_leet_digits_and_strips_separators():
    # pylint: disable=protected-access
    assert handles._glued_and_unleeted('the-word-fan') == 'thewordfan'
    assert handles._glued_and_unleeted('H3LL0') == 'hello'
    assert handles._glued_and_unleeted('word9fan') == 'wordfan'
