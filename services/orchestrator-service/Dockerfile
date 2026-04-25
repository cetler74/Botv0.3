FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/orchestrator-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy orchestrator modules (skip backup copies and local docker test script)
COPY services/orchestrator-service/ /tmp/orchestrator-src/
RUN find /tmp/orchestrator-src -maxdepth 1 -type f -name '*.py' \
      ! -name '*backup*' ! -name 'test_docker_order_book.py' \
      -exec cp {} /app/ \; \
    && rm -rf /tmp/orchestrator-src
COPY core ./core
COPY strategy ./strategy

# Expose port
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Run the application
CMD ["python", "main.py"] 