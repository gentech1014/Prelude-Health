# --- Build stage ---
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build
# README.md is required: pyproject declares `readme = "README.md"`,
# and the build backend fails without it.
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache-dir .

# The report renderer (`app.services.report_pdf`) prints its HTML template
# through headless Chromium. Only the browser binary is fetched here --
# this stage never runs it, so the OS shared libraries it needs (nss, atk,
# cups, ...) are installed in the runtime stage below instead, where they
# actually apply.
RUN . /opt/venv/bin/activate && \
    playwright install chromium

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 1000 appuser
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/ms-playwright /opt/ms-playwright

# The browser *binary* was already fetched in the builder stage; what's
# missing here is the OS shared libraries (nss, atk, cups, ...) it needs
# to actually run -- `install-deps` apt-installs exactly those, then
# `rm -rf /var/lib/apt/lists` keeps the apt cache out of this layer.
RUN apt-get update && \
    playwright install-deps chromium && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src ./src

RUN chown -R appuser:appuser /opt/ms-playwright

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

# uvicorn handles SIGTERM natively for graceful shutdown of in-flight requests/websockets.
# --proxy-headers/--forwarded-allow-ips=* trust X-Forwarded-Proto/-For from
# whatever sits in front (see Caddyfile/docker-compose.yml) -- safe only
# because this container's port is never published directly once a proxy
# is in place; narrow --forwarded-allow-ips to the proxy's address if it
# ever is.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
