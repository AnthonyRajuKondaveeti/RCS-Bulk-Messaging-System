# Multi-stage Dockerfile for RCS Platform API
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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
