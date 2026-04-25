// Enhanced Dashboard JavaScript with GSAP Animations (Framer Motion equivalent)
class EnhancedDashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 3000;
        this.updateInterval = null;
        this.toast = null;
        this.animations = {};
        
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
        // Register GSAP plugins
        gsap.registerPlugin(ScrollTrigger, TextPlugin);
        
        // Set default easing
        gsap.defaults({ ease: "power2.out" });
        
        // Initialize ScrollTrigger
        ScrollTrigger.config({ ignoreMobileResize: true });
    }
    
    initializeAnimations() {
        // Page load animations
        this.animatePageLoad();
        
        // Setup scroll-triggered animations
        this.setupScrollAnimations();
        
        // Setup hover animations
        this.setupHoverAnimations();
        
        // Setup data update animations
        this.setupDataAnimations();
    }
    
    animatePageLoad() {
        // Stagger animation for metric cards
        gsap.fromTo('.metric-card', 
            { 
                y: 50, 
                opacity: 0,
                scale: 0.9
            },
            { 
                y: 0, 
                opacity: 1,
                scale: 1,
                duration: 0.8,
                stagger: 0.1,
                ease: "back.out(1.7)"
            }
        );
        
        // Animate navbar
        gsap.fromTo('.navbar', 
            { y: -100, opacity: 0 },
            { y: 0, opacity: 1, duration: 0.6, ease: "power2.out" }
        );
        
        // Animate cards with stagger
        gsap.fromTo('.card', 
            { 
                y: 30, 
                opacity: 0,
                rotationX: -15
            },
            { 
                y: 0, 
                opacity: 1,
                rotationX: 0,
                duration: 0.6,
                stagger: 0.1,
                ease: "power2.out"
            }
        );
        
        // Animate status indicator
        gsap.fromTo('.status-indicator', 
            { scale: 0, rotation: 180 },
            { scale: 1, rotation: 0, duration: 0.5, ease: "back.out(1.7)" }
        );
    }
    
    setupScrollAnimations() {
        // Animate cards on scroll
        gsap.utils.toArray('.card').forEach(card => {
            gsap.fromTo(card,
                { 
                    y: 50, 
                    opacity: 0,
                    scale: 0.95
                },
                {
                    y: 0,
                    opacity: 1,
                    scale: 1,
                    duration: 0.6,
                    ease: "power2.out",
                    scrollTrigger: {
                        trigger: card,
                        start: "top 85%",
                        end: "bottom 15%",
                        toggleActions: "play none none reverse"
                    }
                }
            );
        });
        
        // Animate table rows on scroll
        gsap.utils.toArray('.table-modern tbody tr').forEach((row, index) => {
            gsap.fromTo(row,
                { x: -50, opacity: 0 },
                {
                    x: 0,
                    opacity: 1,
                    duration: 0.4,
                    delay: index * 0.05,
                    ease: "power2.out",
                    scrollTrigger: {
                        trigger: row,
                        start: "top 90%",
                        toggleActions: "play none none reverse"
                    }
                }
            );
        });
    }
    
    setupHoverAnimations() {
        // Card hover effects
        gsap.utils.toArray('.card').forEach(card => {
            card.addEventListener('mouseenter', () => {
                gsap.to(card, {
                    y: -8,
                    scale: 1.02,
                    duration: 0.3,
                    ease: "power2.out"
                });
            });
            
            card.addEventListener('mouseleave', () => {
                gsap.to(card, {
                    y: 0,
                    scale: 1,
                    duration: 0.3,
                    ease: "power2.out"
                });
            });
        });
        
        // Button hover effects
        gsap.utils.toArray('.btn-modern').forEach(btn => {
            btn.addEventListener('mouseenter', () => {
                gsap.to(btn, {
                    scale: 1.05,
                    duration: 0.2,
                    ease: "power2.out"
                });
            });
            
            btn.addEventListener('mouseleave', () => {
                gsap.to(btn, {
                    scale: 1,
                    duration: 0.2,
                    ease: "power2.out"
                });
            });
        });
        
        // Metric card hover effects
        gsap.utils.toArray('.metric-card').forEach(card => {
            card.addEventListener('mouseenter', () => {
                gsap.to(card, {
                    scale: 1.03,
                    duration: 0.3,
                    ease: "power2.out"
                });
            });
            
            card.addEventListener('mouseleave', () => {
                gsap.to(card, {
                    scale: 1,
                    duration: 0.3,
                    ease: "power2.out"
                });
            });
        });
    }
    
    setupDataAnimations() {
        // Animate metric value changes
        this.animations.metricUpdate = (element, newValue, oldValue) => {
            const isPositive = newValue > oldValue;
            const color = isPositive ? '#10b981' : '#ef4444';
            
            gsap.to(element, {
                scale: 1.1,
                color: color,
                duration: 0.2,
                ease: "power2.out",
                onComplete: () => {
                    gsap.to(element, {
                        scale: 1,
                        color: 'inherit',
                        duration: 0.2,
                        ease: "power2.out"
                    });
                }
            });
        };
        
        // Animate table row updates
        this.animations.tableRowUpdate = (row) => {
            gsap.fromTo(row,
                { backgroundColor: '#fef3c7' },
                { backgroundColor: 'transparent', duration: 1, ease: "power2.out" }
            );
        };
        
        // Animate status changes
        this.animations.statusChange = (element, newStatus) => {
            gsap.to(element, {
                scale: 1.3,
                duration: 0.2,
                ease: "back.out(1.7)",
                onComplete: () => {
                    gsap.to(element, {
                        scale: 1,
                        duration: 0.2,
                        ease: "power2.out"
                    });
                }
            });
        };
    }
    
    animateMetricUpdate(element, newValue) {
        if (!element) return;
        
        const oldValue = parseFloat(element.textContent.replace(/[^0-9.-]/g, '')) || 0;
        const isPositive = newValue > oldValue;
        
        // Animate the value change
        gsap.to(element, {
            scale: 1.1,
            color: isPositive ? '#10b981' : '#ef4444',
            duration: 0.2,
            ease: "power2.out",
            onComplete: () => {
                // Update the text content
                if (element.id === 'total-portfolio' || element.id === 'daily-pnl') {
                    element.textContent = this.formatCurrency(newValue);
                } else if (element.id === 'risk-exposure') {
                    element.textContent = `${newValue.toFixed(1)}%`;
                } else {
                    element.textContent = newValue.toString();
                }
                
                // Animate back to normal
                gsap.to(element, {
                    scale: 1,
                    color: 'inherit',
                    duration: 0.2,
                    ease: "power2.out"
                });
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
        document.getElementById('emergency-btn')?.addEventListener('click', () => this.emergencyStop());
    }
    
    setupToast() {
        this.toast = new bootstrap.Toast(document.getElementById('notification-toast'));
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        const statusText = document.getElementById('status-text');
        
        if (!indicator || !statusText) return;
        
        // Update classes
        indicator.className = `status-indicator ${status === 'connected' ? 'status-online' : status === 'connecting' ? 'status-warning' : 'status-offline'}`;
        statusText.textContent = status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected';
        
        // Animate status change
        this.animations.statusChange(indicator, status);
        
        // Animate status text
        gsap.fromTo(statusText, 
            { scale: 1.1, color: '#f59e0b' },
            { scale: 1, color: 'white', duration: 0.3, ease: "power2.out" }
        );
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
        }
    }
    
    updatePortfolio(data) {
        // Update portfolio values with animations
        this.animateMetricUpdate(document.getElementById('total-portfolio'), data.total_balance || 0);
        this.animateMetricUpdate(document.getElementById('daily-pnl'), data.daily_pnl || 0);
        this.animateMetricUpdate(document.getElementById('active-trades'), data.active_trades || 0);
        this.animateMetricUpdate(document.getElementById('risk-exposure'), data.risk_exposure || 0);
        
        // Update portfolio change percentage with animation
        const portfolioChange = document.getElementById('portfolio-change');
        if (portfolioChange) {
            const change = data.portfolio_change || 0;
            gsap.to(portfolioChange, {
                opacity: 0,
                scale: 0.8,
                duration: 0.2,
                ease: "power2.out",
                onComplete: () => {
                    portfolioChange.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
                    portfolioChange.className = `text-${change >= 0 ? 'success' : 'danger'} small mt-2`;
                    
                    gsap.to(portfolioChange, {
                        opacity: 1,
                        scale: 1,
                        duration: 0.3,
                        ease: "back.out(1.7)"
                    });
                }
            });
        }
        
        // Update risk progress bar with animation
        const riskProgress = document.getElementById('risk-progress');
        if (riskProgress) {
            const risk = data.risk_exposure || 0;
            gsap.to(riskProgress, {
                width: `${Math.min(risk, 100)}%`,
                duration: 0.8,
                ease: "power2.out"
            });
        }
        
        // Animate metric cards on data update
        gsap.utils.toArray('.metric-card').forEach((card, index) => {
            gsap.to(card, {
                scale: 1.02,
                duration: 0.2,
                delay: index * 0.1,
                ease: "power2.out",
                onComplete: () => {
                    gsap.to(card, {
                        scale: 1,
                        duration: 0.2,
                        ease: "power2.out"
                    });
                }
            });
        });
    }
    
    updateTradingStatus(data) {
        // Animate trading status updates
        const statusElements = document.querySelectorAll('[data-trading-status]');
        statusElements.forEach(element => {
            gsap.to(element, {
                scale: 1.1,
                duration: 0.2,
                ease: "power2.out",
                onComplete: () => {
                    element.textContent = data.status || 'Unknown';
                    gsap.to(element, {
                        scale: 1,
                        duration: 0.2,
                        ease: "power2.out"
                    });
                }
            });
        });
    }
    
    updateRiskExposure(data) {
        // Animate risk exposure updates
        const riskElement = document.getElementById('risk-exposure');
        if (riskElement) {
            this.animateMetricUpdate(riskElement, data.exposure_percentage || 0);
        }
        
        const progressElement = document.getElementById('risk-progress');
        if (progressElement) {
            gsap.to(progressElement, {
                width: `${Math.min(data.exposure_percentage || 0, 100)}%`,
                duration: 0.8,
                ease: "power2.out"
            });
        }
    }
    
    updateRecentTrades() {
        // Animate table updates
        const tableBody = document.getElementById('recent-trades-table');
        if (tableBody) {
            gsap.to(tableBody, {
                opacity: 0.5,
                duration: 0.2,
                ease: "power2.out",
                onComplete: () => {
                    // Refresh table data here
                    gsap.to(tableBody, {
                        opacity: 1,
                        duration: 0.3,
                        ease: "power2.out"
                    });
                }
            });
        }
    }
    
    updateRecentAlerts() {
        // Animate alerts updates
        const alertsContainer = document.getElementById('recent-alerts');
        if (alertsContainer) {
            gsap.to(alertsContainer, {
                scale: 1.05,
                duration: 0.2,
                ease: "power2.out",
                onComplete: () => {
                    gsap.to(alertsContainer, {
                        scale: 1,
                        duration: 0.2,
                        ease: "power2.out"
                    });
                }
            });
        }
    }
    
    startTrading() {
        this.showNotification('Starting trading...', 'info');
        // Add trading start logic here
    }
    
    stopTrading() {
        this.showNotification('Stopping trading...', 'warning');
        // Add trading stop logic here
    }
    
    emergencyStop() {
        this.showNotification('Emergency stop executed!', 'danger');
        // Add emergency stop logic here
    }
    
    showNotification(message, type = 'info') {
        const toast = document.getElementById('notification-toast');
        const toastBody = document.getElementById('toast-message');
        const toastTitle = document.getElementById('toast-title');
        
        if (toast && toastBody && toastTitle) {
            toastTitle.textContent = type.charAt(0).toUpperCase() + type.slice(1);
            toastBody.textContent = message;
            
            // Animate toast appearance
            gsap.fromTo(toast, 
                { scale: 0.8, opacity: 0 },
                { scale: 1, opacity: 1, duration: 0.3, ease: "back.out(1.7)" }
            );
            
            this.toast.show();
        }
    }
    
    formatCurrency(value) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(value);
    }
    
    startPeriodicUpdates() {
        this.updateInterval = setInterval(() => {
            this.updatePortfolio();
            this.updateRecentTrades();
            this.updateRecentAlerts();
        }, 30000); // Update every 30 seconds
    }
    
    async updatePortfolio() {
        try {
            const response = await fetch('/api/portfolio');
            if (response.ok) {
                const data = await response.json();
                this.updatePortfolio(data);
            }
        } catch (error) {
            console.error('Error updating portfolio:', error);
        }
    }
}

// Initialize the enhanced dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new EnhancedDashboard();
});
