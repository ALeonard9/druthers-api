"""
Book search proxy.

Wraps the Open Library API (https://openlibrary.org — free, no key) so the
web and MCP frontends can look up books by title/author without talking to
Open Library directly. Results are normalized into the shape the
``/v1/books`` create endpoint expects. Open Library is also the enrichment
source, keyed on the catalog's ``isbn``: authors, publish year, subjects,
description (from the work record), page count, rating, and cover image.

Open Library does not index every edition. Rows imported from Google Books
carry edition-specific ISBNs it has no record of, so the legacy ``googleid``
column is used as an enrichment fallback (see ``resolve_book_detail``). That
path needs ``GOOGLE_BOOKS_API_KEY``; without it those rows are simply skipped.
"""

import re
from html import unescape
from typing import List, Optional

import requests
from fastapi import HTTPException, status

from app.config import get_settings
from app.log.logging_config import logger

OPENLIBRARY_URL = 'https://openlibrary.org'
COVERS_URL = 'https://covers.openlibrary.org'
GOOGLE_BOOKS_URL = 'https://www.googleapis.com/books/v1'
REQUEST_TIMEOUT = 10


class UpstreamUnavailable(Exception):
    """
    An upstream book source could not be reached (transport error, HTTP
    error, unparseable body).

    Distinct from "the source answered, and has no record of this book".
    Enrichment needs to tell those apart: the first is worth backing off
    over, the second will never succeed no matter how long we wait.
    """


_SEARCH_FIELDS = (
    'key,title,author_name,first_publish_year,isbn,cover_i,'
    'number_of_pages_median,subject,ratings_average,language'
)

# ISBN-10 (last check digit may be 'X') or ISBN-13, after stripping any
# hyphens/spaces a user may have typed.
_ISBN_RE = re.compile(r'^(?:\d{13}|\d{9}[\dXx])$')


def _cover(cover_i: Optional[int]) -> Optional[str]:
    return f'{COVERS_URL}/b/id/{cover_i}-L.jpg' if cover_i else None


def _authors(doc: dict) -> Optional[str]:
    authors = doc.get('author_name') or []
    return ', '.join(authors) if authors else None


def _pick_isbn(doc: dict) -> Optional[str]:
    """Prefer an ISBN-13 from the doc's (unordered) isbn list."""
    isbns = doc.get('isbn') or []
    for isbn in isbns:
        if len(isbn) == 13:
            return isbn
    return isbns[0] if isbns else None


# A single-letter word (optionally with a trailing period) that is not a real
# English one-letter word — the residue of a mangled transliteration.
_STRAY_LETTER_RE = re.compile(r'(?<!\S)(?![aAiI](?!\w))[b-zB-Z]\.?(?!\S)')


def _genre(doc: dict) -> Optional[str]:
    """
    Subjects, restricted to the English-looking ones.

    Subjects are work-level, so translations contribute theirs: How to Win
    Friends offered 'Succes', 'Psychologie appliquee', 'Applied Psychology'.
    Non-ASCII marks a translated subject. The rest of the noise is mangled
    transliteration, which shows up as a stray one-letter token or a doubled
    space ('Succe  s.', 'Psychologie applique e.'). Drop those and keep the
    rest; when nothing survives, the Google fallback supplies categories.
    """
    subjects = [
        s
        for s in (doc.get('subject') or [])
        if s.isascii() and '  ' not in s and not _STRAY_LETTER_RE.search(s)
    ]
    return ', '.join(subjects[:3]) if subjects else None


