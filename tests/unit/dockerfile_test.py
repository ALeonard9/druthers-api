"""Regression tests for the production image build structure."""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).parents[2] / 'Dockerfile'
DOCKERIGNORE = Path(__file__).parents[2] / '.dockerignore'

# Read the base image tag out of the Dockerfile instead of hardcoding it.
# Pinning the version here meant every Dependabot patch bump broke this test
# for no reason (3.14.6 -> 3.14.7 in #389 failed on the unpack below). What
# this test actually cares about is that both stages sit on the same base and
# that no build toolchain survives into runtime, not which patch release it is.
_STAGE_RE = re.compile(r'^FROM (\S+) AS (\w+)$', re.MULTILINE)


def _stage_bases(dockerfile: str) -> dict[str, str]:
    """Map each build stage name to the image tag it is built from."""
    return {stage: image for image, stage in _STAGE_RE.findall(dockerfile)}


def test_build_stages_share_one_pinned_base_image():
    """Both stages track the same pinned base, so a bump cannot skew them."""
    bases = _stage_bases(DOCKERFILE.read_text())

    assert set(bases) == {'builder', 'runtime'}
    assert bases['builder'] == bases['runtime']
    assert re.fullmatch(r'python:\d+\.\d+\.\d+-alpine', bases['runtime'])


def test_runtime_stage_uses_builder_wheels_without_build_toolchains():
    """Keep dependency build tools out of the production image stage."""
    dockerfile = DOCKERFILE.read_text()
    runtime_base = _stage_bases(dockerfile)['runtime']

    builder, runtime = dockerfile.split(f'FROM {runtime_base} AS runtime', 1)

    assert f'FROM {runtime_base} AS builder' in builder
    assert 'pip wheel --no-cache-dir --wheel-dir /wheels' in builder
    assert 'postgresql-dev' in builder
    assert 'gcc' in builder
    assert 'rust' in builder
    assert 'cargo' in builder

    assert 'COPY --from=builder /wheels /wheels' in runtime
    assert '--no-index --find-links=/wheels' in runtime
    assert 'gcc' not in runtime
    assert 'rust' not in runtime
    assert 'cargo' not in runtime
    assert 'postgresql-dev' not in runtime


def test_runtime_healthcheck_targets_health_endpoint():
    """Probe the application's dedicated liveness endpoint."""
    dockerfile = DOCKERFILE.read_text()

    assert "'/health'" in dockerfile
    assert 'HEALTHCHECK' in dockerfile


# Paths that exist in a working checkout but must not appear in the image.
# graphify-out/ is ~50 MB of AI tooling output; env/ holds dev/qa/prod env
# files whose names are not caught by the *.env glob (no cross-slash match).
IGNORED_DIRS = ['graphify-out/', 'env/']


def test_dockerignore_excludes_large_and_sensitive_dirs():
    """Directories that only matter locally must not leak into the image."""
    ignore = DOCKERIGNORE.read_text()
    for dirname in IGNORED_DIRS:
        assert dirname in ignore, f'{dirname} missing from .dockerignore'
