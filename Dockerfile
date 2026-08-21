# Build dependency wheels separately so compilers and build headers do not
# become part of the production image.
FROM python:3.14.7-alpine AS builder

WORKDIR /build

COPY requirements/base.txt requirements/base.txt

RUN apk add --no-cache \
    postgresql-dev \
    gcc \
    python3-dev \
    musl-dev \
    rust \
    cargo \
    xz && \
    pip install --no-cache-dir --upgrade "pip>=26.1.2" && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements/base.txt

# Keep only the application, its dependency wheels, and the runtime PostgreSQL
# library in the final image.
FROM python:3.14.7-alpine AS runtime

WORKDIR /app

# Git SHA of the commit this image was built from, so a running container
# can be checked against the working tree (see GET /health, api#232).
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# Create a non-root user.
RUN addgroup -S adam && adduser -S adam -G adam

COPY requirements/base.txt requirements/base.txt
COPY --from=builder /wheels /wheels

RUN apk add --no-cache libpq && \
    pip install --no-cache-dir --no-index --find-links=/wheels -r requirements/base.txt && \
    rm -rf /wheels && \
    find / -xdev -depth \
        \( -path '*/site-packages/pip' -o -name 'pip-*.dist-info' \) -exec rm -rf {} + && \
    find /usr -path '*/ensurepip/_bundled/*.whl' -delete

# Copy the application with the ownership it will have at runtime.
COPY --chown=adam:adam . .

USER adam

# Make port 8000 available.
EXPOSE 8000

# Liveness probe: the API answers on GET /health at PORT (default 8000). Uses
# the stdlib (no curl/wget needed). Resolves Trivy DS-0026 (no HEALTHCHECK).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=2).status==200 else 1)"

# Run app.py when the container launches.
CMD ["sh", "-c", "python -m app.main"]
