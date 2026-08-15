#!/usr/bin/env python3
"""
Generate a Postman Collection v2.1 JSON file from the repo's OpenAPI schema.

Why a hand-rolled converter instead of a third-party package: this is a
one-off, occasionally-run doc-generation step (not a runtime dependency), and
the OpenAPI surface here is simple enough (JSON bodies, bearer auth, path/
query params) that a ~300-line script covers it without adding a new
dependency to `requirements/`.

Usage:
    python scripts/generate_postman_collection.py \\
        [--openapi openapi.json] [--out docs/druthers-api.postman_collection.json]

Re-run this after any change to the API surface (new/changed routes) and
commit the regenerated file - it is a static, versioned artifact, not
generated at request time.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Optional

# Production is the default so the collection works out of the box for a
# customer who has never run the API locally. Override `baseUrl` in a
# Postman environment to point at localhost or QA instead.
DEFAULT_BASE_URL = 'https://api.druthers.io'

# Endpoints that must NOT inherit the collection's bearer-token auth (you
# don't have a token yet when calling them).
NO_AUTH_OPERATIONS = {
    ('post', '/v1/auth/token'),
    ('post', '/v1/auth/google'),
    ('post', '/v1/users'),
    ('get', '/'),
}


def load_schema(path: Path) -> dict:
    """Load and parse the OpenAPI JSON schema from disk."""
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def resolve_ref(schema: dict, ref: str) -> dict:
    """Resolve a local ``#/components/...`` JSON reference."""
    node: Any = schema
    for part in ref.lstrip('#/').split('/'):
        node = node[part]
    return node


# A schema-to-example walker necessarily branches on every JSON Schema/
# OpenAPI construct it supports (object/array/string/enum/anyOf/$ref/...) -
# splitting it up would scatter one cohesive concept across several
# functions for no readability gain.
# pylint: disable=too-many-return-statements,too-many-branches
def example_for_schema(
    schema: dict, root: dict, depth: int = 0, seen: Optional[set] = None
) -> Any:
    """
    Build a small illustrative example value for an OpenAPI schema node.

    Depth-limited and cycle-guarded (via `seen` ref names) so self-referential
    or deeply nested schemas degrade to `{}`/`null` instead of recursing
    forever - this only needs to be "good enough to show request shape", not
    a full example generator.
    """
    if seen is None:
        seen = set()
    if depth > 6:
        return None

    if '$ref' in schema:
        ref = schema['$ref']
        if ref in seen:
            return None
        resolved = resolve_ref(root, ref)
        return example_for_schema(resolved, root, depth + 1, seen | {ref})

    if 'example' in schema:
        return schema['example']
    if schema.get('default') is not None:
        return schema['default']
    if 'enum' in schema and schema['enum']:
        return schema['enum'][0]

    # anyOf/oneOf (commonly Optional[...] in FastAPI) -> first non-null branch
    for combiner in ('anyOf', 'oneOf'):
        if combiner in schema:
            for sub in schema[combiner]:
                if sub.get('type') == 'null':
                    continue
                return example_for_schema(sub, root, depth + 1, seen)
            return None

    schema_type = schema.get('type')
    fmt = schema.get('format')

    if schema_type == 'object' or 'properties' in schema:
        props = schema.get('properties', {})
        required = set(schema.get('required', []))
        out = {}
        for name, sub in props.items():
            # Keep bodies short and focused: required fields always, a couple
            # of optional ones for context, so the request is still readable.
            if required and name not in required and len(out) >= len(required) + 2:
                continue
            out[name] = example_for_schema(sub, root, depth + 1, seen)
        return out

    if schema_type == 'array':
        item_schema = schema.get('items', {})
        return [example_for_schema(item_schema, root, depth + 1, seen)]

    if schema_type == 'string':
        if fmt == 'date-time':
            return '2026-01-01T00:00:00Z'
        if fmt == 'date':
            return '2026-01-01'
        if fmt == 'email':
            return 'you@example.com'
        return 'string'
    if schema_type == 'integer':
        return 0
    if schema_type == 'number':
        return 0
    if schema_type == 'boolean':
        return True

    return None


def split_path(path: str) -> list[str]:
    """``/v1/movies/{movie_id}`` -> ``['v1', 'movies', ':movie_id']``."""
    segments = []
    for seg in path.strip('/').split('/'):
        if seg.startswith('{') and seg.endswith('}'):
            segments.append(':' + seg[1:-1])
        else:
            segments.append(seg)
    return segments


def build_url(path: str, params: list[dict]) -> dict:
    """Build a Postman `url` object (raw + path segments + variables/query)."""
    segments = split_path(path)
    path_params = [p for p in params if p.get('in') == 'path']
    query_params = [p for p in params if p.get('in') == 'query']

    raw = '{{baseUrl}}/' + '/'.join(segments)
    query = [
        {
            'key': p['name'],
            'value': str(p.get('schema', {}).get('example', '')),
            'description': p.get('description', ''),
            'disabled': not p.get('required', False),
        }
        for p in query_params
    ]
    if query:
        raw += '?' + '&'.join(f'{q["key"]}={q["value"]}' for q in query)

    url: dict = {
        'raw': raw,
        'host': ['{{baseUrl}}'],
        'path': segments,
    }
    if query:
        url['query'] = query
    if path_params:
        url['variable'] = [
            {
                'key': p['name'],
                'value': '',
                'description': p.get('description', ''),
            }
            for p in path_params
        ]
    return url


