# syntax=docker/dockerfile:1.7

# ----- Stage 1: build wheel ----------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build tooling
RUN pip install --upgrade pip build

# Copy only the files needed to build the wheel
COPY pyproject.toml README.md ./
COPY posrat ./posrat

RUN python -m build --wheel --outdir /wheels

# ----- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # POSRAT runtime defaults inside the container
    POSRAT_DATA_DIR=/data \
    POSRAT_NO_BROWSER=1

# Minimal OS deps. NiceGUI/uvicorn are pure-python, bcrypt & pydantic ship
# manylinux wheels. Keep the image slim.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system --gid 1000 posrat \
 && useradd --system --uid 1000 --gid posrat --home-dir /app --shell /usr/sbin/nologin posrat

WORKDIR /app

# Install the wheel built in stage 1
COPY --from=builder /wheels /wheels
RUN pip install /wheels/*.whl \
 && rm -rf /wheels

# Data directory (exam SQLite files, assets, system DB, nicegui storage).
RUN mkdir -p /data && chown -R posrat:posrat /data /app

USER posrat

EXPOSE 8080

VOLUME ["/data"]

# tini as PID 1 so Ctrl-C / docker stop propagate cleanly to uvicorn
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "posrat"]