# A trailing parenthetical is decoration, not the title: series and volume
# ('White Night (The Dresden Files, Book 9)'), award badges ('A Thousand
# Acres (Pulitzer Prize Winner)'), edition notes. Repeated for every book in
# a series it is pure noise, and series already has a home in `genre`.
_TRAILING_PAREN_RE = re.compile(r'\s*[(\[][^)\]]*[)\]]\s*$')
# Trailing edition/packaging qualifiers, which name a printing rather than the
# book: Google gave 'The Scorch Trials Movie Tie-in Edition', Open Library
# 'Dune Ebook Collection'.
# Only strip words that actually qualify a printing. A wildcard run of words
# before 'Edition' eats real title words ('The Scorch Trials Movie Tie-in
# Edition' lost 'Trials').
_EDITION_QUALIFIER = (
    r'(?:movie|film|tv|deluxe|anniversary|illustrated|special|revised'
    r"|expanded|collector'?s|annotated|reissue|international|ebook|e-book"
    r'|mass[\s-]market|paperback|hardcover|tie[\s-]?in|\d+(?:st|nd|rd|th))'
)
_EDITION_SUFFIX_RE = re.compile(
    rf'\s*[:,-]?\s*(?:{_EDITION_QUALIFIER}[\s-]+)+(?:edition|collection|set)\s*$',
    re.IGNORECASE,
)
# Words that stay lowercase inside a title.
_MINOR_WORDS = frozenset(
    'a an the and but or nor for so yet at by in of on to up via from with as'.split()
)


def _title_case(title: str) -> str:
    """
    Normalize casing for titles stored sentence-cased ('The dark tower',
    'Jim Butcher's the Dresden files').

    Only touches words that are entirely lowercase, so deliberate casing is
    preserved: acronyms stay upper, and names like 'iRobot' or 'McKay' keep
    their internal capitals.
    """
    words = title.split(' ')
    out = []
    for index, word in enumerate(words):
        if word.islower() and (
            index not in (0, len(words) - 1) and word in _MINOR_WORDS
        ):
            out.append(word)
        elif word.islower():
            out.append(word[:1].upper() + word[1:])
        else:
            out.append(word)
    return ' '.join(out)


def normalize_title(title: Optional[str]) -> Optional[str]:
    """
    Tidy a catalog title: drop the trailing parenthetical and give it
    consistent capitalization.
    """
    if not title:
        return title
    cleaned = _TRAILING_PAREN_RE.sub('', title.strip())
    trimmed = _EDITION_SUFFIX_RE.sub('', cleaned)
    # Only accept the trim if a real title survives it — 'The Complete
    # Collection' must not become 'The'.
    if trimmed and any(w.lower() not in _MINOR_WORDS for w in trimmed.split()):
        cleaned = trimmed
    # Never normalize a title away entirely.
    cleaned = cleaned or title.strip()
    return _title_case(cleaned)


def _title_key(title: str) -> str:
    """Comparison key: casing, punctuation and spacing carry no meaning here."""
    return re.sub(r'[^a-z0-9]+', ' ', title.casefold()).strip()


def _pick_title(edition_titles: list, work_title: Optional[str]) -> Optional[str]:
    """
    Choose between the work's title and its English editions'.

    The work title is only wrong when it came from a translated edition
    ('Fatta Eld' for Catching Fire). If it appears among the English editions
    at all, it is a legitimate English title and stays — otherwise the most
    common edition title wins a work like Order of the Phoenix, whose
    editions are catalogued 8x as the bare series name 'Harry Potter' against
    6x for the actual book.

    Falls back to the most common edition title, ties to the shortest, which
    filters outliers like 'Dune Ebook Collection'.
    """
    if not edition_titles:
        return work_title
    if not work_title:
        return max(
            set(edition_titles), key=lambda t: (edition_titles.count(t), -len(t))
        )

    core = _TRAILING_PAREN_RE.sub('', work_title.strip()) or work_title.strip()
    keys = {_title_key(t) for t in edition_titles}
    # Also matches when the work title only differs by a leading article:
    # The Dark Tower's English edition is filed as 'Dark Tower'.
    stripped = re.sub(r'^(?:the|a|an)\s+', '', core, flags=re.IGNORECASE)
    if _title_key(core) in keys or _title_key(stripped) in keys:
        return core
    return max(set(edition_titles), key=lambda t: (edition_titles.count(t), -len(t)))


def _is_english_edition(entry: dict) -> bool:
    return any(
        '/languages/eng' in (lang.get('key') or '')
        for lang in (entry.get('languages') or [])
    )


