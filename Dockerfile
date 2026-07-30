# Use an official Python runtime as a parent image
FROM python:3.14.6-alpine

# Set the working directory in the container
WORKDIR /app

# Git SHA of the commit this image was built from, so a running container
# can be checked against the working tree (see GET /health, api#232).
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# Create a non-root user
RUN addgroup -S adam && adduser -S adam -G adam

# Set up PATH for pip installations
ENV PATH="/home/adam/.local/bin:$PATH"

# Copy requirements first to leverage Docker cache
COPY --chown=adam:adam requirements/base.txt requirements/base.txt

# Install system dependencies as root
USER root
RUN apk update && apk add --no-cache \
    postgresql-dev \
    gcc \
    python3-dev \
    musl-dev \
    rust \
    cargo \
    xz

# Switch to non-root user for pip operations
USER adam

# Install Python packages as non-root user
RUN pip install --no-cache-dir --upgrade "pip>=26.1.2" && \
    pip install --no-cache-dir -r requirements/base.txt

# Copy the rest of the application with correct ownership
COPY --chown=adam:adam . .

# pip (every release, including latest) vendors its own old copies of
# msgpack/setuptools internally (pip/_vendor/vendor.txt) carrying known CVEs
# (GHSA-6v7p-g79w-8964, CVE-2025-47273) — a lag in pip's own vendoring, not
# something fixable by bumping our requirements. The app never invokes pip
# at runtime (CMD only runs the server), so drop pip — and the base image's
# dormant ensurepip bootstrap wheel, which vendors the same old versions for
# the same reason — from the final image rather than ship known CVEs for a
# tool that's dead weight past the build step.
USER root
RUN find / -xdev -depth \( -path '*/site-packages/pip' -o -name 'pip-*.dist-info' \) -exec rm -rf {} + && \
    find /usr -path '*/ensurepip/_bundled/*.whl' -delete
USER adam

# Make port 8000 available
EXPOSE 8000

# Liveness probe: the API answers on GET / at PORT (default 8000). Uses the
# stdlib (no curl/wget needed). Resolves Trivy DS-0026 (no HEALTHCHECK).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/',timeout=2).status==200 else 1)"

# Run app.py when the container launches
CMD ["sh", "-c", "python -m app.main"]
