# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access, missing-class-docstring
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services import book_search


def test_helpers():
    assert book_search._cover(11481354) == (
        'https://covers.openlibrary.org/b/id/11481354-L.jpg'
    )
    assert book_search._cover(None) is None
    assert book_search._authors({'author_name': ['Frank Herbert']}) == 'Frank Herbert'
    assert book_search._authors({}) is None
    # ISBN-13 preferred over ISBN-10 regardless of order.
    assert (
        book_search._pick_isbn({'isbn': ['0441172717', '9780441172719']})
        == '9780441172719'
    )
    assert book_search._pick_isbn({'isbn': ['0441172717']}) == '0441172717'
    assert book_search._pick_isbn({}) is None
    assert book_search._genre({'subject': ['Sci-fi', 'Deserts', 'Spice', 'Worms']}) == (
        'Sci-fi, Deserts, Spice'
    )


def test_normalize_title_strips_series_and_fixes_casing():
    n = book_search.normalize_title
    assert n('White Night (The Dresden Files, Book 9)') == 'White Night'
    assert n('The Scorch Trials (Maze Runner, Book Two)') == 'The Scorch Trials'
    assert n('The Great Hunt (The Wheel of Time Book 2)') == 'The Great Hunt'
    assert n('The dark tower') == 'The Dark Tower'
    assert n('How to win friends & influence people') == (
        'How to Win Friends & Influence People'
    )
    # Award badges and other trailing decoration go too.
    assert n('A Thousand Acres (Pulitzer Prize Winner)') == 'A Thousand Acres'
    # Never normalize a title away entirely.
    assert n('(Untitled)') == '(Untitled)'


def test_normalize_title_strips_only_real_edition_qualifiers():
    n = book_search.normalize_title
    assert n('The Scorch Trials Movie Tie-in Edition') == 'The Scorch Trials'
    assert n('Dune Ebook Collection') == 'Dune'
    assert n('Dune: 50th Anniversary Edition') == 'Dune'
    # 'Complete' is not a printing qualifier, and stripping 'Special Edition'
    # would leave nothing but an article — both titles stay whole.
    assert n('The Complete Collection') == 'The Complete Collection'
    assert n('The Special Edition') == 'The Special Edition'
    # Deliberate casing is never flattened.
    assert n('iRobot and McKay at NASA') == 'iRobot and McKay at NASA'
    assert n(None) is None


def test_pick_title_keeps_the_catalog_title_when_english_editions_have_it():
    p = book_search._pick_title
    # Order of the Phoenix: the bare series name is the *most common* edition
    # title (8x vs 6x), but the real title is in there, so it must stand.
    phoenix = ['Harry Potter'] * 8 + ['Harry Potter and the Order of the Phoenix'] * 6
    assert p(phoenix, 'Harry Potter and the Order of the Phoenix') == (
        'Harry Potter and the Order of the Phoenix'
    )
    # Punctuation is not meaning: 'Star Wars - Bloodline' matches the
    # edition's 'Star Wars: Bloodline', so it is not collapsed to 'Star Wars'.
    assert p(['Star Wars'] * 3 + ['Star Wars: Bloodline'], 'Star Wars - Bloodline') == (
        'Star Wars - Bloodline'
    )
    # The Dark Tower's English edition is filed as 'Dark Tower' — differs only
    # by the article, so the catalog title stays.
    assert p(['Dark Tower'], 'The dark tower') == 'The dark tower'
    # A genuine translation matches nothing English, so the edition wins.
    assert p(['Waste Lands'], 'A Torre Negra') == 'Waste Lands'
    assert p(['Catching Fire'], 'Fatta Eld') == 'Catching Fire'
    # Outlier edition names lose to the consensus.
    assert p(['Dune'] * 9 + ['Dune Ebook Collection'], 'Fremen') == 'Dune'
    assert p([], 'La Nuit') == 'La Nuit'