def build_request_body(operation: dict, root: dict) -> Optional[dict]:
    """Build a Postman `body` object from an operation's requestBody, if any."""
    request_body = operation.get('requestBody')
    if not request_body:
        return None
    content = request_body.get('content', {})

    if 'application/json' in content:
        schema = content['application/json'].get('schema', {})
        example = example_for_schema(schema, root)
        return {
            'mode': 'raw',
            'raw': json.dumps(example, indent=2),
            'options': {'raw': {'language': 'json'}},
        }

    if 'application/x-www-form-urlencoded' in content:
        schema = content['application/x-www-form-urlencoded'].get('schema', {})
        example = example_for_schema(schema, root) or {}
        return {
            'mode': 'urlencoded',
            'urlencoded': [
                {'key': k, 'value': str(v) if v is not None else '', 'type': 'text'}
                for k, v in example.items()
            ],
        }

    if 'multipart/form-data' in content:
        schema = content['multipart/form-data'].get('schema', {})
        example = example_for_schema(schema, root) or {}
        return {
            'mode': 'formdata',
            'formdata': [
                {'key': k, 'value': str(v) if v is not None else '', 'type': 'text'}
                for k, v in example.items()
            ],
        }

    return None


def build_request(method: str, path: str, operation: dict, root: dict) -> dict:
    """Build a single Postman collection `item` (one request) for an operation."""
    params = operation.get('parameters', [])
    headers = []
    body = build_request_body(operation, root)
    if body and body['mode'] == 'raw':
        headers.append({'key': 'Content-Type', 'value': 'application/json'})

    request: dict = {
        'method': method.upper(),
        'header': headers,
        'url': build_url(path, params),
    }
    if body:
        request['body'] = body
    if operation.get('description') or operation.get('summary'):
        request['description'] = operation.get('description') or operation['summary']

    if (method.lower(), path) in NO_AUTH_OPERATIONS:
        request['auth'] = {'type': 'noauth'}

    item: dict = {
        'name': operation.get('summary')
        or operation.get('operationId')
        or f'{method.upper()} {path}',
        'request': request,
    }
    return item


def build_collection(schema: dict) -> dict:
    """Build the full Postman Collection v2.1 document from an OpenAPI schema."""
    info = schema.get('info', {})
    tag_descriptions = {
        t['name']: t.get('description', '') for t in schema.get('tags', [])
    }

    folders: dict[str, list[dict]] = {}
    untagged: list[dict] = []

    for path, methods in schema.get('paths', {}).items():
        for method, operation in methods.items():
            if method.lower() not in (
                'get',
                'post',
                'put',
                'patch',
                'delete',
                'options',
                'head',
            ):
                continue
            item = build_request(method, path, operation, schema)
            tags = operation.get('tags') or []
            if tags:
                folders.setdefault(tags[0], []).append(item)
            else:
                untagged.append(item)

    items = []
    for tag_name in sorted(folders):
        folder_items = sorted(folders[tag_name], key=lambda it: it['name'])
        items.append(
            {
                'name': tag_name,
                'description': tag_descriptions.get(tag_name, ''),
                'item': folder_items,
            }
        )
    items.extend(sorted(untagged, key=lambda it: it['name']))

    description = (
        (info.get('description') or '')
        + '\n\n---\n\n'
        + '**Auth:** most requests use the collection-level bearer token - set '
        'the `apiToken` variable to a personal API key (`drk_…`), minted at '
        'https://www.druthers.io/settings, or a JWT from `POST /v1/auth/token`. '
        '`POST /v1/auth/token` and `POST /v1/users` are marked "No Auth" since '
        "you don't have a token yet when calling them.\n\n"
        '**Base URL:** the `baseUrl` variable defaults to production '
        f'(`{DEFAULT_BASE_URL}`) - override it in a Postman environment to '
        'point at localhost (`http://localhost:8000`) or QA '
        '(`https://api-qa.druthers.io`) instead.\n\n'
        'Generated from the checked-in OpenAPI schema by '
        '`scripts/generate_postman_collection.py` - see `docs/mcp-usage.md` '
        'for the MCP (Claude) integration instead of raw HTTP calls.'
    )

    return {
        'info': {
            'name': 'Druthers API',
            'description': description,
            'schema': (
                'https://schema.getpostman.com/json/collection/v2.1.0/'
                'collection.json'
            ),
        },
        'auth': {
            'type': 'bearer',
            'bearer': [{'key': 'token', 'value': '{{apiToken}}', 'type': 'string'}],
        },
        'variable': [
            {'key': 'baseUrl', 'value': DEFAULT_BASE_URL, 'type': 'string'},
            {'key': 'apiToken', 'value': '', 'type': 'string'},
        ],
        'item': items,
    }


def main() -> None:
    """CLI entry point: read the OpenAPI schema, write the Postman collection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openapi', default='openapi.json', type=Path)
    parser.add_argument(
        '--out', default='docs/druthers-api.postman_collection.json', type=Path
    )
    args = parser.parse_args()

    schema = load_schema(args.openapi)
    collection = build_collection(schema)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', encoding='utf-8') as f:
        json.dump(collection, f, indent=2)
        f.write('\n')

    total_requests = sum(
        len(folder['item']) if 'item' in folder else 1 for folder in collection['item']
    )
    print(
        f'Wrote {args.out} ({total_requests} requests across {len(collection["item"])} folders)'
    )


if __name__ == '__main__':
    main()
