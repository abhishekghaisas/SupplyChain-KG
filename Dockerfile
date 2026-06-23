# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps needed to compile some Python packages (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer-cached until requirements change)
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# Pre-download the sentence-transformer model during build so the container
# never needs network access at runtime and the non-root appuser has read access.
# HF_HOME is set to a location inside the image that appuser can reach.
ENV HF_HOME=/app/hf_cache
RUN PYTHONPATH=/install/lib/python3.11/site-packages HF_HOME=/app/hf_cache python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2')" \
 && chmod -R 755 /app/hf_cache


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from the builder
COPY --from=builder /install /usr/local

# Copy the pre-downloaded model cache — appuser can read it
COPY --from=builder /app/hf_cache /app/hf_cache

# Copy source tree
COPY src/ ./src/

# Tell sentence-transformers where the model lives
ENV HF_HOME=/app/hf_cache

# Non-root user for security
RUN addgroup --system appgroup \
 && adduser  --system --ingroup appgroup appuser
USER appuser

# Port the API listens on (matches config.py api_port default)
EXPOSE 8000

# Health check — hits the no-auth /health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production entrypoint
CMD ["uvicorn", "src.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info"]