def _english_edition(work_key: Optional[str]) -> dict:
    """
    Best English edition of an Open Library work: its cover, ISBN and page
    count.

    The work-level search doc aggregates every edition, so ``cover_i`` is
    whichever edition Open Library happened to surface — for How to Win
    Friends that was a foreign-language jacket. Editions carry a real
    language, so pick from those instead.

    Best effort: returns ``{}`` when the call fails or nothing is English,
    leaving the caller on the work-level values.
    """
    if not work_key or not _WORK_KEY_RE.match(work_key):
        return {}
    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}{work_key}/editions.json',
            params={'limit': 50},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        entries = response.json().get('entries') or []
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Open Library editions fetch failed for %s: %s', work_key, exc)
        return {}

    english = [e for e in entries if _is_english_edition(e)]
    if not english:
        return {}
    # No single edition reliably has everything, so take each field from the
    # first English edition that has it.
    result = {}
    for entry in english:
        covers = [c for c in (entry.get('covers') or []) if c and c > 0]
        if covers and 'cover_i' not in result:
            result['cover_i'] = covers[0]
        isbns = entry.get('isbn_13') or entry.get('isbn_10') or []
        if isbns and 'isbn' not in result:
            result['isbn'] = isbns[0]
        if entry.get('number_of_pages') and 'page_count' not in result:
            result['page_count'] = entry['number_of_pages']
    # Every English edition title, for _pick_title to choose among.
    result['titles'] = [e['title'] for e in english if e.get('title')]
    return result


def _language(doc: dict) -> Optional[str]:
    """
    Pick the language of the *work*, which Open Library reports as the union
    of every edition's language.

    Taking element 0 is close to arbitrary — it tagged The Stand 'rus' and
    The Da Vinci Code 'mal'. Only claim a language when the work has exactly
    one, or when English is among them (this catalog is English-language);
    otherwise say nothing rather than assert a translation at random.
    """
    languages = doc.get('language') or []
    if len(languages) == 1:
        return languages[0]
    return 'eng' if 'eng' in languages else None


def _bibkeys_authors(data: dict) -> Optional[str]:
    authors = data.get('authors') or []
    names = [a.get('name') for a in authors if a.get('name')]
    return ', '.join(names) if names else None


def _bibkeys_year(data: dict) -> Optional[str]:
    publish_date = data.get('publish_date')
    if not publish_date:
        return None
    match = re.search(r'\d{4}', publish_date)
    return match.group(0) if match else None


def _bibkeys_poster(data: dict) -> Optional[str]:
    cover = data.get('cover') or {}
    return cover.get('large') or cover.get('medium') or cover.get('small')


def _bibkeys_isbn(data: dict, requested: str) -> str:
    """Prefer an ISBN-13 from the record's identifiers, falling back to
    whatever the caller searched for."""
    identifiers = data.get('identifiers') or {}
    isbn_13 = identifiers.get('isbn_13') or []
    isbn_10 = identifiers.get('isbn_10') or []
    if isbn_13:
        return isbn_13[0]
    if isbn_10:
        return isbn_10[0]
    return requested


def _search_by_isbn(isbn: str) -> List[dict]:
    """
    Resolve a single ISBN via Open Library's bibkeys API, which returns
    title/authors/publish_date/cover in one call (avoiding a second
    round-trip to resolve author names from a work reference, unlike the
    ``/isbn/{isbn}.json`` edition endpoint).

    Returns ``[]`` when the ISBN doesn't resolve to a known edition — the
    same "no matches" shape a title search produces, not an error.
    """
    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}/api/books',
            params={
                'bibkeys': f'ISBN:{isbn}',
                'format': 'json',
                'jscmd': 'data',
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Open Library ISBN lookup failed for %r: %s', isbn, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream book search failed',
        ) from exc

    data = payload.get(f'ISBN:{isbn}')
    if not data:
        return []

    return [
        {
            'isbn': _bibkeys_isbn(data, isbn),
            'title': data.get('title'),
            'authors': _bibkeys_authors(data),
            'year': _bibkeys_year(data),
            'poster_url': _bibkeys_poster(data),
        }
    ]


