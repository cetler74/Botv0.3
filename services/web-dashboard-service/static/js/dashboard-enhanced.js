// Enhanced Dashboard JavaScript with GSAP Animations (Framer Motion equivalent)
class ModernDashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 3000;
        this.updateInterval = null;
        this.toast = null;
        
        // State management
        this.state = {
            portfolio: {},
            trades: [],
            tradeHistory: [],
            exchanges: {},
            strategies: {},
            currentPage: 1,
            totalPages: 1,
            currentFilter: 'all',
            currentTradesFilter: 'all',
            currentTradesSearch: '',
            itemsPerPage: 20,
            sortConfig: {
                field: null,
                direction: 'asc'
            }
        };
        
        this.initialize();
    }
    
    initialize() {
        this.setupGSAP();
        this.setupWebSocket();
        this.setupEventListeners();
        this.setupToast();
        this.startPeriodicUpdates();
        this.initializeAnimations();
    }
    
    setupGSAP() {
        // Register ScrollTrigger plugin
        gsap.registerPlugin(ScrollTrigger);
        
        // Initialize GSAP animations
        this.initializeGSAPAnimations();
    }
    
    initializeGSAPAnimations() {
        // Stagger animation for metric cards
        gsap.from('.stagger-card', {
            duration: 0.8,
            y: 50,
            opacity: 0,
            stagger: 0.1,
            ease: "power2.out",
            scrollTrigger: {
                trigger: '.stagger-card',
                start: "top 80%",
                end: "bottom 20%",
                toggleActions: "play none none reverse"
            }
        });
        
        // Fade in animation for cards
        gsap.from('.modern-card', {
            duration: 0.6,
            y: 30,
            opacity: 0,
            stagger: 0.2,
            ease: "power2.out",
            scrollTrigger: {
                trigger: '.modern-card',
                start: "top 85%",
                end: "bottom 15%",
                toggleActions: "play none none reverse"
            }
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
            this.showNotification('Connected to trading bot', 'success');
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
        document.getElementById('start-btn')?.addEventListener('click', () => this.startTrading());
        document.getElementById('stop-btn')?.addEventListener('click', () => this.stopTrading());
        document.getElementById('emergency-stop')?.addEventListener('click', () => this.showEmergencyModal());
        
        // Emergency modal
        document.getElementById('confirm-emergency-stop')?.addEventListener('click', () => this.executeEmergencyStop());
        document.getElementById('cancel-emergency-stop')?.addEventListener('click', () => this.hideEmergencyModal());
        
        // Refresh buttons
        document.getElementById('refresh-trades')?.addEventListener('click', () => this.fetchTrades());
        document.getElementById('refresh-history')?.addEventListener('click', () => this.fetchTradeHistory());
        
        // Filters and search
        document.getElementById('trades-filter')?.addEventListener('change', (e) => {
            this.state.currentTradesFilter = e.target.value;
            this.applyTradesFilter();
        });
        
        document.getElementById('trade-search')?.addEventListener('input', (e) => {
            this.state.currentTradesSearch = e.target.value;
            this.applyTradesFilter();
        });
        
        document.getElementById('history-filter')?.addEventListener('change', (e) => {
            this.state.currentFilter = e.target.value;
            this.state.currentPage = 1;
            this.fetchTradeHistory();
        });
        
        // Pagination
        document.getElementById('prev-page')?.addEventListener('click', () => this.previousPage());
        document.getElementById('next-page')?.addEventListener('click', () => this.nextPage());
        
        // Exchange breakdown toggle
        document.getElementById('toggle-exchange-breakdown')?.addEventListener('click', () => this.toggleExchangeBreakdown());
        
        // Column toggles
        document.querySelectorAll('.toggle-col').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => this.toggleColumn(e.target.dataset.col, e.target.checked));
        });
        
        // Table sorting - use event delegation for dynamic content
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('sortable')) {
                const field = e.target.dataset.sort;
                if (field) {
                    this.handleSort(field);
                }
            }
        });
        
        // Modal backdrop click
        document.getElementById('emergency-modal')?.addEventListener('click', (e) => {
            if (e.target.id === 'emergency-modal') {
                this.hideEmergencyModal();
            }
        });
    }
    
    setupToast() {
        this.toast = document.getElementById('notification-toast');
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        const text = document.getElementById('status-text');
        
        if (indicator && text) {
            indicator.className = `status-indicator status-${status}`;
            text.textContent = status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected';
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
                this.updatePortfolio(data.data);
                break;
            case 'trading_status_update':
                this.updateTradingStatus(data.data);
                break;
            case 'risk_exposure_update':
                this.updateRiskExposure(data.data);
                break;
            case 'trade_update':
                this.fetchTrades();
                break;
            case 'alert_update':
                this.showNotification('New alert received', 'info');
                break;
            case 'emergency_stop':
                this.showNotification('Emergency stop executed!', 'warning');
                this.updateTradingStatus(data.data);
                break;
        }
    }
    
    // Portfolio Management
    async fetchPortfolio() {
        try {
            const response = await fetch('/api/portfolio');
            const data = await response.json();
            this.state.portfolio = data;
            this.updatePortfolioUI(data);
        } catch (error) {
            console.error('Error fetching portfolio:', error);
            this.showNotification('Failed to fetch portfolio data', 'error');
        }
    }
    
    updatePortfolioUI(data) {
        // Update metric cards with animations
        const updates = [
            { id: 'total-balance', value: `$${data.total_balance?.toFixed(2) || '0.00'}` },
            { id: 'available-balance', value: `$${data.available_balance?.toFixed(2) || '0.00'}` },
            { id: 'total-pnl', value: `$${data.total_pnl?.toFixed(2) || '0.00'}` },
            { id: 'daily-pnl', value: `$${data.daily_pnl?.toFixed(2) || '0.00'}` },
            { id: 'open-trades', value: data.active_trades || 0 },
            { id: 'win-rate', value: `${data.win_rate?.toFixed(1) || '0.0'}%` }
        ];
        
        updates.forEach(update => {
            const element = document.getElementById(update.id);
            if (element) {
                // Animate value changes
                gsap.to(element, {
                    duration: 0.3,
                    scale: 1.1,
                    ease: "power2.out",
                    onComplete: () => {
                        element.textContent = update.value;
                        gsap.to(element, {
                            duration: 0.3,
                            scale: 1,
                            ease: "power2.out"
                        });
                    }
                });
            }
        });
        
        // Update exchange breakdown if available
        if (data.exchanges) {
            this.updateExchangeBreakdownTable(Object.entries(data.exchanges).map(([exchange, ex]) => ({
                exchange,
                total_balance: ex.balance,
                available_balance: ex.available,
                total_pnl: ex.total_pnl,
                timestamp: ex.timestamp
            })));
        }
    }
    
    // Trades Management
    async fetchTrades() {
        try {
            const response = await fetch('/api/trades');
            const data = await response.json();
            this.state.trades = data.trades || [];
            this.applyTradesFilter();
        } catch (error) {
            console.error('Error fetching trades:', error);
            this.showNotification('Failed to fetch trades data', 'error');
        }
    }
    
    applyTradesFilter() {
        let filtered = this.state.trades;
        
        // Apply status filter
        if (this.state.currentTradesFilter !== 'all') {
            filtered = filtered.filter(trade => trade.status === this.state.currentTradesFilter);
        }
        
        // Apply search filter
        if (this.state.currentTradesSearch) {
            filtered = filtered.filter(trade => 
                trade.trade_id && trade.trade_id.toLowerCase().includes(this.state.currentTradesSearch.toLowerCase())
            );
        }
        
        // Apply sorting
        if (this.state.sortConfig.field) {
            filtered = this.sortData(filtered, this.state.sortConfig.field, this.state.sortConfig.direction);
        }
        
        this.updateTradesTable(filtered);
    }
    
    updateTradesTable(trades) {
        const tbody = document.getElementById('trades-table-body');
        if (!tbody) return;
        
        tbody.innerHTML = '';
        
        if (trades.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td colspan="14" class="px-4 py-8 text-center text-gray-500">
                    No trades found
                </td>
            `;
            tbody.appendChild(row);
            return;
        }
        
        trades.forEach((trade, index) => {
            const row = document.createElement('tr');
            const pnl = trade.unrealized_pnl || 0;
            const pnlClass = pnl >= 0 ? 'text-green-600' : 'text-red-600';
            const entryTime = trade.entry_time ? new Date(trade.entry_time).toLocaleString() : 'N/A';
            const entryReason = trade.entry_reason || 'N/A';
            const truncatedReason = entryReason.length > 30 ? entryReason.substring(0, 30) + '...' : entryReason;
            const shortTradeId = trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A';
            
            // Apply row styling based on status
            let rowClass = '';
            if (trade.status === 'OPEN') {
                rowClass = 'trade-open';
            } else if (trade.status === 'CLOSED') {
                rowClass = 'trade-closed';
            } else if (trade.status === 'PENDING_EXIT') {
                rowClass = 'trade-pending';
            }
            
            // Determine status badge
            let statusBadge = 'badge-secondary';
            if (trade.status === 'OPEN') {
                statusBadge = 'badge-success';
            } else if (trade.status === 'CLOSED') {
                statusBadge = 'badge-danger';
            } else if (trade.status === 'PENDING_EXIT') {
                statusBadge = 'badge-warning';
            }
            
            row.className = rowClass;
            row.innerHTML = `
                <td class="font-mono text-sm text-gray-900" title="${trade.trade_id || 'N/A'}">${shortTradeId}</td>
                <td class="font-medium text-gray-900">${trade.pair || 'N/A'}</td>
                <td class="text-gray-500 mobile-hidden">${trade.exchange || 'N/A'}</td>
                <td class="text-gray-500 mobile-hidden">${entryTime}</td>
                <td class="text-gray-500">$${trade.entry_price?.toFixed(4) || '0.0000'}</td>
                <td class="text-gray-500">$${trade.current_price?.toFixed(4) || '0.0000'}</td>
                <td class="text-gray-500">${trade.position_size?.toFixed(6) || '0.000000'}</td>
                <td class="text-gray-500">$${((trade.entry_price || 0) * (trade.position_size || 0)).toFixed(2)}</td>
                <td class="font-medium ${pnlClass}">$${pnl.toFixed(2)}</td>
                <td>
                    <span class="badge-modern ${statusBadge}">${trade.status || 'N/A'}</span>
                </td>
                <td class="text-gray-500 mobile-hidden" title="${entryReason}">${truncatedReason}</td>
                <td class="profit-trigger-col">
                    ${trade.profit_protection || 'inactive'}
                    <div class="text-xs text-gray-500">${trade.profit_protection_trigger ? `Trigger: ${trade.profit_protection_trigger}` : 'None'}</div>
                </td>
                <td class="trailing-stop-col">
                    ${trade.trail_stop || 'inactive'}
                    <div class="text-xs text-gray-500">${trade.trail_stop_trigger ? `Trigger: ${trade.trail_stop_trigger}` : 'None'}</div>
                </td>
                <td class="highest-price-col">
                    ${trade.highest_price !== undefined && trade.highest_price !== null ? trade.highest_price.toFixed(6) : 'None'}
                </td>
            `;
            
            tbody.appendChild(row);
            
            // Animate row entry
            gsap.from(row, {
                duration: 0.3,
                opacity: 0,
                y: 20,
                delay: index * 0.05,
                ease: "power2.out"
            });
        });
    }
    
    // Trade History Management
    async fetchTradeHistory() {
        try {
            const response = await fetch(`/api/trade-history?page=${this.state.currentPage}&filter=${this.state.currentFilter}&limit=${this.state.itemsPerPage}`);
            const data = await response.json();
            this.state.tradeHistory = data.trades || [];
            this.state.totalPages = data.total_pages || 1;
            
            // Apply sorting if configured
            let sortedTrades = this.state.tradeHistory;
            if (this.state.sortConfig.field) {
                sortedTrades = this.sortData([...this.state.tradeHistory], this.state.sortConfig.field, this.state.sortConfig.direction);
            }
            
            this.updateTradeHistoryTable(sortedTrades);
            this.updatePagination(data.total, data.page, data.total_pages);
            this.updateHistorySummary(data.total, data.trades.length);
        } catch (error) {
            console.error('Error fetching trade history:', error);
            this.showNotification('Failed to fetch trade history', 'error');
        }
    }
    
    updateTradeHistoryTable(trades) {
        const tbody = document.getElementById('trade-history-table-body');
        if (!tbody) return;
        
        tbody.innerHTML = '';
        
        if (!trades || trades.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td colspan="15" class="px-4 py-8 text-center text-gray-500">
                    No closed trades found
                </td>
            `;
            tbody.appendChild(row);
            return;
        }
        
        trades.forEach((trade, index) => {
            const row = document.createElement('tr');
            const realizedPnl = trade.realized_pnl || 0;
            const realizedPnlPct = trade.realized_pnl_pct || 0;
            const pnlClass = realizedPnl >= 0 ? 'text-green-600' : 'text-red-600';
            const pnlPctClass = realizedPnlPct >= 0 ? 'text-green-600' : 'text-red-600';
            
            const entryTime = trade.entry_time ? new Date(trade.entry_time).toLocaleString() : 'N/A';
            const exitTime = trade.exit_time ? new Date(trade.exit_time).toLocaleString() : 'N/A';
            const entryReason = trade.entry_reason || 'N/A';
            const exitReason = trade.exit_reason || 'N/A';
            
            const truncatedEntryReason = entryReason.length > 30 ? entryReason.substring(0, 30) + '...' : entryReason;
            const truncatedExitReason = exitReason.length > 30 ? exitReason.substring(0, 30) + '...' : exitReason;
            const shortTradeId = trade.trade_id ? trade.trade_id.substring(0, 8) + '...' : 'N/A';
            
            row.innerHTML = `
                <td class="font-mono text-sm text-gray-900" title="${trade.trade_id || 'N/A'}">${shortTradeId}</td>
                <td class="font-medium text-gray-900">${trade.pair || 'N/A'}</td>
                <td class="text-gray-500">${trade.exchange || 'N/A'}</td>
                <td class="text-gray-500">${entryTime}</td>
                <td class="text-gray-500">$${trade.entry_price?.toFixed(4) || '0.0000'}</td>
                <td class="text-gray-500">$${trade.position_size?.toFixed(2) || '0.00'}</td>
                <td class="text-gray-500">${exitTime}</td>
                <td class="text-gray-500">$${trade.exit_price?.toFixed(4) || '0.0000'}</td>
                <td class="text-gray-500" title="${entryReason}">${truncatedEntryReason}</td>
                <td class="text-gray-500" title="${exitReason}">${truncatedExitReason}</td>
                <td class="font-medium ${pnlClass}">$${realizedPnl.toFixed(2)}</td>
                <td class="font-medium ${pnlPctClass}">${realizedPnlPct.toFixed(2)}%</td>
                <td class="profit-trigger-col">${trade.profit_protection || 'N/A'}</td>
                <td class="trailing-stop-col">${trade.trail_stop || 'N/A'}</td>
                <td class="highest-price-col">${trade.highest_price !== undefined && trade.highest_price !== null ? trade.highest_price.toFixed(6) : 'N/A'}</td>
            `;
            
            tbody.appendChild(row);
            
            // Animate row entry
            gsap.from(row, {
                duration: 0.3,
                opacity: 0,
                y: 20,
                delay: index * 0.05,
                ease: "power2.out"
            });
        });
    }
    
    // Exchange Management
    async fetchExchanges() {
        try {
            const response = await fetch('/api/exchanges');
            const data = await response.json();
            this.state.exchanges = data;
            this.updateExchangeStatus(data);
        } catch (error) {
            console.error('Error fetching exchanges:', error);
            this.showNotification('Failed to fetch exchange data', 'error');
        }
    }
    
    updateExchangeStatus(exchanges) {
        const exchangeStatus = document.getElementById('exchange-status');
        if (!exchangeStatus) return;
        
        exchangeStatus.innerHTML = '';
        
        Object.entries(exchanges).forEach(([name, info], index) => {
            const exchangeDiv = document.createElement('div');
            const statusClass = info.healthy ? 'status-online' : 'status-offline';
            const statusText = info.healthy ? 'Online' : 'Offline';
            
            exchangeDiv.className = 'modern-card p-4';
            exchangeDiv.innerHTML = `
                <div class="flex items-center">
                    <span class="status-indicator ${statusClass}"></span>
                    <div class="ml-3">
                        <h4 class="font-medium text-gray-900">${name.toUpperCase()}</h4>
                        <p class="text-sm text-gray-500">${statusText}</p>
                    </div>
                </div>
            `;
            
            exchangeStatus.appendChild(exchangeDiv);
            
            // Animate entry
            gsap.from(exchangeDiv, {
                duration: 0.4,
                opacity: 0,
                x: -30,
                delay: index * 0.1,
                ease: "power2.out"
            });
        });
    }
    
    updateExchangeBreakdownTable(exchangeData) {
        const tbody = document.getElementById('exchange-breakdown-table-body');
        if (!tbody) return;
        
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
        
        exchangeData.forEach((exchange, index) => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td class="font-medium text-gray-900">${exchange.exchange || 'N/A'}</td>
                <td class="text-gray-900">$${exchange.total_balance?.toFixed(2) || '0.00'}</td>
                <td class="text-gray-900">$${exchange.available_balance?.toFixed(2) || '0.00'}</td>
                <td class="text-gray-900">$${exchange.total_pnl?.toFixed(2) || '0.00'}</td>
                <td class="text-gray-500">${exchange.timestamp ? new Date(exchange.timestamp).toLocaleString() : 'N/A'}</td>
            `;
            
            tbody.appendChild(row);
            
            // Animate row entry
            gsap.from(row, {
                duration: 0.3,
                opacity: 0,
                y: 20,
                delay: index * 0.05,
                ease: "power2.out"
            });
        });
    }
    
    // Strategy Management
    async fetchStrategies() {
        try {
            const response = await fetch('/api/strategies');
            const data = await response.json();
            this.state.strategies = data;
            this.updateStrategyPerformance(data);
        } catch (error) {
            console.error('Error fetching strategies:', error);
            this.showNotification('Failed to fetch strategy data', 'error');
        }
    }
    
    updateStrategyPerformance(strategies) {
        const strategyPerformance = document.getElementById('strategy-performance');
        if (!strategyPerformance) return;
        
        strategyPerformance.innerHTML = '';
        
        if (strategies && Array.isArray(strategies.enabled_strategies)) {
            strategies.enabled_strategies.forEach((strategy, index) => {
                const strategyDiv = document.createElement('div');
                strategyDiv.className = 'modern-card p-4';
                strategyDiv.innerHTML = `
                    <h4 class="font-medium text-gray-900">${strategy}</h4>
                    <p class="text-sm text-gray-500">Enabled</p>
                `;
                
                strategyPerformance.appendChild(strategyDiv);
                
                // Animate entry
                gsap.from(strategyDiv, {
                    duration: 0.4,
                    opacity: 0,
                    y: 30,
                    delay: index * 0.1,
                    ease: "power2.out"
                });
            });
        } else {
            strategyPerformance.innerHTML = '<span class="text-red-600">No enabled strategies found</span>';
        }
    }
    
    // Pagination
    updatePagination(total, currentPage, totalPages) {
        const prevButton = document.getElementById('prev-page');
        const nextButton = document.getElementById('next-page');
        const pageInfo = document.getElementById('page-info');
        
        if (prevButton) prevButton.disabled = currentPage <= 1;
        if (nextButton) nextButton.disabled = currentPage >= totalPages;
        if (pageInfo) pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    }
    
    updateHistorySummary(total, currentCount) {
        const summary = document.getElementById('history-summary');
        if (summary) summary.textContent = `Showing ${currentCount} of ${total} trades`;
    }
    
    previousPage() {
        if (this.state.currentPage > 1) {
            this.state.currentPage--;
            this.fetchTradeHistory();
        }
    }
    
    nextPage() {
        if (this.state.currentPage < this.state.totalPages) {
            this.state.currentPage++;
            this.fetchTradeHistory();
        }
    }
    
    // Sorting
    handleSort(field) {
        const direction = this.state.sortConfig.field === field && this.state.sortConfig.direction === 'asc' ? 'desc' : 'asc';
        this.state.sortConfig = { field, direction };
        
        // Update sort indicators
        document.querySelectorAll('.sortable').forEach(th => {
            th.classList.remove('sorted-asc', 'sorted-desc');
        });
        
        const currentTh = document.querySelector(`[data-sort="${field}"]`);
        if (currentTh) {
            currentTh.classList.add(`sorted-${direction}`);
        }
        
        this.applyTradesFilter();
    }
    
    sortData(data, field, direction) {
        return data.sort((a, b) => {
            let aVal = a[field];
            let bVal = b[field];
            
            // Handle null/undefined values
            if (aVal === null || aVal === undefined) aVal = '';
            if (bVal === null || bVal === undefined) bVal = '';
            
            // Handle numeric values (including strings that can be converted to numbers)
            const aNum = parseFloat(aVal);
            const bNum = parseFloat(bVal);
            
            if (!isNaN(aNum) && !isNaN(bNum)) {
                return direction === 'asc' ? aNum - bNum : bNum - aNum;
            }
            
            // Handle date values
            const aDate = new Date(aVal);
            const bDate = new Date(bVal);
            
            if (!isNaN(aDate.getTime()) && !isNaN(bDate.getTime())) {
                return direction === 'asc' ? aDate - bDate : bDate - aDate;
            }
            
            // Handle string values
            aVal = String(aVal).toLowerCase();
            bVal = String(bVal).toLowerCase();
            
            if (direction === 'asc') {
                return aVal.localeCompare(bVal);
            } else {
                return bVal.localeCompare(aVal);
            }
        });
    }
    
    // Column toggles
    toggleColumn(colName, visible) {
        const columns = document.querySelectorAll(`.${colName}-col`);
        columns.forEach(col => {
            col.style.display = visible ? 'table-cell' : 'none';
        });
        
        // Also update the header columns
        const headerColumns = document.querySelectorAll(`th.${colName}-col`);
        headerColumns.forEach(col => {
            col.style.display = visible ? 'table-cell' : 'none';
        });
        
        console.log(`Toggled ${colName} columns: ${visible ? 'visible' : 'hidden'}`);
    }
    
    // Exchange breakdown toggle
    toggleExchangeBreakdown() {
        const breakdownSection = document.getElementById('exchange-breakdown-section');
        const button = document.getElementById('toggle-exchange-breakdown');
        
        if (breakdownSection && button) {
            const isVisible = breakdownSection.style.display !== 'none';
            
            if (isVisible) {
                gsap.to(breakdownSection, {
                    duration: 0.3,
                    opacity: 0,
                    y: -20,
                    ease: "power2.in",
                    onComplete: () => {
                        breakdownSection.style.display = 'none';
                        button.innerHTML = '<i class="fas fa-chart-pie mr-2"></i>Show per-exchange breakdown';
                    }
                });
            } else {
                breakdownSection.style.display = 'block';
                breakdownSection.style.opacity = '0';
                breakdownSection.style.transform = 'translateY(-20px)';
                
                gsap.to(breakdownSection, {
                    duration: 0.3,
                    opacity: 1,
                    y: 0,
                    ease: "power2.out"
                });
                
                button.innerHTML = '<i class="fas fa-chart-pie mr-2"></i>Hide per-exchange breakdown';
            }
        }
    }
    
    // Emergency stop
    showEmergencyModal() {
        const modal = document.getElementById('emergency-modal');
        const modalContent = document.getElementById('modal-content');
        
        if (modal && modalContent) {
            modal.classList.remove('hidden');
            
            gsap.to(modalContent, {
                duration: 0.3,
                scale: 1,
                opacity: 1,
                ease: "power2.out"
            });
        }
    }
    
    hideEmergencyModal() {
        const modal = document.getElementById('emergency-modal');
        const modalContent = document.getElementById('modal-content');
        
        if (modal && modalContent) {
            gsap.to(modalContent, {
                duration: 0.2,
                scale: 0.95,
                opacity: 0,
                ease: "power2.in",
                onComplete: () => {
                    modal.classList.add('hidden');
                }
            });
        }
    }
    
    async executeEmergencyStop() {
        try {
            const response = await fetch('/api/control/emergency-stop', { method: 'POST' });
            const data = await response.json();
            this.showNotification(data.message, 'warning');
            this.hideEmergencyModal();
            this.fetchTrades();
        } catch (error) {
            console.error('Error executing emergency stop:', error);
            this.showNotification('Error executing emergency stop', 'error');
        }
    }
    
    // Trading controls
    async startTrading() {
        try {
            const response = await fetch('/api/control/start', { method: 'POST' });
            const data = await response.json();
            this.showNotification(data.message, 'success');
        } catch (error) {
            console.error('Error starting trading:', error);
            this.showNotification('Error starting trading', 'error');
        }
    }
    
    async stopTrading() {
        try {
            const response = await fetch('/api/control/stop', { method: 'POST' });
            const data = await response.json();
            this.showNotification(data.message, 'warning');
        } catch (error) {
            console.error('Error stopping trading:', error);
            this.showNotification('Error stopping trading', 'error');
        }
    }
    
    // Notifications
    showNotification(message, type = 'info') {
        if (!this.toast) return;
        
        const title = document.getElementById('toast-title');
        const messageEl = document.getElementById('toast-message');
        
        if (title && messageEl) {
            title.textContent = type.charAt(0).toUpperCase() + type.slice(1);
            messageEl.textContent = message;
            
            // Update icon based on type
            const icon = this.toast.querySelector('i');
            if (icon) {
                icon.className = `fas fa-${type === 'success' ? 'check-circle text-green-500' : type === 'error' ? 'exclamation-circle text-red-500' : type === 'warning' ? 'exclamation-triangle text-yellow-500' : 'info-circle text-blue-500'} mr-3`;
            }
        }
        
        // Show toast with animation
        gsap.to(this.toast, {
            duration: 0.3,
            x: 0,
            opacity: 1,
            ease: "power2.out"
        });
        
        // Auto hide after 5 seconds
        setTimeout(() => {
            gsap.to(this.toast, {
                duration: 0.3,
                x: '100%',
                opacity: 0,
                ease: "power2.in"
            });
        }, 5000);
    }
    
    // Periodic updates
    startPeriodicUpdates() {
        this.updateInterval = setInterval(() => {
            this.fetchPortfolio();
            this.fetchTrades();
        }, 30000);
    }
    
    // Initialize animations
    initializeAnimations() {
        // Animate metric cards on load
        gsap.from('.stagger-card', {
            duration: 0.8,
            y: 50,
            opacity: 0,
            stagger: 0.1,
            ease: "power2.out"
        });
        
        // Animate cards on scroll
        gsap.utils.toArray('.modern-card').forEach(card => {
            gsap.from(card, {
                duration: 0.6,
                y: 30,
                opacity: 0,
                ease: "power2.out",
                scrollTrigger: {
                    trigger: card,
                    start: "top 85%",
                    end: "bottom 15%",
                    toggleActions: "play none none reverse"
                }
            });
        });
    }
    
    // Initialize dashboard
    async initialize() {
        // Initial data fetch
        await Promise.all([
            this.fetchPortfolio(),
            this.fetchTrades(),
            this.fetchTradeHistory(),
            this.fetchExchanges(),
            this.fetchStrategies()
        ]);
        
        // Update current time
        this.updateCurrentTime();
        setInterval(() => this.updateCurrentTime(), 1000);
    }
    
    updateCurrentTime() {
        const timeElement = document.getElementById('current-time');
        if (timeElement) {
            timeElement.textContent = new Date().toLocaleTimeString();
        }
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new ModernDashboard();
});
