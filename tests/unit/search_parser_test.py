from app.services.search_parser import parse_provider_query


def test_parse_provider_query():
    # Bare text
    q, f = parse_provider_query('matrix', 'movies')
    assert q == 'matrix'
    assert f == {}

    # recognized key
    q, f = parse_provider_query('matrix director:Wachowski', 'movies')
    assert q == 'matrix'
    assert f == {'director': 'Wachowski'}

    # unknown key
    q, f = parse_provider_query('matrix unknown:val', 'movies')
    assert q == 'matrix unknown:val'
    assert f == {}

    # mixed syntax
    q, f = parse_provider_query(
        'the matrix director:Wachowski sequel unknown:val genre:sci-fi', 'movies'
    )
    assert q == 'the matrix sequel unknown:val'
    assert f == {'director': 'Wachowski', 'genre': 'sci-fi'}

    # empty value? Wait, our regex `\bkey:([^\s]+)` requires a non-space character.
    # If the user types `director: genre:sci-fi`, `director:` isn't matched by `\bkey:([^\s]+)` unless the value is something. Wait! If the value is empty, how is it passed?
    # e.g., `genre:""`? Or `director:`?
    # If `director: ` with a space, then the regex won't match, so it's treated as literal text.
    # The requirement says "empty value". Let's update the regex in search_parser.py to handle empty values.
    # empty value
    q, f = parse_provider_query('matrix director:', 'movies')
    assert q == 'matrix'
    assert f == {'director': ''}

    # multiple filters
    q, f = parse_provider_query('matrix director:Nolan year:2024', 'movies')
    assert q == 'matrix'
    assert f == {'director': 'Nolan', 'year': '2024'}