def search_books(query: str) -> List[dict]:
    """
    Search Open Library for books matching ``query``.

    Returns a list of normalized dicts (``isbn``, ``title``, ``authors``,
    ``year``, ``poster_url``). Raises 400 on an empty query and 502 when the
    upstream call fails.

    When ``query`` looks like an ISBN (10 or 13 digits, optionally
    hyphenated), it's resolved directly via Open Library's ISBN lookup
    instead of a title search.
    """
    query = (query or '').strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Search query must not be empty',
        )

    normalized_isbn = re.sub(r'[-\s]', '', query)
    if _ISBN_RE.match(normalized_isbn):
        return _search_by_isbn(normalized_isbn)

    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}/search.json',
            params={
                'q': query,
                'limit': 20,
                'fields': _SEARCH_FIELDS,
                # Restrict to works that have an English edition. This is an
                # English-language catalog, and unfiltered title searches
                # surface translations ahead of the edition the user means.
                'language': 'eng',
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Open Library search failed for %r: %s', query, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream book search failed',
        ) from exc

    results = []
    for doc in payload.get('docs') or []:
        year = doc.get('first_publish_year')
        results.append(
            {
                'isbn': _pick_isbn(doc),
                'title': doc.get('title'),
                'authors': _authors(doc),
                'year': str(year) if year else None,
                'poster_url': _cover(doc.get('cover_i')),
            }
        )
    return results


# Max lengths for the bounded catalog columns (see models_sandbox.DbBook).
_FIELD_LIMITS = {
    'title': 254,
    'isbn': 20,
    'poster_url': 254,
    'authors': 512,
    'genre': 255,
    'language': 40,
}


def apply_detail_to_book(book, detail: dict, overwrite_title: bool = False) -> None:
    """
    Copy Open Library detail onto a DbBook, truncating to column limits and
    skipping None values (never clobber a good value with None).

    A title the book already has is left alone. Someone adding a book picked
    a specific edition from search results, and overwriting their choice with
    whatever Open Library calls the work is how the catalog ended up with
    'Jim Butcher's the Dresden Files' for Welcome to the Jungle. Only the
    one-time backfill of legacy rows passes ``overwrite_title``.
    """
    for key, value in detail.items():
        if value is None:
            continue
        if key == 'title' and getattr(book, 'title', None) and not overwrite_title:
            continue
        if key in _FIELD_LIMITS and isinstance(value, str):
            value = value[: _FIELD_LIMITS[key]]
        setattr(book, key, value)


# Open Library work keys look like "/works/OL45883W". Anything else from the
# search response is discarded before it can reach a URL (SSRF hardening).
_WORK_KEY_RE = re.compile(r'^/works/OL\d+W$')


def _work_description(work_key: Optional[str]) -> Optional[str]:
    """Fetch the work record for its description (best effort)."""
    if not work_key or not _WORK_KEY_RE.match(work_key):
        return None
    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}{work_key}.json', timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        description = response.json().get('description')
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Open Library work fetch failed for %s: %s', work_key, exc)
        return None
    if isinstance(description, dict):
        description = description.get('value')
    return _plain_text(description)


def _openlibrary_detail(isbn: str) -> Optional[dict]:
    """
    Open Library detail for an ISBN, in catalog shape.

    Returns None when Open Library has no record of the ISBN; raises
    ``UpstreamUnavailable`` when the call itself failed.
    """
    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}/search.json',
            params={'q': f'isbn:{isbn}', 'limit': 1, 'fields': _SEARCH_FIELDS},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        docs = response.json().get('docs') or []
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Open Library detail failed for %s: %s', isbn, exc)
        raise UpstreamUnavailable(str(exc)) from exc
    if not docs:
        return None

    doc = docs[0]
    year = doc.get('first_publish_year')
    rating = doc.get('ratings_average')
    # Cover/pages come from an English edition where one exists, rather than
    # from the work's arbitrary pick across all translations. Note isbn is
    # deliberately *not* taken from that edition: the caller's ISBN is the
    # row's identity (router_books dedupes on it), so swapping it here would
    # silently store something other than what was asked for. Repointing a
    # row at an English edition is a data repair, not a lookup side effect.
    edition = _english_edition(doc.get('key'))
    return {
        'title': normalize_title(
            _pick_title(edition.get('titles') or [], doc.get('title'))
        ),
        'isbn': isbn,
        'authors': _authors(doc),
        'year': year,
        'genre': _genre(doc),
        'description': _work_description(doc.get('key')),
        'page_count': edition.get('page_count') or doc.get('number_of_pages_median'),
        'rating': round(rating, 2) if rating else None,
        'language': 'eng' if edition else _language(doc),
        'poster_url': _cover(edition.get('cover_i') or doc.get('cover_i')),
    }


