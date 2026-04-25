FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and templates
COPY main.py .
COPY strategy_optimizer.py .
COPY enhanced_strategy_optimizer.py .
COPY test_connections.py .
COPY test_endpoints.py .
COPY templates/ templates/
COPY static/ static/

# Expose port
EXPOSE 8006

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8006/health || exit 1

# Run the application
CMD ["python", "main.py"] 