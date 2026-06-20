# ── Stage 1: builder ─────────────────────────────────────────────
# Install dependencies in a separate stage so the final image
# doesn't contain pip or build tools.
FROM python:3.13-slim AS builder

WORKDIR /app

# Copy only requirements first — Docker caches this layer
# and won't re-run pip install unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/
COPY consumer/ ./consumer/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Non-root user — never run production containers as root
RUN addgroup --system devpad && adduser --system --ingroup devpad devpad
USER devpad

# Expose the port uvicorn will listen on (docker-compose maps this)
EXPOSE 8000

# INSTANCE_ID is overridden per container in docker-compose.yml
ENV INSTANCE_ID=api1

# Start uvicorn. --host 0.0.0.0 is required inside Docker.
# --workers 1 because we run multiple containers instead of multiple workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