def get_book_detail(isbn: Optional[str]) -> Optional[dict]:
    """
    Fetch full detail for a book by ISBN and map it to the fields the
    catalog stores. Returns None when unavailable so callers can skip
    enrichment gracefully.
    """
    isbn = (isbn or '').strip().replace('-', '')
    if not isbn:
        return None
    try:
        return _openlibrary_detail(isbn)
    except UpstreamUnavailable:
        return None


def _google_isbn(volume: dict) -> Optional[str]:
    """Prefer an ISBN-13 from the volume's industry identifiers."""
    identifiers = volume.get('industryIdentifiers') or []
    by_type = {i.get('type'): i.get('identifier') for i in identifiers}
    return by_type.get('ISBN_13') or by_type.get('ISBN_10')


def _google_year(volume: dict) -> Optional[int]:
    """publishedDate is any of 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD'."""
    match = re.search(r'\d{4}', volume.get('publishedDate') or '')
    return int(match.group(0)) if match else None


_GOOGLE_ID_RE = re.compile(r'[?&]id=([^&]+)')


def _google_poster(volume: dict) -> Optional[str]:
    """
    Stable https cover URL for a Google volume.

    The raw thumbnail is http (blocked as mixed content on an https site),
    carries a page-curl overlay, and trails a long ``imgtk`` signature that
    pushes it past the 254-char poster_url column — where it gets truncated
    into a broken link. Rebuild the minimal form instead of patching the
    given one.
    """
    links = volume.get('imageLinks') or {}
    thumbnail = links.get('thumbnail') or links.get('smallThumbnail')
    if not thumbnail:
        return None
    match = _GOOGLE_ID_RE.search(thumbnail)
    if not match:
        return thumbnail.replace('http://', 'https://').replace('&edge=curl', '')
    return (
        f'https://books.google.com/books/content?id={match.group(1)}'
        '&printsec=frontcover&img=1&zoom=1'
    )


