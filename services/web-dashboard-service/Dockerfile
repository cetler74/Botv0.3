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
COPY core/hyperliquid_watchlist.py ./hyperliquid_watchlist.py
COPY core/cycle_analysis_snapshot.py ./cycle_analysis_snapshot.py
COPY core/perp_paper_pnl_report.py ./perp_paper_pnl_report.py
COPY core/spot_pnl_report.py ./spot_pnl_report.py
COPY core/dashboard_audit_venues.py ./dashboard_audit_venues.py
COPY core/supply_demand_audit_summary.py ./supply_demand_audit_summary.py
COPY core/dual_sma_audit_summary.py ./dual_sma_audit_summary.py
COPY core/arc_audit_summary.py ./arc_audit_summary.py
COPY core/ema50_breakout_pullback_audit_summary.py ./ema50_breakout_pullback_audit_summary.py
COPY core/orb_5m_scalp_audit_summary.py ./orb_5m_scalp_audit_summary.py
COPY core/ema50_entry_block_reason.py ./ema50_entry_block_reason.py
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