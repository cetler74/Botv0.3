// Dashboard JavaScript
class Dashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 3000;
        this.updateInterval = null;
        this.toast = null;
        
        this.initialize();
    }
    
    initialize() {
        this.setupWebSocket();
        this.setupEventListeners();
        this.setupToast();
        this.startPeriodicUpdates();
    }
    
    setupWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.updateConnectionStatus('connected');
            this.reconnectAttempts = 0;
        };
        
        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            } catch (error) {
                console.error('Error parsing WebSocket message:', error);
            }
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
            this.scheduleReconnect();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionStatus('disconnected');
        };
    }
    
    setupEventListeners() {
        // Trading control buttons
        document.getElementById('start-btn').addEventListener('click', () => this.startTrading());
        document.getElementById('stop-btn').addEventListener('click', () => this.stopTrading());
        document.getElementById('emergency-btn').addEventListener('click', () => this.emergencyStop());
    }
    
    setupToast() {
        this.toast = new bootstrap.Toast(document.getElementById('notification-toast'));
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        indicator.className = `badge ${status === 'connected' ? 'bg-success' : status === 'connecting' ? 'bg-warning' : 'bg-danger'}`;
        indicator.textContent = status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected';
    }
    
    scheduleReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this.updateConnectionStatus('connecting');
            setTimeout(() => {
                this.setupWebSocket();
            }, this.reconnectDelay * this.reconnectAttempts);
        }
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'portfolio_update':
                this.updatePortfolio(data.data);
                break;
            case 'trading_status_update':
                this.updateTradingStatus(data.data);
                break;
            case 'risk_exposure_update':
                this.updateRiskExposure(data.data);
                break;
            case 'trade_update':
                this.updateRecentTrades();
                break;
            case 'alert_update':
                this.updateRecentAlerts();
                break;
            case 'emergency_stop':
                this.showNotification('Emergency stop executed!', 'warning');
                this.updateTradingStatus(data.data);
                break;
            default:
                console.log('Unknown message type:', data.type);
        }
    }
    
    async startTrading() {
        try {
            const response = await fetch('/api/control/start', { method: 'POST' });
            if (response.ok) {
                this.showNotification('Trading bot started successfully', 'success');
                this.updateTradingStatus();
            } else {
                throw new Error('Failed to start trading');
            }
        } catch (error) {
            console.error('Error starting trading:', error);
            this.showNotification('Failed to start trading bot', 'error');
        }
    }
    
    async stopTrading() {
        try {
            const response = await fetch('/api/control/stop', { method: 'POST' });
            if (response.ok) {
                this.showNotification('Trading bot stopped successfully', 'info');
                this.updateTradingStatus();
            } else {
                throw new Error('Failed to stop trading');
            }
        } catch (error) {
            console.error('Error stopping trading:', error);
            this.showNotification('Failed to stop trading bot', 'error');
        }
    }
    
    async emergencyStop() {
        if (confirm('Are you sure you want to execute an emergency stop? This will close all active trades.')) {
            try {
                const response = await fetch('/api/control/emergency-stop', { method: 'POST' });
                if (response.ok) {
                    this.showNotification('Emergency stop executed successfully', 'warning');
                    this.updateTradingStatus();
                } else {
                    throw new Error('Failed to execute emergency stop');
                }
            } catch (error) {
                console.error('Error executing emergency stop:', error);
                this.showNotification('Failed to execute emergency stop', 'error');
            }
        }
    }
    
    async updateTradingStatus() {
        try {
            const response = await fetch('/api/trading/status');
            if (response.ok) {
                const data = await response.json();
                this.updateTradingStatusUI(data);
            }
        } catch (error) {
            console.error('Error updating trading status:', error);
        }
    }
    
    updateTradingStatusUI(data) {
        const statusElement = document.getElementById('trading-status');
        const cycleCountElement = document.getElementById('cycle-count');
        const activeTradesElement = document.getElementById('active-trades');
        const uptimeElement = document.getElementById('uptime');
        
        // Update status
        statusElement.textContent = data.status;
        statusElement.className = `badge ${this.getStatusBadgeClass(data.status)}`;
        
        // Update cycle count
        cycleCountElement.textContent = data.cycle_count || 0;
        
        // Update active trades
        activeTradesElement.textContent = data.active_trades || 0;
        
        // Update uptime
        if (data.uptime) {
            uptimeElement.textContent = this.formatUptime(data.uptime);
        }
        
        // Add animation
        statusElement.classList.add('status-update');
        setTimeout(() => statusElement.classList.remove('status-update'), 500);
    }
    
    async updatePortfolio() {
        try {
            const response = await fetch('/api/portfolio');
            if (response.ok) {
                const data = await response.json();
                this.updatePortfolioUI(data);
            }
        } catch (error) {
            console.error('Error updating portfolio:', error);
        }
    }
    
    updatePortfolioUI(data) {
        const totalBalanceElement = document.getElementById('total-balance');
        const totalPnlElement = document.getElementById('total-pnl');
        const dailyPnlElement = document.getElementById('daily-pnl');
        const winRateElement = document.getElementById('win-rate');
        
        // Update total balance
        totalBalanceElement.textContent = this.formatCurrency(data.total_balance || 0);
        
        // Update total PnL
        const totalPnl = data.total_pnl || 0;
        totalPnlElement.textContent = this.formatCurrency(totalPnl);
        totalPnlElement.className = `h4 ${this.getPnlClass(totalPnl)}`;
        
        // Update daily PnL
        const dailyPnl = data.daily_pnl || 0;
        dailyPnlElement.textContent = this.formatCurrency(dailyPnl);
        dailyPnlElement.className = `h4 ${this.getPnlClass(dailyPnl)}`;
        
        // Update win rate
        const winRate = data.win_rate || 0;
        winRateElement.textContent = `${(winRate * 100).toFixed(1)}%`;
    }
    
    async updateRiskExposure() {
        try {
            const response = await fetch('/api/risk/exposure');
            if (response.ok) {
                const data = await response.json();
                this.updateRiskExposureUI(data);
            }
        } catch (error) {
            console.error('Error updating risk exposure:', error);
        }
    }
    
    updateRiskExposureUI(data) {
        const totalExposureElement = document.getElementById('total-exposure');
        const exposurePercentageElement = document.getElementById('exposure-percentage');
        const exposureProgressElement = document.getElementById('exposure-progress');
        
        // Update total exposure
        totalExposureElement.textContent = this.formatCurrency(data.total_exposure || 0);
        
        // Update exposure percentage
        const exposurePercentage = data.exposure_percentage || 0;
        exposurePercentageElement.textContent = `${exposurePercentage.toFixed(1)}%`;
        
        // Update progress bar
        exposureProgressElement.style.width = `${Math.min(exposurePercentage, 100)}%`;
        
        // Update progress bar color based on exposure level
        if (exposurePercentage > 80) {
            exposureProgressElement.className = 'progress-bar bg-danger';
        } else if (exposurePercentage > 60) {
            exposureProgressElement.className = 'progress-bar bg-warning';
        } else {
            exposureProgressElement.className = 'progress-bar bg-success';
        }
    }
    
    async updateRecentTrades() {
        try {
            const response = await fetch('/api/trades?limit=10');
            if (response.ok) {
                const data = await response.json();
                this.updateRecentTradesUI(data.trades || []);
            }
        } catch (error) {
            console.error('Error updating recent trades:', error);
        }
    }
    
    updateRecentTradesUI(trades) {
        const tbody = document.getElementById('recent-trades');
        
        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center">No recent trades</td></tr>';
            return;
        }
        
        tbody.innerHTML = trades.map(trade => `
            <tr>
                <td>${this.formatDateTime(trade.entry_time)}</td>
                <td>${trade.pair}</td>
                <td>${trade.exchange}</td>
                <td>${trade.position_size > 0 ? 'BUY' : 'SELL'}</td>
                <td>${Math.abs(trade.position_size).toFixed(4)}</td>
                <td>${this.formatCurrency(trade.entry_price)}</td>
                <td><span class="trade-status-${trade.status.toLowerCase()}">${trade.status}</span></td>
            </tr>
        `).join('');
    }
    
    async updateRecentAlerts() {
        try {
            const response = await fetch('/api/alerts?limit=5');
            if (response.ok) {
                const data = await response.json();
                this.updateRecentAlertsUI(data.alerts || []);
            }
        } catch (error) {
            console.error('Error updating recent alerts:', error);
        }
    }
    
    updateRecentAlertsUI(alerts) {
        const container = document.getElementById('recent-alerts');
        
        if (alerts.length === 0) {
            container.innerHTML = '<p class="text-muted">No recent alerts</p>';
            return;
        }
        
        container.innerHTML = alerts.map(alert => `
            <div class="alert-item alert-${alert.level.toLowerCase()}">
                <div class="d-flex justify-content-between">
                    <strong>${alert.category}</strong>
                    <small>${this.formatDateTime(alert.timestamp)}</small>
                </div>
                <div>${alert.message}</div>
            </div>
        `).join('');
    }
    
    startPeriodicUpdates() {
        // Update data every 30 seconds
        this.updateInterval = setInterval(() => {
            this.updateTradingStatus();
            this.updatePortfolio();
            this.updateRiskExposure();
            this.updateRecentTrades();
            this.updateRecentAlerts();
        }, 30000);
        
        // Initial update
        this.updateTradingStatus();
        this.updatePortfolio();
        this.updateRiskExposure();
        this.updateRecentTrades();
        this.updateRecentAlerts();
    }
    
    showNotification(message, type = 'info') {
        const toastMessage = document.getElementById('toast-message');
        toastMessage.textContent = message;
        
        // Update toast header color based on type
        const toastHeader = document.querySelector('#notification-toast .toast-header');
        toastHeader.className = `toast-header ${this.getToastHeaderClass(type)}`;
        
        this.toast.show();
    }
    
    // Utility functions
    formatCurrency(amount) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(amount);
    }
    
    formatDateTime(dateString) {
        const date = new Date(dateString);
        return date.toLocaleString();
    }
    
    formatUptime(uptimeString) {
        // Parse uptime string (e.g., "1 day, 2:30:45")
        const match = uptimeString.match(/(\d+):(\d+):(\d+)/);
        if (match) {
            const hours = parseInt(match[1]);
            const minutes = parseInt(match[2]);
            const seconds = parseInt(match[3]);
            return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
        return uptimeString;
    }
    
    getStatusBadgeClass(status) {
        switch (status) {
            case 'running':
                return 'bg-success';
            case 'stopped':
                return 'bg-secondary';
            case 'emergency_stop':
                return 'bg-danger';
            default:
                return 'bg-secondary';
        }
    }
    
    getPnlClass(pnl) {
        if (pnl > 0) return 'pnl-positive';
        if (pnl < 0) return 'pnl-negative';
        return 'pnl-neutral';
    }
    
    getToastHeaderClass(type) {
        switch (type) {
            case 'success':
                return 'bg-success text-white';
            case 'error':
                return 'bg-danger text-white';
            case 'warning':
                return 'bg-warning text-dark';
            default:
                return 'bg-info text-white';
        }
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new Dashboard();
}); 