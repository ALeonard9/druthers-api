"""Regression tests for the production image build structure."""

from pathlib import Path

DOCKERFILE = Path(__file__).parents[2] / 'Dockerfile'
DOCKERIGNORE = Path(__file__).parents[2] / '.dockerignore'


def test_runtime_stage_uses_builder_wheels_without_build_toolchains():
    """Keep dependency build tools out of the production image stage."""
    dockerfile = DOCKERFILE.read_text()
    builder, runtime = dockerfile.split('FROM python:3.14.6-alpine AS runtime', 1)

    assert 'FROM python:3.14.6-alpine AS builder' in builder
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
