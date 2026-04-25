FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/web-dashboard-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, templates, and bundled config
COPY services/web-dashboard-service/main.py .
COPY services/web-dashboard-service/strategy_optimizer.py .
COPY services/web-dashboard-service/enhanced_strategy_optimizer.py .
COPY services/web-dashboard-service/test_connections.py .
COPY services/web-dashboard-service/test_endpoints.py .
COPY services/web-dashboard-service/templates/ templates/
COPY services/web-dashboard-service/static/ static/
RUN mkdir -p /app/config
COPY config/config.yaml /app/config/config.yaml

# Expose port
EXPOSE 8006

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8006/health || exit 1

# Run the application
CMD ["python", "main.py"] 