def test_plain_text_strips_markdown_link_spam():
    # Open Library descriptions are Markdown and openly editable; Rich Dad
    # Poor Dad's opened with a link to a PDF piracy site.
    raw = (
        '[<u>Rich Dad, Poor Dad PDF</u>](https://chesserresources.com/doc/x/)'
        '\\\r\n\\\r\nApril of 2022 marks a 25-year milestone.'
    )
    out = book_search._plain_text(raw)
    assert 'chesserresources' not in out
    assert '](' not in out and '<u>' not in out
    # The link was the whole line, so it goes entirely — keeping its text
    # left the description opening with 'Rich Dad, Poor Dad PDF'.
    assert out == 'April of 2022 marks a 25-year milestone.'


def test_plain_text_keeps_inline_link_wording():
    assert book_search._plain_text('See [the sequel](http://x.com) for more.') == (
        'See the sequel for more.'
    )


def test_google_poster_is_stable_and_short():
    long_thumb = (
        'http://books.google.com/books/publisher/content?id=0QaqDQAAQBAJ'
        '&printsec=frontcover&img=1&zoom=1&imgtk=' + 'A' * 200 + '&source=gbs_api'
    )
    url = book_search._google_poster({'imageLinks': {'thumbnail': long_thumb}})
    # Must fit the 254-char poster_url column, or it is stored truncated
    # into a broken link.
    assert len(url) < 254
    assert url.startswith('https://')
    assert 'id=0QaqDQAAQBAJ' in url
    assert 'imgtk' not in url


def test_genre_drops_translated_subjects():
    doc = {
        'subject': [
            'Succès',
            'Psychologie appliquée',
            'Succe  s.',
            'Applied Psychology',
            'Self-help',
        ]
    }
    assert book_search._genre(doc) == 'Applied Psychology, Self-help'
    assert book_search._genre({'subject': ['Succès']}) is None
    # 'Psychologie applique e.' is ASCII and single-spaced; the stray 'e.'
    # is what marks it as mangled.
    assert book_search._genre({'subject': ['Psychologie applique e.']}) is None
    # Real one-letter words must survive.
    assert book_search._genre({'subject': ['A history of art', 'I, Robot']}) == (
        'A history of art, I, Robot'
    )


@patch('app.services.book_search.requests.get')
def test_english_edition_prefers_english_cover_and_isbn(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        'entries': [
            # Foreign edition first — must not win the cover.
            {
                'languages': [{'key': '/languages/jpn'}],
                'covers': [999],
                'isbn_13': ['9784000000000'],
            },
            {
                'languages': [{'key': '/languages/eng'}],
                'covers': [15200981],
                'number_of_pages': 262,
                'title': 'How to Win Friends and Influence People',
            },
            {
                'languages': [{'key': '/languages/eng'}],
                'isbn_13': ['9780671027032'],
                'title': 'How to Win Friends and Influence People',
            },
            # An outlier edition name must not beat the consensus title.
            {
                'languages': [{'key': '/languages/eng'}],
                'title': 'How to Win Friends Ebook Collection',
            },
        ]
    }
    mock_get.return_value = resp

    edition = book_search._english_edition('/works/OL1063267W')
    assert edition['titles'] == [
        'How to Win Friends and Influence People',
        'How to Win Friends and Influence People',
        'How to Win Friends Ebook Collection',
    ]

    assert edition['cover_i'] == 15200981
    assert edition['page_count'] == 262
    # Exposed for the repair pass, but never applied by _openlibrary_detail:
    # the caller's ISBN is the row's identity.
    assert edition['isbn'] == '9780671027032'  # taken from a later entry


