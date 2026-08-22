'''
Regenerate openapi.json from the live FastAPI app.

The committed spec is consumed by things that cannot tell it has gone stale:
scripts/generate_postman_collection.py builds the Postman collection from it,
and the druthers super repo treated it as the route inventory. It had drifted
by five routes - four /{domain}/{id}/social endpoints and
GET /v1/admin/reports/{report} - all of which exist and all of which the web
BFF calls. The pre-commit hook that was supposed to cover this is
openapi-spec-validator, which only checks the file is well-formed; it happily
validated the stale spec for months.

Run by the `openapi-json` pre-commit hook whenever anything under app/
changes, and enforced again in CI, since a hook can be skipped with
--no-verify and pre-commit.ci cannot run venv-backed hooks at all.

  python scripts/generate_openapi.py            write openapi.json
  python scripts/generate_openapi.py --check    exit 1 if it would change
'''

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / 'openapi.json'
# docs/druthers-api.postman_collection.json is built *from* openapi.json, so
# it inherits every drift the spec has. Regenerating one without the other
# just moves the stale artifact, which is how the collection ended up 45
# requests short of the API alongside the spec being 5 routes short.
POSTMAN = ROOT / 'scripts' / 'generate_postman_collection.py'

# This script lives in scripts/, so `app` is only importable once the repo
# root is on the path. Done at module scope rather than inside main() so the
# import in render() cannot depend on call order.
sys.path.insert(0, str(ROOT))


def render() -> str:
    '''The spec as it should appear on disk.'''
    # app.run builds the title as 'druthers.io API ' + settings.env, so the
    # output would otherwise depend on whoever's shell generated it. Pin the
    # default before importing, so a developer with ENV=qa exported does not
    # produce a spurious one-line diff.
    os.environ.setdefault('ENV', 'local')
    os.environ['ENV'] = 'local'

    # pylint: disable=import-outside-toplevel
    # Deliberately deferred: importing app.run builds the FastAPI app, and
    # the title is fixed at construction from settings.env. Importing at
    # module scope would read the environment before the pin above.
    # pylint: disable=import-error
    # `app` resolves via the sys.path insert above, which pylint does not
    # execute.
    from app.run import app

    # indent=2 and insertion order match the committed file, so a real change
    # shows as a real diff rather than a 13,000-line reformat.
    return json.dumps(app.openapi(), indent=2) + '\n'


def main() -> int:
    '''Write or check openapi.json; returns a process exit code.'''
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--check',
        action='store_true',
        help='do not write; exit 1 if openapi.json is out of date',
    )
    args = parser.parse_args()

    fresh = render()
    current = TARGET.read_text() if TARGET.exists() else ''

    if fresh == current:
        print('openapi.json is up to date')
        return 0

    if args.check:
        print(
            'openapi.json is out of date. Run:\n'
            '  python scripts/generate_openapi.py',
            file=sys.stderr,
        )
        return 1

    TARGET.write_text(fresh)
    print(f'openapi.json regenerated ({len(json.loads(fresh)["paths"])} paths)')

    subprocess.run([sys.executable, str(POSTMAN)], check=True, cwd=ROOT)
    # Non-zero so the pre-commit run fails and the developer re-stages the
    # file, the same way black and end-of-file-fixer behave here.
    return 1


if __name__ == '__main__':
    sys.exit(main())