_BLOCK_TAG_RE = re.compile(r'</(?:p|div|li|h[1-6])\s*>|<br\s*/?>', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
# Open Library descriptions are Markdown, and are open to edit by anyone —
# link spam shows up in them. Rich Dad Poor Dad's opened with a Markdown link
# to a PDF piracy site. Keep the link text, drop the destination.
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')
# A whole line that is just a Markdown link (with optional trailing hard-break
# backslashes) — the shape the spam takes.
_STANDALONE_LINK_RE = re.compile(r'\\*\[[^\]]*\]\([^)]*\)\\*')
_BARE_URL_RE = re.compile(r'\bhttps?://\S+')
# Markdown hard line breaks arrive as a literal backslash before the newline.
_ESCAPED_BREAK_RE = re.compile(r'\\+\r?\n')


def _plain_text(markup: Optional[str]) -> Optional[str]:
    """
    Flatten a description to plain text.

    Both sources need this, for different reasons: Google Books returns HTML,
    Open Library returns Markdown. The web renders the column as text
    (BookDetail renders ``{book.description}``, not
    ``dangerouslySetInnerHTML``), so either one's markup shows up literally.
    Keeping the column plain also avoids storing third-party markup that some
    later renderer might decide to trust.

    URLs are dropped rather than kept as text: they are almost always spam in
    this data, and a bare link in a catalog description is noise at best.
    """
    if not markup:
        return None
    text = _ESCAPED_BREAK_RE.sub('\n', markup)
    # A line that is nothing but a link is navigation or spam, so drop it
    # whole. Inline links keep their words, which are part of the prose.
    text = '\n'.join(
        line
        for line in text.split('\n')
        if not _STANDALONE_LINK_RE.fullmatch(line.strip())
    )
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _BLOCK_TAG_RE.sub('\n', text)
    text = _TAG_RE.sub('', text)
    text = unescape(text)
    text = _BARE_URL_RE.sub('', text)
    # Collapse the run of blank lines the block substitution can leave behind.
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip() or None


def _google_genre(volume: dict) -> Optional[str]:
    """
    Flatten Google's hierarchical categories into the same comma-separated
    shape Open Library subjects use, so the column stays filterable.

    'Juvenile Fiction / Action & Adventure / General' carries three segments,
    of which 'General' is filler. Split on the path separator, drop the
    filler, de-duplicate across categories, keep the first three.
    """
    segments = []
    for category in volume.get('categories') or []:
        for segment in category.split('/'):
            segment = segment.strip()
            if segment and segment != 'General' and segment not in segments:
                segments.append(segment)
    return ', '.join(segments[:3]) if segments else None


def _google_books_detail(googleid: str) -> Optional[dict]:
    """
    Google Books detail for a volume id, in catalog shape.

    Returns None when the key is unset or the volume is unknown; raises
    ``UpstreamUnavailable`` when the call itself failed. Note that Google
    answers an unknown volume id with 404, which is a "no record" result
    rather than an outage — hence the explicit check before raising.
    """
    api_key = get_settings().google_books_api_key
    if not api_key:
        logger.info('GOOGLE_BOOKS_API_KEY unset; skipping %s', googleid)
        return None
    try:
        response = requests.get(
            f'{GOOGLE_BOOKS_URL}/volumes/{googleid}',
            params={'key': api_key},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        response.raise_for_status()
        volume = response.json().get('volumeInfo') or {}
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Google Books detail failed for %s: %s', googleid, exc)
        raise UpstreamUnavailable(str(exc)) from exc
    if not volume:
        return None

    authors = volume.get('authors') or []
    rating = volume.get('averageRating')
    return {
        'title': normalize_title(volume.get('title')),
        'isbn': _google_isbn(volume),
        'authors': ', '.join(authors) if authors else None,
        'year': _google_year(volume),
        'genre': _google_genre(volume),
        'description': _plain_text(volume.get('description')),
        'page_count': volume.get('pageCount'),
        'rating': round(rating, 2) if rating else None,
        'language': volume.get('language'),
        'poster_url': _google_poster(volume),
    }


def get_book_detail_by_googleid(googleid: Optional[str]) -> Optional[dict]:
    """Google Books counterpart to ``get_book_detail``, keyed on volume id."""
    googleid = (googleid or '').strip()
    if not googleid:
        return None
    try:
        return _google_books_detail(googleid)
    except UpstreamUnavailable:
        return None


def resolve_book_detail(
    isbn: Optional[str], googleid: Optional[str] = None
) -> Optional[dict]:
    """
    Best-available detail for a catalog row: Open Library by ISBN first,
    then Google Books by ``googleid``.

    Unlike ``get_book_detail`` this propagates ``UpstreamUnavailable`` so
    enrichment can back off on a genuine outage instead of counting it as a
    book that will never resolve. Returns None only when a source actually
    answered and had no record.
    """
    isbn = (isbn or '').strip().replace('-', '')
    googleid = (googleid or '').strip()
    detail = _openlibrary_detail(isbn) if isbn else None
    if not googleid:
        return detail

    fallback = _google_books_detail(googleid)
    if not detail:
        return fallback
    if not fallback:
        return detail
    # Open Library wins where it has a value — better ratings coverage, and
    # first-publish year rather than this printing's date — but it answers
    # with partial records (One Mission came back as a title and nothing
    # else), so let Google fill the gaps rather than discarding it.
    return {
        key: value if value is not None else fallback.get(key)
        for key, value in detail.items()
    }