@patch('app.services.book_search.requests.get')
def test_english_edition_is_best_effort(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {'entries': [{'languages': [{'key': '/languages/fre'}]}]}
    mock_get.return_value = resp
    assert not book_search._english_edition('/works/OL1W')
    assert not book_search._english_edition(None)


@patch('app.services.book_search._google_books_detail')
@patch('app.services.book_search._openlibrary_detail')
def test_resolve_merges_google_into_partial_openlibrary_hit(mock_ol, mock_google):
    # One Mission resolved on Open Library as a title and nothing else.
    mock_ol.return_value = {
        'title': 'One Mission',
        'authors': None,
        'year': None,
        'rating': 4.1,
    }
    mock_google.return_value = {
        'title': 'One Mission (Google)',
        'authors': 'Chris Fussell',
        'year': 2017,
        'rating': None,
    }

    detail = book_search.resolve_book_detail('9780735211360', '0QaqDQAAQBAJ')

    assert detail['title'] == 'One Mission'  # Open Library wins where it has a value
    assert detail['authors'] == 'Chris Fussell'  # gap filled
    assert detail['year'] == 2017
    assert detail['rating'] == 4.1  # not clobbered by Google's None


@patch('app.services.book_search.requests.get')
def test_search_books_restricts_to_english(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {'docs': []}
    mock_get.return_value = resp

    book_search.search_books('dune')

    assert mock_get.call_args.kwargs['params']['language'] == 'eng'


def test_language_never_guesses_a_translation():
    # A work listing many editions must not assert one of them at random:
    # element 0 tagged The Stand 'rus' and The Da Vinci Code 'mal'.
    assert book_search._language({'language': ['rus', 'eng', 'ita']}) == 'eng'
    assert book_search._language({'language': ['rus', 'ita']}) is None
    assert book_search._language({'language': ['fre']}) == 'fre'
    assert book_search._language({}) is None


def test_apply_detail_keeps_the_title_the_user_chose():
    class Book:
        title = 'Welcome to the Jungle'
        authors = None

    b = Book()
    book_search.apply_detail_to_book(
        b, {'title': "Jim Butcher's the Dresden Files", 'authors': 'Jim Butcher'}
    )
    assert b.title == 'Welcome to the Jungle'  # selection wins on add
    assert b.authors == 'Jim Butcher'  # other fields still fill

    # The one-time backfill of legacy rows opts in explicitly.
    book_search.apply_detail_to_book(b, {'title': 'Renamed'}, overwrite_title=True)
    assert b.title == 'Renamed'


def test_apply_detail_truncates_and_skips_none():
    class Book:
        authors = None
        genre = None
        description = None

    b = Book()
    book_search.apply_detail_to_book(
        b, {'authors': 'x' * 999, 'genre': 'Fiction', 'description': None}
    )
    assert len(b.authors) == 512  # truncated to column limit
    assert b.genre == 'Fiction'
    assert b.description is None  # None values are skipped


@patch('app.services.book_search._work_description', return_value='A desert planet.')
@patch('app.services.book_search.requests.get')
def test_get_book_detail_maps_fields(mock_get, mock_desc):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        'docs': [
            {
                'key': '/works/OL893415W',
                'title': 'Dune',
                'author_name': ['Frank Herbert'],
                'first_publish_year': 1965,
                'number_of_pages_median': 592,
                'subject': ['Science fiction', 'Deserts'],
                'ratings_average': 4.2345,
                'language': ['eng', 'fre'],
                'cover_i': 11481354,
            }
        ]
    }
    mock_get.return_value = resp

    detail = book_search.get_book_detail('978-0441172719')
    assert detail['title'] == 'Dune'
    assert detail['isbn'] == '9780441172719'  # dashes stripped
    assert detail['authors'] == 'Frank Herbert'
    assert detail['year'] == 1965
    assert detail['genre'] == 'Science fiction, Deserts'
    assert detail['description'] == 'A desert planet.'
    assert detail['page_count'] == 592
    assert detail['rating'] == 4.23
    assert detail['language'] == 'eng'
    assert detail['poster_url'] == 'https://covers.openlibrary.org/b/id/11481354-L.jpg'
    mock_desc.assert_called_once_with('/works/OL893415W')


def test_get_book_detail_without_isbn_returns_none():
    assert book_search.get_book_detail(None) is None
    assert book_search.get_book_detail('') is None


@patch('app.services.book_search.requests.get', side_effect=requests.Timeout('slow'))
def test_get_book_detail_swallows_upstream_failure(_mock_get):
    # Public helper keeps its "None means no detail" contract for routers.
    assert book_search.get_book_detail('9780441172719') is None


@patch('app.services.book_search.requests.get', side_effect=requests.Timeout('slow'))
def test_openlibrary_detail_raises_on_upstream_failure(_mock_get):
    # Enrichment needs the failure to be distinguishable from a miss.
    with pytest.raises(book_search.UpstreamUnavailable):
        book_search._openlibrary_detail('9780441172719')


_GOOGLE_VOLUME = {
    'volumeInfo': {
        'title': 'The Phoenix Project',
        'authors': ['Gene Kim', 'Kevin Behr'],
        'publishedDate': '2014-10-15',
        'description': '<p>An <i>IT</i> novel.</p><p>Bill &amp; Brent.</p>',
        'industryIdentifiers': [
            {'type': 'ISBN_10', 'identifier': '1942788290'},
            {'type': 'ISBN_13', 'identifier': '9781942788294'},
        ],
        'pageCount': 348,
        'categories': [
            'Business & Economics / Management',
            'Business & Economics / General',
            'Computers / IT / Operations',
        ],
        'averageRating': 4.3456,
        'language': 'en',
        'imageLinks': {
            'thumbnail': 'http://books.google.com/books/content?id=X&edge=curl'
        },
    }
}


@patch('app.services.book_search.get_settings')
@patch('app.services.book_search.requests.get')
def test_google_books_detail_maps_fields(mock_get, mock_settings):
    mock_settings.return_value.google_books_api_key = 'AIzaTest'
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = _GOOGLE_VOLUME
    mock_get.return_value = resp

    detail = book_search.get_book_detail_by_googleid('_An-CAAAQBAJ')

    assert detail['title'] == 'The Phoenix Project'
    assert detail['isbn'] == '9781942788294'  # ISBN-13 preferred
    assert detail['authors'] == 'Gene Kim, Kevin Behr'
    assert detail['year'] == 2014  # parsed out of 'YYYY-MM-DD'
    # Hierarchical categories flattened, 'General' dropped, capped at 3.
    assert detail['genre'] == 'Business & Economics, Management, Computers'
    # HTML flattened to text so the web's {book.description} does not show tags.
    assert detail['description'] == 'An IT novel.\nBill & Brent.'
    assert detail['page_count'] == 348
    assert detail['rating'] == 4.35
    assert detail['language'] == 'en'
    # https, no page-curl overlay, rebuilt to the stable minimal form.
    assert detail['poster_url'] == (
        'https://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=1'
    )


@patch('app.services.book_search.get_settings')
def test_google_books_detail_skipped_without_key(mock_settings):
    mock_settings.return_value.google_books_api_key = None
    assert book_search.get_book_detail_by_googleid('_An-CAAAQBAJ') is None


@patch('app.services.book_search.get_settings')
@patch('app.services.book_search.requests.get')
def test_google_books_unknown_volume_is_a_miss_not_an_outage(mock_get, mock_settings):
    mock_settings.return_value.google_books_api_key = 'AIzaTest'
    resp = MagicMock()
    resp.status_code = 404
    mock_get.return_value = resp

    # 404 means "no such volume" — must not look like an upstream failure.
    assert book_search._google_books_detail('nope') is None
    resp.raise_for_status.assert_not_called()


@patch('app.services.book_search._google_books_detail')
@patch('app.services.book_search._openlibrary_detail')
def test_resolve_falls_back_to_googleid(mock_ol, mock_google):
    mock_ol.return_value = None  # Open Library has no record of this edition
    mock_google.return_value = {'title': 'The Phoenix Project'}

    detail = book_search.resolve_book_detail('9780307378026', '_An-CAAAQBAJ')

    assert detail == {'title': 'The Phoenix Project'}
    mock_google.assert_called_once_with('_An-CAAAQBAJ')


@patch('app.services.book_search._google_books_detail')
@patch('app.services.book_search._openlibrary_detail')
def test_resolve_keeps_openlibrary_values_over_google(mock_ol, mock_google):
    mock_ol.return_value = {'title': 'Dune', 'rating': 4.2}
    mock_google.return_value = {'title': 'Dune (movie tie-in)', 'rating': 3.0}
    detail = book_search.resolve_book_detail('9780441172719', 'abc')
    assert detail == {'title': 'Dune', 'rating': 4.2}


@patch('app.services.book_search._google_books_detail')
@patch('app.services.book_search._openlibrary_detail')
def test_resolve_skips_google_when_there_is_no_googleid(mock_ol, mock_google):
    mock_ol.return_value = {'title': 'Dune'}
    assert book_search.resolve_book_detail('9780441172719', None) == {'title': 'Dune'}
    mock_google.assert_not_called()


@patch('app.services.book_search._google_books_detail')
def test_resolve_handles_row_with_googleid_but_no_isbn(mock_google):
    mock_google.return_value = {'title': 'The Phoenix Project'}
    assert book_search.resolve_book_detail(None, '_An-CAAAQBAJ') is not None


@patch('app.services.book_search._openlibrary_detail')
def test_resolve_propagates_upstream_failure(mock_ol):
    mock_ol.side_effect = book_search.UpstreamUnavailable('boom')
    with pytest.raises(book_search.UpstreamUnavailable):
        book_search.resolve_book_detail('9780441172719', 'abc')


def test_work_description_unwraps_dict():
    with patch('app.services.book_search.requests.get') as mock_get:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {'description': {'value': 'Nested text.'}}
        mock_get.return_value = resp
        assert book_search._work_description('/works/OL1W') == 'Nested text.'
    assert book_search._work_description(None) is None


_BIBKEYS_PAYLOAD = {
    'ISBN:9780441172719': {
        'title': 'Dune',
        'authors': [{'name': 'Frank Herbert'}],
        'publish_date': 'August 3, 1990',
        'identifiers': {
            'isbn_10': ['0441172717'],
            'isbn_13': ['9780441172719'],
        },
        'cover': {
            'small': 'https://covers.openlibrary.org/b/id/1-S.jpg',
            'medium': 'https://covers.openlibrary.org/b/id/1-M.jpg',
            'large': 'https://covers.openlibrary.org/b/id/1-L.jpg',
        },
    }
}


@patch('app.services.book_search.requests.get')
def test_search_books_hyphenated_isbn_resolves_via_bibkeys(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _BIBKEYS_PAYLOAD
    mock_get.return_value = resp

    results = book_search.search_books('978-0-441-17271-9')

    assert results == [
        {
            'isbn': '9780441172719',
            'title': 'Dune',
            'authors': 'Frank Herbert',
            'year': '1990',
            'poster_url': 'https://covers.openlibrary.org/b/id/1-L.jpg',
        }
    ]
    args, kwargs = mock_get.call_args
    assert args[0] == 'https://openlibrary.org/api/books'
    assert kwargs['params']['bibkeys'] == 'ISBN:9780441172719'


@patch('app.services.book_search.requests.get')
def test_search_books_bare_isbn_resolves_via_bibkeys(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _BIBKEYS_PAYLOAD
    mock_get.return_value = resp

    results = book_search.search_books('9780441172719')

    assert len(results) == 1
    assert results[0]['isbn'] == '9780441172719'
    assert results[0]['title'] == 'Dune'


@patch('app.services.book_search.requests.get')
def test_search_books_unknown_isbn_returns_empty_list(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {}
    mock_get.return_value = resp

    assert not book_search.search_books('0000000000')


@patch('app.services.book_search.requests.get')
def test_search_books_title_query_unaffected(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        'docs': [
            {
                'title': 'Dune',
                'author_name': ['Frank Herbert'],
                'first_publish_year': 1965,
                'isbn': ['9780441172719'],
                'cover_i': 11481354,
            }
        ]
    }
    mock_get.return_value = resp

    results = book_search.search_books('Dune')

    assert results == [
        {
            'isbn': '9780441172719',
            'title': 'Dune',
            'authors': 'Frank Herbert',
            'year': '1965',
            'poster_url': 'https://covers.openlibrary.org/b/id/11481354-L.jpg',
        }
    ]
    args, kwargs = mock_get.call_args
    assert args[0] == 'https://openlibrary.org/search.json'
    assert kwargs['params']['q'] == 'Dune'
