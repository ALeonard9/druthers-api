# pylint: disable=missing-module-docstring, missing-function-docstring
from app.services.search_ranking import rank_and_cap


def test_exact_match_beats_partial_matches():
    hits = [
        {'title': 'Titanic II'},
        {'title': 'Titanic'},
        {'title': 'Raise the Titanic'},
    ]
    ranked = rank_and_cap('Titanic', hits)
    assert ranked[0]['title'] == 'Titanic'


def test_partial_match_popularity_beats_prefix_position():
    # Regression test for a real search bug: an obscure IGDB entry that
    # merely *starts with* the query used to automatically outrank the
    # famous game everyone actually means, which only *contains* the
    # query. Prefix position alone is not a reliable signal - within the
    # partial-match tier, popularity decides.
    hits = [
        {'title': 'Zelda 64', 'popularity': 3},
        {'title': 'The Legend of Zelda: Ocarina of Time', 'popularity': 9000},
    ]
    ranked = rank_and_cap('Zelda', hits)
    assert ranked[0]['title'] == 'The Legend of Zelda: Ocarina of Time'


def test_partial_match_without_popularity_preserves_provider_order():
    # No popularity data (e.g. TVMaze and Open Library don't supply any) -
    # prefix and contains matches are treated as equally valid partial
    # matches, and the provider's own result order is the tiebreaker.
    hits = [
        {'title': 'The Legend of Zelda: Anniversary Edition'},
        {'title': 'Zelda II: The Adventure of Link'},
    ]
    ranked = rank_and_cap('Zelda', hits)
    assert [r['title'] for r in ranked] == [r['title'] for r in hits]


def test_match_is_case_and_punctuation_insensitive():
    hits = [{'title': 'Spider-Man'}]
    ranked = rank_and_cap('spider man', hits)
    assert ranked[0]['title'] == 'Spider-Man'


def test_ties_preserve_provider_order():
    # Same tier (both "contains") for every item, so the provider's own
    # relevance/popularity order should be the tiebreaker and survive
    # unchanged.
    hits = [
        {'title': 'Something Else Entirely'},
        {'title': 'A Whole New World'},
        {'title': 'Yet Another Unrelated Title'},
    ]
    ranked = rank_and_cap('e', hits)
    assert [r['title'] for r in ranked] == [r['title'] for r in hits]


def test_caps_to_limit():
    hits = [{'title': f'Match {i}'} for i in range(10)]
    ranked = rank_and_cap('Match', hits)
    assert len(ranked) == 5


def test_caps_to_custom_limit():
    hits = [{'title': f'Match {i}'} for i in range(10)]
    ranked = rank_and_cap('Match', hits, limit=3)
    assert len(ranked) == 3


def test_empty_results():
    assert rank_and_cap('anything', []) == []


def test_missing_title_does_not_crash():
    hits = [{'imdb': 'tt1'}, {'title': 'Real Title', 'imdb': 'tt2'}]
    ranked = rank_and_cap('Real Title', hits)
    assert ranked[0]['title'] == 'Real Title'
