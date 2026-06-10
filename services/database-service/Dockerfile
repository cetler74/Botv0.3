FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/database-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (repo root context)
COPY services/database-service/*.py ./
COPY core/hyperliquid_ledger.py ./hyperliquid_ledger.py
COPY core/perp_paper_pnl_report.py ./perp_paper_pnl_report.py
COPY core/hyperliquid_live_readiness.py ./hyperliquid_live_readiness.py
COPY core/supply_demand_audit_summary.py ./supply_demand_audit_summary.py
COPY core/dual_sma_audit_summary.py ./dual_sma_audit_summary.py
COPY core/arc_audit_summary.py ./arc_audit_summary.py
COPY core/ema50_breakout_pullback_audit_summary.py ./ema50_breakout_pullback_audit_summary.py
COPY core/orb_5m_scalp_audit_summary.py ./orb_5m_scalp_audit_summary.py

# Expose port
EXPOSE 8002

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

# Run the application
CMD ["python", "main.py"]
