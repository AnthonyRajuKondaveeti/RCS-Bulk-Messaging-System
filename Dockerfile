# =============================================================================
# RCS Platform — Multi-stage Production Dockerfile
#
# Stage 1 (builder): Install all dependencies into a venv
# Stage 2 (runtime): Minimal image — copy venv + app, run as non-root user
#
# Build:
#   docker build -t rcs-platform:latest .
#
# Run API:
#   docker run --env-file .env.prod -p 8000:8000 rcs-platform:latest
#
# Run workers:
#   docker run --env-file .env.prod rcs-platform:latest \
#       python -m apps.workers.manager
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — Builder
# Installs all Python deps into /opt/venv
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# System build dependencies (needed to compile asyncpg, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtual environment so Stage 2 can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip first — avoids resolver issues with older bundled pip
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements first — Docker layer cache means this only re-runs
# when requirements.txt actually changes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


# -----------------------------------------------------------------------------
# Stage 2 — Runtime
# Minimal image: only the venv + application source
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL maintainer="rcs-platform" \
      version="1.0.0" \
      description="RCS Messaging Platform API"

# Runtime system dependencies only (libpq for asyncpg at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built venv from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user and group — never run as root in production
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home --shell /bin/false appuser

# Set working directory
WORKDIR /app

# Copy application source
# .dockerignore excludes: .git, .env, __pycache__, *.pyc, logs/, tests/local/
COPY --chown=appuser:appgroup . /app

# Drop all privileges — switch to non-root user
USER appuser

# Expose the API port
EXPOSE 8000

# Health check used by Docker + orchestrators (Kubernetes, ECS)
# Calls the /health endpoint every 30s; allows 3 failures before marking unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command: run the API under Gunicorn with UvicornWorker
# API_WORKERS defaults to 4 if not set via env.
# Override CMD in docker-compose.prod.yml for worker containers.
CMD ["sh", "-c", \
     "exec gunicorn apps.api.main:app \
        --worker-class uvicorn.workers.UvicornWorker \
        --workers ${API_WORKERS:-4} \
        --bind 0.0.0.0:8000 \
        --timeout 60 \
        --graceful-timeout 30 \
        --keep-alive 5 \
        --log-level ${LOG_LEVEL:-info} \
        --access-logfile - \
        --error-logfile -"]
