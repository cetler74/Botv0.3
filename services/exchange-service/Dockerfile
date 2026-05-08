FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build context is repo root (see docker-compose) so we can bundle shared config
COPY services/exchange-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/config
COPY config/config.yaml /app/config/config.yaml

# Application modules (trailing / required when multiple *.py match)
COPY services/exchange-service/*.py ./

# Expose port
EXPOSE 8003

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# Run the application
CMD ["python", "main.py"]
