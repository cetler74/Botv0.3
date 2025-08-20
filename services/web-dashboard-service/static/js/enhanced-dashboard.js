/**
 * Enhanced Trading Bot Dashboard
 * Preserves all existing functionality while adding smooth animations and improved UX
 */

class EnhancedDashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 3000;
        this.updateInterval = null;
        this.currentPage = 1;
        this.itemsPerPage = 20;
        this.totalItems = 0;
        this.currentFilter = 'all';
        this.currentSort = { column: null, direction: 'asc' };
        this.animationQueue = [];
        
        // Recent Trades table properties
        this.currentTradesFilter = 'OPEN';
        this.currentTradesSearch = '';
        
        // Trading configuration
        this.tradingConfig = {
            profit_protection: {
                trigger_percentage: 0.01,
                activation_threshold: 0.008
            },
            trailing_stop: {
                trigger_percentage: 0.04,
                activation_threshold: 0.005
            }
        };
        
        this.initialize();
    }
    
    async initialize() {
        await this.fetchTradingConfig();
        this.setupWebSocket();
        this.setupEventListeners();
        this.setupAnimations();
        this.setupTableSorting();
        this.setupColumnToggles();
        this.setupRecentTradesControls();
        this.startPeriodicUpdates();
        this.fetchBotStatus();
    }

    async fetchTradingConfig() {
        try {
            const response = await fetch('/api/config/trading');
            if (response.ok) {
                this.tradingConfig = await response.json();
                console.log('Trading config loaded:', this.tradingConfig);
            } else {
                console.warn('Failed to fetch trading config, using defaults');
            }
        } catch (error) {
            console.error('Error fetching trading config:', error);
        }
    }

    setupAnimations() {
        // Animate cards on page load
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('animate-in');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });

        document.querySelectorAll('.animate-in').forEach((el) => {
            observer.observe(el);
        });

        // Add loading states
        this.setupLoadingStates();
    }

    setupLoadingStates() {
        const loadingElements = document.querySelectorAll('.loading');
        loadingElements.forEach(el => {
            el.style.opacity = '0.6';
        });
    }

    setupWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.updateConnectionStatus('connected');
            this.reconnectAttempts = 0;
            this.showNotification('Connected to server', 'success');
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
        const startBtns = ['start-btn', 'mobile-start-btn'];
        const stopBtns = ['stop-btn', 'mobile-stop-btn'];
        const emergencyBtns = ['emergency-btn', 'mobile-emergency-btn'];

        startBtns.forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', () => this.startTrading());
        });

        stopBtns.forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', () => this.stopTrading());
        });

        emergencyBtns.forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', () => this.showEmergencyModal());
        });

        // Mobile menu toggle
        const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
        if (mobileMenuToggle) {
            mobileMenuToggle.addEventListener('click', this.toggleMobileMenu);
        }

        // Emergency modal
        const emergencyModal = document.getElementById('emergency-modal');
        const confirmBtn = document.getElementById('confirm-emergency-stop');
        const cancelBtn = document.getElementById('cancel-emergency-stop');

        if (confirmBtn) confirmBtn.addEventListener('click', () => this.confirmEmergencyStop());
        if (cancelBtn) cancelBtn.addEventListener('click', () => this.hideEmergencyModal());

        // Close modal on backdrop click
        if (emergencyModal) {
            emergencyModal.addEventListener('click', (e) => {
                if (e.target === emergencyModal) this.hideEmergencyModal();
            });
        }

        // History controls
        const refreshBtn = document.getElementById('refresh-history');
        const filterSelect = document.getElementById('history-filter');
        const prevPageBtn = document.getElementById('prev-page');
        const nextPageBtn = document.getElementById('next-page');

        if (refreshBtn) refreshBtn.addEventListener('click', () => this.refreshTradeHistory());
        if (filterSelect) filterSelect.addEventListener('change', (e) => this.filterHistory(e.target.value));
        if (prevPageBtn) prevPageBtn.addEventListener('click', () => this.previousPage());
        if (nextPageBtn) nextPageBtn.addEventListener('click', () => this.nextPage());

        // Recent Trades controls
        const refreshTradesBtn = document.getElementById('refresh-trades');
        const tradesFilter = document.getElementById('trades-filter');
        const tradeSearch = document.getElementById('trade-search');

        if (refreshTradesBtn) refreshTradesBtn.addEventListener('click', () => this.refreshRecentTrades());
        if (tradesFilter) tradesFilter.addEventListener('change', (e) => this.filterRecentTrades(e.target.value));
        if (tradeSearch) tradeSearch.addEventListener('input', (e) => this.searchRecentTrades(e.target.value));

        // Exchange breakdown toggle
        const toggleExchangeBtn = document.getElementById('toggle-exchange-breakdown');
        if (toggleExchangeBtn) {
            toggleExchangeBtn.addEventListener('click', () => this.toggleExchangeBreakdown());
        }

        // ESC key to close modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.hideEmergencyModal();
            }
        });
    }

    setupTableSorting() {
        const sortableHeaders = document.querySelectorAll('.sortable');
        sortableHeaders.forEach(header => {
            header.addEventListener('click', () => {
                const column = header.dataset.sort;
                this.sortTable(column);
            });
        });
    }

    setupColumnToggles() {
        const toggles = document.querySelectorAll('.toggle-col');
        toggles.forEach(toggle => {
            // Set initial toggle states
            this.updateToggleVisual(toggle);
            
            toggle.addEventListener('change', (e) => {
                const colClass = e.target.dataset.col;
                const isVisible = e.target.checked;
                this.toggleColumn(colClass, isVisible);
                this.updateToggleVisual(toggle);
            });
        });
    }

    setupRecentTradesControls() {
        const recentTradesToggles = document.querySelectorAll('.toggle-recent-trades-col');
        recentTradesToggles.forEach(toggle => {
            // Set initial toggle states
            this.updateToggleVisual(toggle);
            
            toggle.addEventListener('change', (e) => {
                const colClass = e.target.dataset.col;
                const isVisible = e.target.checked;
                this.toggleColumn(colClass, isVisible);
                this.updateToggleVisual(toggle);
            });
        });
    }

    updateToggleVisual(toggle) {
        const parent = toggle.parentElement;
        const dot = parent.querySelector('.dot');
        const bg = parent.querySelector('div:first-child');
        
        if (toggle.checked) {
            bg.classList.add('bg-blue-600');
            bg.classList.remove('bg-gray-600');
            dot.style.transform = 'translateX(24px)';
        } else {
            bg.classList.add('bg-gray-600');
            bg.classList.remove('bg-blue-600');
            dot.style.transform = 'translateX(0)';
        }
    }

    toggleColumn(colClass, isVisible) {
        const columns = document.querySelectorAll(`.${colClass}-col`);
        columns.forEach(col => {
            col.style.display = isVisible ? '' : 'none';
        });
        
        // Animate the change
        columns.forEach((col, index) => {
            setTimeout(() => {
                col.style.opacity = isVisible ? '1' : '0';
                if (isVisible) {
                    col.classList.add('animate-fade-in');
                }
            }, index * 50);
        });
    }

    sortTable(column) {
        // Update sort direction
        if (this.currentSort.column === column) {
            this.currentSort.direction = this.currentSort.direction === 'asc' ? 'desc' : 'asc';
        } else {
            this.currentSort.column = column;
            this.currentSort.direction = 'asc';
        }

        // Update header visual indicators
        document.querySelectorAll('.sortable').forEach(header => {
            header.classList.remove('sort-asc', 'sort-desc');
            if (header.dataset.sort === column) {
                header.classList.add(`sort-${this.currentSort.direction}`);
            }
        });

        // Refresh data with new sort
        this.refreshTradeHistory();
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-status');
        const text = document.getElementById('connection-text');
        const statusBadge = document.getElementById('status-indicator');
        
        if (indicator && text) {
            indicator.className = `status-indicator status-${status === 'connected' ? 'online' : status === 'connecting' ? 'warning' : 'offline'}`;
            text.textContent = status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected';
        }

        if (statusBadge) {
            const badgeClass = status === 'connected' ? 'badge-success' : status === 'connecting' ? 'badge-warning' : 'badge-danger';
            statusBadge.className = `enhanced-badge ${badgeClass}`;
            statusBadge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        }
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
            case 'update':
                this.updatePortfolio(data.data || data.portfolio);
                break;
            case 'trading_status_update':
                this.updateTradingStatus(data.data);
                break;
            case 'risk_exposure_update':
                this.updateRiskExposure(data.data);
                break;
            case 'trade_update':
                this.refreshTradeHistory();
                this.refreshRecentTrades();
                break;
            case 'alert_update':
                this.showNotification(data.data.message, data.data.level || 'info');
                break;
            case 'emergency_stop':
                this.showNotification('Emergency stop executed!', 'warning');
                this.updateTradingStatus(data.data);
                break;
            default:
                console.log('Unknown message type:', data.type);
        }
    }

    async updatePortfolio(data) {
        if (!data) {
            try {
                const response = await fetch('/api/portfolio');
                if (response.ok) {
                    data = await response.json();
                } else {
                    console.error('Failed to fetch portfolio data');
                    return;
                }
            } catch (error) {
                console.error('Error fetching portfolio:', error);
                return;
            }
        }

        this.updatePortfolioUI(data);
    }
    
    updatePortfolioUI(data) {
        const elements = {
            'total-balance': data.total_balance || 0,
            'available-balance': this.calculateAvailableBalance(data),
            'total-pnl': data.total_pnl || 0,
            'daily-pnl': data.daily_pnl || 0,
            'open-trades': data.active_trades || 0,
            'total-unrealized-pnl': data.total_unrealized_pnl || 0,
            'win-rate': `${(data.win_rate || 0).toFixed(1)}%`
        };

        // Update each element with animation
        Object.entries(elements).forEach(([id, value], index) => {
            setTimeout(() => {
                const element = document.getElementById(id);
                if (element) {
                    const isMonetary = typeof value === 'number' && id !== 'open-trades';
                    const displayValue = isMonetary ? this.formatCurrency(value) : value;
                    
                    // Animate value change
                    element.style.transform = 'scale(1.05)';
                    element.style.transition = 'transform 0.2s ease';
                    
                    setTimeout(() => {
                        element.textContent = displayValue;
                        element.style.transform = 'scale(1)';
                        
                        // Apply PnL coloring
                        if (id.includes('pnl') && typeof value === 'number') {
                            element.className = element.className.replace(/text-(profit|loss|neutral)/, '');
                            if (value > 0) {
                                element.classList.add('text-profit');
                            } else if (value < 0) {
                                element.classList.add('text-loss');
                            } else {
                                element.classList.add('text-neutral');
                            }
                        }
                    }, 100);
                }
            }, index * 50);
        });

        // Update tooltips
        this.updateTooltips(data);
    }

    calculateAvailableBalance(data) {
        if (!data.exchanges) return 0;
        return Object.values(data.exchanges).reduce((sum, ex) => {
            return sum + (ex.available_balance || ex.available || 0);
        }, 0);
    }

    updateTooltips(data) {
        if (!data.exchanges) return;

        const totalBalance = document.getElementById('total-balance');
        const availableBalance = document.getElementById('available-balance');
        const totalPnl = document.getElementById('total-pnl');

        if (totalBalance) {
            totalBalance.setAttribute('data-tooltip', this.generateBalanceTooltip(data.exchanges, 'balance'));
        }
        if (availableBalance) {
            availableBalance.setAttribute('data-tooltip', this.generateBalanceTooltip(data.exchanges, 'available_balance'));
        }
        if (totalPnl) {
            totalPnl.setAttribute('data-tooltip', this.generatePnLTooltip(data.exchanges));
        }
    }

    generateBalanceTooltip(exchanges, balanceType) {
        const title = balanceType === 'balance' ? 'Total Balance by Exchange:' : 'Available Balance by Exchange:';
        let tooltip = title + '\\n';
        
        Object.entries(exchanges).forEach(([exchangeName, data]) => {
            const value = balanceType === 'balance' ? data.balance : (data.available_balance || data.available);
            const exchangeDisplayName = exchangeName.charAt(0).toUpperCase() + exchangeName.slice(1);
            tooltip += `${exchangeDisplayName}: ${this.formatCurrency(value || 0)}\\n`;
        });
        
        return tooltip.trim();
    }

    generatePnLTooltip(exchanges) {
        let tooltip = 'Total PnL by Exchange:\\n';
        
        Object.entries(exchanges).forEach(([exchangeName, data]) => {
            const value = data.total_pnl || 0;
            const exchangeDisplayName = exchangeName.charAt(0).toUpperCase() + exchangeName.slice(1);
            tooltip += `${exchangeDisplayName}: ${this.formatCurrency(value)}\\n`;
        });
        
        return tooltip.trim();
    }

    async fetchBotStatus() {
        try {
            const response = await fetch('/api/bot-status');
            if (!response.ok) throw new Error('Failed to fetch bot status');
            const data = await response.json();
            
            this.updateBotStatusUI(data);
        } catch (error) {
            console.error('Error fetching bot status:', error);
            this.updateBotStatusError();
        }
    }

    updateBotStatusUI(data) {
        // Update service status
        const servicesDiv = document.getElementById('bot-status-services');
        if (servicesDiv) {
            servicesDiv.innerHTML = '';
            Object.entries(data.services || {}).forEach(([name, status]) => {
                const badgeClass = status === 'healthy' ? 'badge-success' : 
                                 status.startsWith('unhealthy') ? 'badge-warning' : 
                                 'badge-danger';
                
                const badge = document.createElement('span');
                badge.className = `enhanced-badge ${badgeClass}`;
                badge.textContent = `${name}: ${status}`;
                badge.style.opacity = '0';
                badge.style.transform = 'translateY(10px)';
                
                servicesDiv.appendChild(badge);
                
                // Animate in
                setTimeout(() => {
                    badge.style.opacity = '1';
                    badge.style.transform = 'translateY(0)';
                    badge.style.transition = 'all 0.3s ease';
                }, Object.keys(data.services).indexOf(name) * 100);
            });
        }

        // Update trading status
        const trading = data.trading_status || {};
        const elements = {
            'bot-trading-status': trading.status || 'unknown',
            'bot-cycle-count': trading.cycle_count !== undefined ? trading.cycle_count : 'N/A',
            'bot-uptime': this.formatUptime(trading.uptime)
        };

        Object.entries(elements).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = value;
                element.classList.remove('loading');
            }
        });
    }

    updateBotStatusError() {
        const servicesDiv = document.getElementById('bot-status-services');
        if (servicesDiv) {
            servicesDiv.innerHTML = '<span class="enhanced-badge badge-danger">Error loading bot status</span>';
        }
        
        ['bot-trading-status', 'bot-cycle-count', 'bot-uptime'].forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = 'N/A';
                element.classList.remove('loading');
            }
        });
    }

    formatUptime(uptime) {
        if (uptime === undefined) return 'N/A';
        
        const seconds = parseInt(uptime);
        const d = Math.floor(seconds / (3600 * 24));
        const h = Math.floor((seconds % (3600 * 24)) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        
        let uptimeStr = '';
        if (d > 0) uptimeStr += `${d}d `;
        if (h > 0 || uptimeStr) uptimeStr += `${h}h `;
        if (m > 0 || uptimeStr) uptimeStr += `${m}m `;
        uptimeStr += `${s}s`;
        
        return uptimeStr;
    }

    async refreshTradeHistory() {
        const tbody = document.getElementById('trade-history-table-body');
        if (tbody) {
            tbody.classList.add('loading');
            tbody.innerHTML = `
                <tr>
                    <td colspan="15" class="text-center py-8">
                        <div class="flex items-center justify-center">
                            <i class="fas fa-spinner spinner text-blue-500 mr-2"></i>
                            Loading trade history...
                        </div>
                    </td>
                </tr>
            `;
        }

        try {
            const params = new URLSearchParams({
                page: this.currentPage,
                limit: this.itemsPerPage,
                filter: this.currentFilter
            });

            if (this.currentSort.column) {
                params.set('sort_by', this.currentSort.column);
                params.set('sort_dir', this.currentSort.direction);
            }

            const response = await fetch(`/api/trades?${params}`);
            if (response.ok) {
                const data = await response.json();
                this.updateTradeHistoryUI(data);
            } else {
                throw new Error('Failed to fetch trade history');
            }
        } catch (error) {
            console.error('Error fetching trade history:', error);
            this.updateTradeHistoryError();
        } finally {
            if (tbody) {
                tbody.classList.remove('loading');
            }
        }
    }

    updateTradeHistoryUI(data) {
        const tbody = document.getElementById('trade-history-table-body');
        const summary = document.getElementById('history-summary');
        const pageInfo = document.getElementById('page-info');
        
        if (!tbody) return;

        this.totalItems = data.total || 0;
        const trades = data.trades || [];

        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="15" class="text-center py-8 text-gray-500">No trades found</td></tr>';
        } else {
            tbody.innerHTML = trades.map((trade, index) => {
                const row = document.createElement('tr');
                row.style.opacity = '0';
                row.style.transform = 'translateY(10px)';
                
                setTimeout(() => {
                    row.style.opacity = '1';
                    row.style.transform = 'translateY(0)';
                    row.style.transition = 'all 0.3s ease';
                }, index * 50);

                return this.createTradeRow(trade);
            }).join('');
        }

        // Update pagination info
        if (summary) {
            const start = (this.currentPage - 1) * this.itemsPerPage + 1;
            const end = Math.min(this.currentPage * this.itemsPerPage, this.totalItems);
            summary.textContent = `Showing ${start}-${end} of ${this.totalItems} trades`;
        }

        if (pageInfo) {
            const totalPages = Math.ceil(this.totalItems / this.itemsPerPage);
            pageInfo.textContent = `Page ${this.currentPage} of ${totalPages}`;
        }

        this.updatePaginationButtons();
    }

    createTradeRow(trade) {
        const statusClass = trade.status === 'OPEN' ? 'badge-success' : 
                           trade.status === 'CLOSED' ? 'badge-secondary' : 'badge-warning';
        
        const pnlClass = this.getPnlClass(trade.realized_pnl);
        const unrealizedPnlClass = this.getPnlClass(trade.unrealized_pnl);

        return `
            <tr class="enhanced-table-row">
                <td class="font-mono text-sm" title="${trade.trade_id || 'N/A'}">
                    ${trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A'}
                </td>
                <td class="font-semibold">${trade.pair || 'N/A'}</td>
                <td class="mobile-hidden">${trade.exchange || 'N/A'}</td>
                <td class="mobile-hidden text-sm">${this.formatDateTime(trade.entry_time)}</td>
                <td class="text-right font-mono">${this.formatCurrency(trade.entry_price, 4)}</td>
                <td class="text-right font-mono">${(trade.position_size || 0).toFixed(6)}</td>
                <td class="mobile-hidden text-sm">${this.formatDateTime(trade.exit_time)}</td>
                <td class="text-right font-mono">${this.formatCurrency(trade.exit_price, 4)}</td>
                <td class="mobile-hidden text-sm" title="${trade.entry_reason || 'N/A'}">
                    ${this.truncateText(trade.entry_reason || 'N/A', 30)}
                </td>
                <td class="mobile-hidden text-sm" title="${trade.exit_reason || 'N/A'}">
                    ${this.truncateText(trade.exit_reason || 'N/A', 30)}
                </td>
                <td class="text-right font-mono ${pnlClass}">
                    ${this.formatCurrency(trade.realized_pnl || 0)}
                    ${this.calculatePnlPercentage(trade)}
                </td>
                <td class="text-right font-mono ${unrealizedPnlClass}">
                    ${this.calculatePnlPercentage(trade)}
                </td>
                <td class="profit-trigger-col">
                    <div class="enhanced-badge ${trade.profit_protection === 'active' ? 'badge-success' : 'badge-danger'}">
                        ${trade.profit_protection || 'inactive'}
                    </div>
                    ${trade.profit_protection_trigger ? `<div class="text-xs text-gray-500 mt-1">Trigger: ${trade.profit_protection_trigger}</div>` : ''}
                </td>
                <td class="trailing-stop-col">
                    <div class="enhanced-badge ${trade.trail_stop === 'active' ? 'badge-success' : 'badge-danger'}">
                        ${trade.trail_stop || 'inactive'}
                    </div>
                    ${trade.trail_stop_trigger ? `<div class="text-xs text-gray-500 mt-1">Trigger: ${trade.trail_stop_trigger}</div>` : ''}
                </td>
                <td class="highest-price-col text-right font-mono">
                    ${trade.highest_price !== undefined && trade.highest_price !== null ? 
                      this.formatCurrency(trade.highest_price, 6) : 'None'}
                </td>
            </tr>
        `;
    }

    updateTradeHistoryError() {
        const tbody = document.getElementById('trade-history-table-body');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="15" class="text-center py-8 text-red-500">
                        <div class="flex items-center justify-center">
                            <i class="fas fa-exclamation-triangle mr-2"></i>
                            Error loading trade history
                        </div>
                    </td>
                </tr>
            `;
        }
    }

    updatePaginationButtons() {
        const prevBtn = document.getElementById('prev-page');
        const nextBtn = document.getElementById('next-page');
        const totalPages = Math.ceil(this.totalItems / this.itemsPerPage);

        if (prevBtn) {
            prevBtn.disabled = this.currentPage <= 1;
            prevBtn.classList.toggle('opacity-50', prevBtn.disabled);
        }

        if (nextBtn) {
            nextBtn.disabled = this.currentPage >= totalPages;
            nextBtn.classList.toggle('opacity-50', nextBtn.disabled);
        }
    }

    filterHistory(filter) {
        this.currentFilter = filter;
        this.currentPage = 1;
        this.refreshTradeHistory();
    }

    previousPage() {
        if (this.currentPage > 1) {
            this.currentPage--;
            this.refreshTradeHistory();
        }
    }

    nextPage() {
        const totalPages = Math.ceil(this.totalItems / this.itemsPerPage);
        if (this.currentPage < totalPages) {
            this.currentPage++;
            this.refreshTradeHistory();
        }
    }

    // Trading controls
    async startTrading() {
        try {
            const response = await fetch('/api/trading/start', { method: 'POST' });
            if (response.ok) {
                this.showNotification('Trading started successfully', 'success');
            } else {
                throw new Error('Failed to start trading');
            }
        } catch (error) {
            this.showNotification('Error starting trading: ' + error.message, 'error');
        }
    }

    async stopTrading() {
        try {
            const response = await fetch('/api/trading/stop', { method: 'POST' });
            if (response.ok) {
                this.showNotification('Trading stopped successfully', 'warning');
            } else {
                throw new Error('Failed to stop trading');
            }
        } catch (error) {
            this.showNotification('Error stopping trading: ' + error.message, 'error');
        }
    }

    showEmergencyModal() {
        const modal = document.getElementById('emergency-modal');
        if (modal) {
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }
    }

    hideEmergencyModal() {
        const modal = document.getElementById('emergency-modal');
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }

    async confirmEmergencyStop() {
        try {
            const response = await fetch('/api/trading/emergency-stop', { method: 'POST' });
            if (response.ok) {
                this.showNotification('Emergency stop executed!', 'warning');
                this.hideEmergencyModal();
            } else {
                throw new Error('Failed to execute emergency stop');
            }
        } catch (error) {
            this.showNotification('Error executing emergency stop: ' + error.message, 'error');
        }
    }

    toggleMobileMenu() {
        const menu = document.getElementById('mobile-menu');
        if (menu) {
            menu.classList.toggle('hidden');
        }
    }

    showNotification(message, type = 'info') {
        // Create notification element if it doesn't exist
        let notification = document.getElementById('notification');
        if (!notification) {
            notification = document.createElement('div');
            notification.id = 'notification';
            notification.className = 'fixed top-4 right-4 z-50 max-w-sm';
            document.body.appendChild(notification);
        }

        const typeClass = {
            'success': 'bg-green-500',
            'error': 'bg-red-500', 
            'warning': 'bg-yellow-500',
            'info': 'bg-blue-500'
        }[type] || 'bg-blue-500';

        const icon = {
            'success': 'fas fa-check-circle',
            'error': 'fas fa-exclamation-circle',
            'warning': 'fas fa-exclamation-triangle', 
            'info': 'fas fa-info-circle'
        }[type] || 'fas fa-info-circle';

        const notificationEl = document.createElement('div');
        notificationEl.className = `${typeClass} text-white px-4 py-3 rounded-lg shadow-lg mb-2 transform translate-x-full transition-transform duration-300 ease-out`;
        notificationEl.innerHTML = `
            <div class="flex items-center">
                <i class="${icon} mr-2"></i>
                <span class="text-sm font-medium">${message}</span>
                <button class="ml-4 text-white hover:text-gray-200" onclick="this.parentElement.parentElement.remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;

        notification.appendChild(notificationEl);

        // Animate in
        setTimeout(() => {
            notificationEl.style.transform = 'translateX(0)';
        }, 100);

        // Auto remove after 5 seconds
        setTimeout(() => {
            notificationEl.style.transform = 'translateX(100%)';
            setTimeout(() => {
                if (notificationEl.parentNode) {
                    notificationEl.remove();
                }
            }, 300);
        }, 5000);
    }

    async refreshRecentTrades() {
        const tbody = document.getElementById('trades-table-body');
        if (tbody) {
            tbody.classList.add('loading');
            tbody.innerHTML = `
                <tr>
                    <td colspan="14" class="text-center py-8">
                        <div class="flex items-center justify-center">
                            <i class="fas fa-spinner spinner text-blue-500 mr-2"></i>
                            Loading recent trades...
                        </div>
                    </td>
                </tr>
            `;
        }

        try {
            const params = new URLSearchParams({
                limit: 100 // Get more trades to ensure we show all open trades
            });

            // If filtering to OPEN trades only, request them specifically from API
            if (this.currentTradesFilter === 'OPEN') {
                params.set('status', 'OPEN');
                params.set('limit', 50); // We can use lower limit when filtering server-side
            }

            const response = await fetch(`/api/trades?${params}`);
            if (response.ok) {
                const data = await response.json();
                this.updateRecentTradesUI(data.trades || []);
            } else {
                throw new Error('Failed to fetch recent trades');
            }
        } catch (error) {
            console.error('Error fetching recent trades:', error);
            this.updateRecentTradesError();
        } finally {
            if (tbody) {
                tbody.classList.remove('loading');
            }
        }
    }

    updateRecentTradesUI(trades) {
        const tbody = document.getElementById('trades-table-body');
        if (!tbody) return;

        // Apply current filters
        const filteredTrades = this.applyRecentTradesFilter(trades);

        if (filteredTrades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="14" class="text-center py-8 text-gray-500">No trades found</td></tr>';
            return;
        }

        tbody.innerHTML = filteredTrades.map((trade, index) => {
            const row = document.createElement('tr');
            row.style.opacity = '0';
            row.style.transform = 'translateY(10px)';
            
            setTimeout(() => {
                row.style.opacity = '1';
                row.style.transform = 'translateY(0)';
                row.style.transition = 'all 0.3s ease';
            }, index * 30);

            return this.createRecentTradeRow(trade);
        }).join('');
    }

    createRecentTradeRow(trade) {
        const statusClass = trade.status === 'OPEN' ? 'badge-success' : 
                           trade.status === 'CLOSED' ? 'badge-danger' : 'badge-warning';
        
        const pnlClass = this.getPnlClass(trade.unrealized_pnl);
        
        // Apply row styling for CLOSED trades (faded out)
        const rowClass = trade.status === 'CLOSED' ? 'opacity-60 bg-gray-50' : '';

        return `
            <tr class="enhanced-table-row ${rowClass}">
                <td class="font-mono text-sm" title="${trade.trade_id || 'N/A'}">
                    ${trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A'}
                </td>
                <td class="font-semibold">${trade.pair || 'N/A'}</td>
                <td class="mobile-hidden">${trade.exchange || 'N/A'}</td>
                <td class="mobile-hidden text-sm">${this.formatDateTime(trade.entry_time)}</td>
                <td class="text-right font-mono">${this.formatCurrency(trade.entry_price, 4)}</td>
                <td class="text-right font-mono">${this.formatCurrency(trade.current_price, 4)}</td>
                <td class="text-right font-mono">${(trade.position_size || 0).toFixed(6)}</td>
                <td class="text-right font-mono" id="notional-value-${trade.trade_id || 'N/A'}">
                    ${this.formatCurrency(((trade.entry_price || 0) * (trade.position_size || 0)), 2)}
                </td>
                <td class="text-right font-mono ${pnlClass}">
                    ${this.formatCurrency(trade.unrealized_pnl || 0, 2)}
                    ${this.calculatePnlPercentage(trade)}
                </td>
                <td>
                    <div class="enhanced-badge ${statusClass}">
                        ${trade.status || 'N/A'}
                    </div>
                </td>
                <td class="mobile-hidden text-sm" title="${trade.entry_reason || 'N/A'}">
                    ${this.truncateText(trade.entry_reason || 'N/A', 30)}
                </td>
                <td class="profit-trigger-col">
                    <div class="enhanced-badge ${trade.profit_protection === 'active' ? 'badge-success' : 'badge-danger'}">
                        ${trade.profit_protection || 'inactive'}
                    </div>
                    ${trade.profit_protection === 'active' && trade.profit_protection_trigger ? `
                        <div class="text-xs text-gray-500 mt-1">
                            Exit: ${this.formatCurrency(trade.profit_protection_trigger, 4)}
                        </div>
                    ` : trade.profit_protection === 'inactive' && trade.current_price ? `
                        <div class="text-xs text-gray-600 mt-1">
                            Activates: ${this.formatCurrency(trade.current_price * (1 + this.tradingConfig.profit_protection.trigger_percentage), 4)} (+${(this.tradingConfig.profit_protection.trigger_percentage * 100).toFixed(1)}%)
                        </div>
                    ` : ''}
                </td>
                <td class="trailing-stop-col">
                    <div class="enhanced-badge ${trade.trail_stop === 'active' ? 'badge-success' : 'badge-danger'}">
                        ${trade.trail_stop || 'inactive'}
                    </div>
                    ${trade.trail_stop === 'active' && trade.trail_stop_trigger ? `
                        <div class="text-xs text-gray-500 mt-1">
                            Exit: ${this.formatCurrency(trade.trail_stop_trigger, 4)}
                        </div>
                        <div class="text-xs text-blue-500 mt-1">
                            Trailing: ${(this.tradingConfig.trailing_stop.step_percentage * 100).toFixed(2)}% distance
                        </div>
                    ` : trade.trail_stop === 'inactive' && trade.current_price ? `
                        <div class="text-xs text-gray-600 mt-1">
                            Activates: ${this.formatCurrency(trade.current_price * (1 + this.tradingConfig.trailing_stop.activation_threshold), 4)} (+${(this.tradingConfig.trailing_stop.activation_threshold * 100).toFixed(1)}%)
                        </div>
                    ` : ''}
                </td>
                <td class="highest-price-col text-right font-mono">
                    ${trade.highest_price !== undefined && trade.highest_price !== null ? 
                      this.formatCurrency(trade.highest_price, 6) : 'None'}
                </td>
            </tr>
        `;
    }

    applyRecentTradesFilter(trades) {
        let filtered = trades;

        // Apply status filter
        if (this.currentTradesFilter !== 'all') {
            filtered = filtered.filter(trade => trade.status === this.currentTradesFilter);
        }

        // Apply search filter
        if (this.currentTradesSearch) {
            const searchLower = this.currentTradesSearch.toLowerCase();
            filtered = filtered.filter(trade => 
                (trade.trade_id && trade.trade_id.toLowerCase().includes(searchLower)) ||
                (trade.pair && trade.pair.toLowerCase().includes(searchLower))
            );
        }

        return filtered;
    }

    updateRecentTradesError() {
        const tbody = document.getElementById('trades-table-body');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="14" class="text-center py-8 text-red-500">
                        <div class="flex items-center justify-center">
                            <i class="fas fa-exclamation-triangle mr-2"></i>
                            Error loading recent trades
                        </div>
                    </td>
                </tr>
            `;
        }
    }

    filterRecentTrades(filter) {
        this.currentTradesFilter = filter;
        this.refreshRecentTrades();
    }

    searchRecentTrades(searchTerm) {
        this.currentTradesSearch = searchTerm;
        this.refreshRecentTrades();
    }

    startPeriodicUpdates() {
        // Update data every 30 seconds
        this.updateInterval = setInterval(() => {
            this.updatePortfolio();
            this.fetchBotStatus();
            this.refreshRecentTrades();
            this.updateExchangeStatus();
            this.updateStrategyPerformance();
        }, 30000);
        
        // Initial updates
        this.updatePortfolio();
        this.refreshTradeHistory();
        this.refreshRecentTrades();
        this.updateExchangeStatus();
        this.updateStrategyPerformance();
    }

    toggleExchangeBreakdown() {
        const breakdownSection = document.getElementById('exchange-breakdown-section');
        const button = document.getElementById('toggle-exchange-breakdown');
        const buttonSpan = button.querySelector('span');
        
        if (breakdownSection && button) {
            if (breakdownSection.style.display === 'none') {
                breakdownSection.style.display = 'block';
                buttonSpan.textContent = 'Hide per-exchange breakdown';
                // Fetch exchange data when showing
                this.updateExchangeBreakdown();
            } else {
                breakdownSection.style.display = 'none';
                buttonSpan.textContent = 'Show per-exchange breakdown';
            }
        }
    }

    async updateExchangeBreakdown() {
        try {
            const response = await fetch('/api/portfolio');
            if (response.ok) {
                const data = await response.json();
                // Convert exchanges object to array format
                const exchangesArray = data.exchanges ? Object.entries(data.exchanges).map(([exchange, data]) => ({
                    exchange: exchange,
                    total_balance: data.balance,
                    available_balance: data.available_balance || data.available,
                    total_pnl: data.total_pnl,
                    timestamp: data.timestamp
                })) : [];
                this.updateExchangeBreakdownTable(exchangesArray);
            }
        } catch (error) {
            console.error('Error fetching exchange breakdown:', error);
        }
    }

    updateExchangeBreakdownTable(exchangeData) {
        const tbody = document.getElementById('exchange-breakdown-table-body');
        if (!tbody) return;

        if (!exchangeData || exchangeData.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" class="text-center py-8 text-gray-500">
                        <div class="flex items-center justify-center">
                            <i class="fas fa-info-circle mr-2"></i>
                            No per-exchange balance data available
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = exchangeData.map((exchange, index) => {
            return `
                <tr class="enhanced-table-row" style="opacity: 0; transform: translateY(10px); transition: all 0.3s ease ${index * 50}ms;">
                    <td class="font-semibold capitalize">
                        <div class="flex items-center">
                            <div class="w-3 h-3 rounded-full bg-blue-500 mr-2"></div>
                            ${exchange.exchange || 'N/A'}
                        </div>
                    </td>
                    <td class="text-right font-mono">${this.formatCurrency(exchange.total_balance || 0, 2)}</td>
                    <td class="text-right font-mono">${this.formatCurrency(exchange.available_balance || 0, 2)}</td>
                    <td class="text-right font-mono ${this.getPnlClass(exchange.total_pnl || 0)}">
                        ${this.formatCurrency(exchange.total_pnl || 0, 2)}
                    </td>
                    <td class="mobile-hidden text-center text-sm text-gray-500">
                        ${exchange.timestamp ? this.formatDateTime(exchange.timestamp) : 'N/A'}
                    </td>
                </tr>
            `;
        }).join('');
        
        // Animate rows after insertion
        setTimeout(() => {
            const rows = tbody.querySelectorAll('tr');
            rows.forEach(row => {
                row.style.opacity = '1';
                row.style.transform = 'translateY(0)';
            });
        }, 100);
    }

    // Utility methods
    formatCurrency(value, decimals = 2) {
        if (value === null || value === undefined) return '$0.00';
        return `$${Number(value).toFixed(decimals)}`;
    }

    formatDateTime(dateString) {
        if (!dateString) return 'N/A';
        try {
            const date = new Date(dateString);
            return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { 
                hour: '2-digit', 
                minute: '2-digit' 
            });
        } catch {
            return 'N/A';
        }
    }

    getPnlClass(value) {
        if (value > 0) return 'text-profit';
        if (value < 0) return 'text-loss';
        return 'text-neutral';
    }

    calculatePnlPercentage(trade) {
        if (!trade.entry_price || !trade.position_size) return '';
        const pnl = trade.realized_pnl || trade.unrealized_pnl || 0;
        const percentage = (pnl / (trade.entry_price * trade.position_size)) * 100;
        return ` (${percentage > 0 ? '+' : ''}${percentage.toFixed(2)}%)`;
    }

    truncateText(text, length) {
        if (!text || text.length <= length) return text;
        return text.substring(0, length) + '...';
    }

    async updateExchangeStatus() {
        try {
            // Get exchange performance data
            const performanceResponse = await fetch('/api/v1/performance/metrics');
            if (!performanceResponse.ok) return;
            const performanceData = await performanceResponse.json();

            // Get exchange balances
            const portfolioResponse = await fetch('/api/portfolio');
            if (!portfolioResponse.ok) return;
            const portfolioData = await portfolioResponse.json();

            // Get trading pairs for each exchange
            const exchangePairs = {};
            const exchangesList = Object.keys(portfolioData.exchanges || {});
            for (const exchange of exchangesList) {
                try {
                    const pairsResponse = await fetch(`/api/pairs/${exchange}`);
                    if (pairsResponse.ok) {
                        const pairsData = await pairsResponse.json();
                        exchangePairs[exchange] = pairsData.pairs || [];
                    } else {
                        exchangePairs[exchange] = [];
                    }
                } catch (error) {
                    exchangePairs[exchange] = [];
                }
            }

            const exchangeStatusContainer = document.getElementById('exchange-status');
            if (!exchangeStatusContainer) return;

            const exchanges = portfolioData.exchanges || {};
            const exchangeBreakdown = performanceData.exchange_breakdown || {};

            const exchangeCards = Object.keys(exchanges).map(exchange => {
                const balance = exchanges[exchange];
                const performance = exchangeBreakdown[exchange] || {};
                const successRate = performance.success_rate || 0;
                const totalOrders = performance.total || 0;
                const filledOrders = performance.filled || 0;
                const pairs = exchangePairs[exchange] || [];
                
                // Determine status color based on filled orders and success rate
                let statusColor = 'gray';
                let statusText = 'Unknown';
                
                if (totalOrders === 0) {
                    statusColor = 'blue';
                    statusText = 'Ready';
                } else if (filledOrders === 0 && totalOrders > 0) {
                    statusColor = 'yellow';
                    statusText = 'Pending';
                } else if (successRate >= 80) {
                    statusColor = 'green';
                    statusText = 'Excellent';
                } else if (successRate >= 60) {
                    statusColor = 'blue';
                    statusText = 'Good';
                } else if (successRate >= 40) {
                    statusColor = 'yellow';
                    statusText = 'Fair';
                } else if (successRate > 0) {
                    statusColor = 'red';
                    statusText = 'Poor';
                } else {
                    statusColor = 'gray';
                    statusText = 'No Data';
                }

                // Format pairs display - show all pairs
                const pairsDisplay = pairs.length > 0 
                    ? pairs.join(', ')
                    : 'No pairs selected';

                return `
                    <div class="enhanced-metric-card bg-white border-l-4 border-${statusColor}-500 p-4 rounded-r-lg shadow-sm">
                        <div class="flex items-center justify-between">
                            <div>
                                <h4 class="font-semibold text-gray-900 capitalize">${exchange}</h4>
                                <p class="text-2xl font-bold text-gray-900">${this.formatCurrency(balance.available_balance || balance.available || 0, 2)}</p>
                                <div class="flex items-center text-sm text-gray-600 mt-1">
                                    <div class="w-2 h-2 rounded-full bg-${statusColor}-500 mr-2"></div>
                                    <span>${statusText}</span>
                                </div>
                                <div class="text-xs text-gray-500 mt-1" title="${pairs.join(', ')}">
                                    ${pairs.length} pairs: ${pairsDisplay}
                                </div>
                            </div>
                            <div class="text-right text-sm">
                                <div class="text-gray-600">Orders: ${totalOrders}</div>
                                <div class="text-gray-600">Filled: ${filledOrders} (${successRate > 0 ? successRate.toFixed(1) : '0.0'}%)</div>
                                <div class="text-gray-500 mt-1">${new Date(balance.timestamp).toLocaleTimeString()}</div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            exchangeStatusContainer.innerHTML = exchangeCards;

            // Animate cards
            const cards = exchangeStatusContainer.querySelectorAll('.enhanced-metric-card');
            cards.forEach((card, index) => {
                card.style.opacity = '0';
                card.style.transform = 'translateY(10px)';
                setTimeout(() => {
                    card.style.transition = 'all 0.3s ease';
                    card.style.opacity = '1';
                    card.style.transform = 'translateY(0)';
                }, index * 100);
            });

        } catch (error) {
            console.error('Error updating exchange status:', error);
        }
    }

    async updateStrategyPerformance() {
        try {
            // Get trade data to calculate strategy performance
            const tradesResponse = await fetch('/api/trades?limit=200');
            if (!tradesResponse.ok) return;
            const tradesData = await tradesResponse.json();

            const strategyPerformanceContainer = document.getElementById('strategy-performance');
            if (!strategyPerformanceContainer) return;

            // Calculate strategy statistics
            const strategies = {};
            const trades = tradesData.trades || [];

            trades.forEach(trade => {
                const strategy = trade.strategy || 'unknown';
                const status = trade.status;
                const realizedPnl = parseFloat(trade.realized_pnl || 0);

                if (!strategies[strategy]) {
                    strategies[strategy] = {
                        total: 0,
                        wins: 0,
                        losses: 0,
                        open: 0,
                        totalPnl: 0,
                        enabled: true
                    };
                }

                strategies[strategy].total += 1;

                if (status === 'OPEN') {
                    strategies[strategy].open += 1;
                } else if (status === 'CLOSED' && realizedPnl !== 0) {
                    strategies[strategy].totalPnl += realizedPnl;
                    if (realizedPnl > 0) {
                        strategies[strategy].wins += 1;
                    } else {
                        strategies[strategy].losses += 1;
                    }
                }
            });

            const strategyCards = Object.keys(strategies).map(strategyName => {
                const stats = strategies[strategyName];
                const closedTrades = stats.wins + stats.losses;
                const winRate = closedTrades > 0 ? (stats.wins / closedTrades * 100) : 0;
                
                // Determine performance color
                let performanceColor = 'gray';
                if (closedTrades === 0) {
                    performanceColor = 'gray';
                } else if (winRate >= 60) {
                    performanceColor = 'green';
                } else if (winRate >= 50) {
                    performanceColor = 'blue';
                } else if (winRate >= 40) {
                    performanceColor = 'yellow';
                } else {
                    performanceColor = 'red';
                }

                return `
                    <div class="enhanced-metric-card bg-white border-l-4 border-${performanceColor}-500 p-4 rounded-r-lg shadow-sm">
                        <div class="mb-2">
                            <h4 class="font-semibold text-gray-900 text-sm capitalize">${strategyName.replace(/_/g, ' ')}</h4>
                        </div>
                        <div class="space-y-1">
                            <div class="flex justify-between text-sm">
                                <span class="text-gray-600">Total:</span>
                                <span class="font-medium">${stats.total}</span>
                            </div>
                            <div class="flex justify-between text-sm">
                                <span class="text-gray-600">Open:</span>
                                <span class="font-medium">${stats.open}</span>
                            </div>
                            <div class="flex justify-between text-sm">
                                <span class="text-gray-600">Win Rate:</span>
                                <span class="font-medium ${this.getPnlClass(winRate - 50)}">${winRate.toFixed(1)}%</span>
                            </div>
                            <div class="flex justify-between text-sm">
                                <span class="text-gray-600">PnL:</span>
                                <span class="font-medium ${this.getPnlClass(stats.totalPnl)}">${this.formatCurrency(stats.totalPnl, 2)}</span>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            strategyPerformanceContainer.innerHTML = strategyCards;

            // Animate cards
            const cards = strategyPerformanceContainer.querySelectorAll('.enhanced-metric-card');
            cards.forEach((card, index) => {
                card.style.opacity = '0';
                card.style.transform = 'translateY(10px)';
                setTimeout(() => {
                    card.style.transition = 'all 0.3s ease';
                    card.style.opacity = '1';
                    card.style.transform = 'translateY(0)';
                }, index * 100);
            });

        } catch (error) {
            console.error('Error updating strategy performance:', error);
        }
    }

    // Cleanup on page unload
    destroy() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        if (this.ws) {
            this.ws.close();
        }
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new EnhancedDashboard();
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (window.dashboard) {
        window.dashboard.destroy();
    }
});

// Expose some functions globally for compatibility
window.fetchPortfolio = () => window.dashboard?.updatePortfolio();
window.fetchBotStatus = () => window.dashboard?.fetchBotStatus();
window.connectWebSocket = () => window.dashboard?.setupWebSocket();