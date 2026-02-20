# Multi-stage Dockerfile for RCS Platform Workers
FROM python:3.11-slim as builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 rcs

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 rcs

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /home/rcs/.local

# Copy application code
COPY --chown=rcs:rcs . .

# Set environment
ENV PYTHONPATH=/app
ENV PATH=/home/rcs/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Switch to app user
USER rcs

# Health check (checks if worker process is running)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "python -m apps.workers" || exit 1

# Run workers
CMD ["python", "-m", "apps.workers.manager"]
