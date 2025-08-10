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
        const availableBalanceElement = document.getElementById('available-balance');
        const totalPnlElement = document.getElementById('total-pnl');
        const dailyPnlElement = document.getElementById('daily-pnl');
        const openTradesElement = document.getElementById('open-trades');
        const totalUnrealizedPnlElement = document.getElementById('total-unrealized-pnl');
        const winRateElement = document.getElementById('win-rate');
        // Update total balance
        totalBalanceElement.textContent = this.formatCurrency(data.total_balance || 0);
        
        // Calculate and update available balance (sum of all exchanges)
        let availableSum = 0;
        if (data.exchanges) {
            for (const ex of Object.values(data.exchanges)) {
                availableSum += (typeof ex.available !== 'undefined' ? ex.available : (ex.available_balance || 0));
            }
        }
        availableBalanceElement.textContent = this.formatCurrency(availableSum);
        
        // Generate and set tooltips with per-exchange breakdown
        if (data.exchanges) {
            const totalBalanceTooltip = this.generateBalanceTooltip(data.exchanges, 'balance');
            const availableBalanceTooltip = this.generateBalanceTooltip(data.exchanges, 'available_balance');
            
            totalBalanceElement.setAttribute('title', totalBalanceTooltip);
            availableBalanceElement.setAttribute('title', availableBalanceTooltip);
        }
        // Update total PnL
        const totalPnl = data.total_pnl || 0;
        totalPnlElement.textContent = this.formatCurrency(totalPnl);
        totalPnlElement.className = `h4 ${this.getPnlClass(totalPnl)}`;
        
        // Generate PnL tooltip
        if (data.exchanges) {
            const pnlTooltip = this.generatePnLTooltip(data.exchanges);
            totalPnlElement.setAttribute('title', pnlTooltip);
        }
        // Update daily PnL
        const dailyPnl = data.daily_pnl || 0;
        dailyPnlElement.textContent = this.formatCurrency(dailyPnl);
        dailyPnlElement.className = `h4 ${this.getPnlClass(dailyPnl)}`;
        // Update open trades count
        const activeTrades = data.active_trades || 0;
        openTradesElement.textContent = activeTrades;
        // Update total unrealized PnL
        const totalUnrealizedPnl = data.total_unrealized_pnl || 0;
        totalUnrealizedPnlElement.textContent = this.formatCurrency(totalUnrealizedPnl);
        totalUnrealizedPnlElement.className = `h4 ${this.getPnlClass(totalUnrealizedPnl)}`;
        // Update win rate
        const winRate = data.win_rate || 0;
        winRateElement.textContent = `${(winRate * 100).toFixed(1)}%`;
        // Update per-exchange breakdown table
        if (data.exchanges) {
            const exchangeRows = Object.entries(data.exchanges).map(([exchange, ex]) => ({
                exchange,
                total_balance: ex.balance,
                available_balance: ex.available,
                total_pnl: ex.total_pnl,
                timestamp: ex.timestamp
            }));
            updateExchangeBreakdownTable(exchangeRows);
        }
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
        const tbody = document.getElementById('recent-trades') || document.getElementById('trades-table-body');
        if (!tbody) return;
        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="15" class="text-center">No recent trades</td></tr>';
            return;
        }
        tbody.innerHTML = trades.map(trade => `
            <tr>
                <td class="px-2 sm:px-4 py-3 text-sm font-mono text-gray-900 align-middle" title="${trade.trade_id || 'N/A'}">${trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm font-semibold text-gray-900 align-middle">${trade.pair || 'N/A'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle mobile-hidden">${trade.exchange || 'N/A'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle mobile-hidden">${this.formatDateTime(trade.entry_time)}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">$${trade.entry_price?.toFixed(4) || '0.0000'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">$${trade.current_price?.toFixed(4) || '0.0000'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">${trade.position_size?.toFixed(6) || '0.000000'}</td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle" id="notional-value-${trade.trade_id || 'N/A'}">$${((trade.entry_price || 0) * (trade.position_size || 0)).toFixed(2)}</td>
                <td class="px-2 sm:px-4 py-3 text-sm ${this.getPnlClass(trade.unrealized_pnl)} text-right align-middle">$${(trade.unrealized_pnl || 0).toFixed(2)}</td>
                <td class="px-2 sm:px-4 py-3 text-sm align-middle">
                    <span class="badge ${trade.status === 'OPEN' ? 'bg-success' : trade.status === 'CLOSED' ? 'bg-secondary' : 'bg-warning'}">${trade.status || 'N/A'}</span>
                </td>
                <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle mobile-hidden" title="${trade.entry_reason || 'N/A'}">${this.truncateText(trade.entry_reason || 'N/A', 30)}</td>
                <td class="profit-trigger-col align-middle">
                  ${trade.profit_protection || 'inactive'}
                  <div class="text-xs text-gray-500">${trade.profit_protection_trigger ? `Trigger: ${trade.profit_protection_trigger}` : 'None'}</div>
                </td>
                <td class="trailing-stop-col align-middle">
                  ${trade.trail_stop || 'inactive'}
                  <div class="text-xs text-gray-500">${trade.trail_stop_trigger ? `Trigger: ${trade.trail_stop_trigger}` : 'None'}</div>
                </td>
                <td class="highest-price-col text-right align-middle">
                  ${trade.highest_price !== undefined && trade.highest_price !== null ? trade.highest_price.toFixed(6) : 'None'}
                </td>
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
    
    truncateText(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
    }
    
    generateBalanceTooltip(exchanges, balanceType) {
        const title = balanceType === 'balance' ? 'Total Balance by Exchange:' : 'Available Balance by Exchange:';
        let tooltip = title + '\n';
        
        Object.entries(exchanges).forEach(([exchangeName, data]) => {
            const value = balanceType === 'balance' ? data.balance : (data.available_balance || data.available);
            const exchangeDisplayName = exchangeName.charAt(0).toUpperCase() + exchangeName.slice(1);
            tooltip += `${exchangeDisplayName}: $${value?.toFixed(2) || '0.00'}\n`;
        });
        
        return tooltip.trim();
    }
    
    generatePnLTooltip(exchanges) {
        let tooltip = 'Total PnL by Exchange:\n';
        
        Object.entries(exchanges).forEach(([exchangeName, data]) => {
            const value = data.total_pnl || 0;
            const exchangeDisplayName = exchangeName.charAt(0).toUpperCase() + exchangeName.slice(1);
            tooltip += `${exchangeDisplayName}: $${value.toFixed(2)}\n`;
        });
        
        return tooltip.trim();
    }
}

function updateExchangeBreakdownTable(exchangeData) {
    const tbody = document.getElementById('exchange-breakdown-table-body');
    tbody.innerHTML = '';
    if (!exchangeData || exchangeData.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td colspan="5" class="px-4 py-8 text-center text-gray-500">
                No per-exchange balance data available
            </td>
        `;
        tbody.appendChild(row);
        return;
    }
    exchangeData.forEach(exchange => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">${exchange.exchange || 'N/A'}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">$${(exchange.total_balance || 0).toFixed(2)}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">$${(exchange.available_balance || 0).toFixed(2)}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">$${(exchange.total_pnl || 0).toFixed(2)}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">${exchange.timestamp ? new Date(exchange.timestamp).toLocaleString() : 'N/A'}</td>
        `;
        tbody.appendChild(row);
    });
}

// Add this function for trade history rendering (similar to updateRecentTradesUI)
function updateTradeHistoryUI(trades) {
    const tbody = document.getElementById('trade-history-table-body');
    if (!tbody) return;
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="15" class="text-center">No trade history</td></tr>';
        return;
    }
    tbody.innerHTML = trades.map(trade => `
        <tr>
            <td class="px-2 sm:px-4 py-3 text-sm font-mono text-gray-900 align-middle" title="${trade.trade_id || 'N/A'}">${trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm font-semibold text-gray-900 align-middle">${trade.pair || 'N/A'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle">${trade.exchange || 'N/A'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle">${Dashboard.prototype.formatDateTime(trade.entry_time)}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">$${trade.entry_price?.toFixed(4) || '0.0000'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">${trade.position_size?.toFixed(6) || '0.000000'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle">${trade.exit_time ? Dashboard.prototype.formatDateTime(trade.exit_time) : 'N/A'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 text-right align-middle">${trade.exit_price ? Dashboard.prototype.formatCurrency(trade.exit_price) : 'N/A'}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle" title="${trade.entry_reason || 'N/A'}">${Dashboard.prototype.truncateText(trade.entry_reason || 'N/A', 30)}</td>
            <td class="px-2 sm:px-4 py-3 text-sm text-gray-500 align-middle" title="${trade.exit_reason || 'N/A'}">${Dashboard.prototype.truncateText(trade.exit_reason || 'N/A', 30)}</td>
            <td class="px-2 sm:px-4 py-3 text-sm font-medium ${Dashboard.prototype.getPnlClass(trade.realized_pnl)} text-right align-middle">${Dashboard.prototype.formatCurrency(trade.realized_pnl || 0)}</td>
            <td class="px-2 sm:px-4 py-3 text-sm font-medium ${Dashboard.prototype.getPnlClass(trade.realized_pnl_pct)} text-right align-middle">${trade.realized_pnl_pct !== undefined ? trade.realized_pnl_pct.toFixed(2) + '%' : 'N/A'}</td>
            <td class="profit-trigger-col align-middle">
              ${trade.profit_protection || 'inactive'}
              <div class="text-xs text-gray-500">${trade.profit_protection_trigger ? `Trigger: ${trade.profit_protection_trigger}` : 'None'}</div>
            </td>
            <td class="trailing-stop-col align-middle">
              ${trade.trail_stop || 'inactive'}
              <div class="text-xs text-gray-500">${trade.trail_stop_trigger ? `Trigger: ${trade.trail_stop_trigger}` : 'None'}</div>
            </td>
            <td class="highest-price-col text-right align-middle">
              ${trade.highest_price !== undefined && trade.highest_price !== null ? trade.highest_price.toFixed(6) : 'None'}
            </td>
        </tr>
    `).join('');
}

// Patch the dashboard to call updateTradeHistoryUI after fetching trade history
// (Assume the function that fetches trade history is called updateTradeHistory)
const origUpdateTradeHistory = Dashboard.prototype.updateTradeHistory;
Dashboard.prototype.updateTradeHistory = async function() {
    try {
        const response = await fetch('/api/trades/closed/history?limit=20');
        if (response.ok) {
            const data = await response.json();
            updateTradeHistoryUI(data.trades || []);
        }
    } catch (error) {
        console.error('Error updating trade history:', error);
    }
};

function updateExchangeStatus(exchanges) {
    const exchangeStatus = document.getElementById('exchange-status');
    exchangeStatus.innerHTML = '';
    Object.entries(exchanges).forEach(([name, info]) => {
        const exchangeDiv = document.createElement('div');
        // Use status string from API
        const isHealthy = info.status && info.status.toLowerCase() === 'healthy';
        const statusClass = isHealthy ? 'status-online' : 'status-offline';
        const statusText = isHealthy ? 'Online' : 'Offline';
        exchangeDiv.className = 'flex items-center p-4 border rounded-lg';
        exchangeDiv.innerHTML = `
            <div class="flex items-center">
                <span class="status-indicator ${statusClass}"></span>
                <div>
                    <h4 class="font-medium text-gray-900">${name.toUpperCase()}</h4>
                    <p class="text-sm text-gray-500">${statusText}</p>
                </div>
            </div>
        `;
        exchangeStatus.appendChild(exchangeDiv);
    });
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new Dashboard();
    document.querySelectorAll('.toggle-col').forEach(checkbox => {
      checkbox.addEventListener('change', function() {
        const colClass = this.dataset.col + '-col';
        document.querySelectorAll('.' + colClass).forEach(cell => {
          cell.style.display = this.checked ? '' : 'none';
        });
      });
    });
}); 