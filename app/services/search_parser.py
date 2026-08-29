import re
from typing import Dict, List, Tuple

_FILTER_RE = re.compile(r'\b([a-zA-Z0-9_]+):([^\s]*)')

PROVIDER_KEYS = {
    'movies': ['director', 'genre', 'year', 'primary_release_year'],
    'tv_shows': ['genre', 'network', 'language', 'status'],
    'games': ['genre', 'platform', 'theme'],
    'books': ['author', 'subject', 'publisher', 'isbn'],
}


def parse_provider_query(q: str, domain: str) -> Tuple[str, Dict[str, str]]:
    """
    Extract recognized key:value pairs from the query for a given domain.
    Unrecognized keys are left in the query text as literal search terms.
    """
    if not q:
        return q, {}

    recognized_keys = PROVIDER_KEYS.get(domain, [])
    filters = {}

    def replacer(match):
        key, value = match.groups()
        if key in recognized_keys:
            filters[key] = value
            return ''  # Remove from query
        return match.group(0)  # Keep in query

    new_q = _FILTER_RE.sub(replacer, q)
    # clean up extra whitespace
    new_q = re.sub(r'\s+', ' ', new_q).strip()
    return new_q, filters
