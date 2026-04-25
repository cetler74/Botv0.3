/**
 * Performance Analytics Extension for Enhanced Dashboard
 * 
 * Provides comprehensive trailing stop system performance monitoring:
 * - Real-time metrics collection and display
 * - System health scoring and recommendations
 * - Performance trend analysis and visualization
 * - WebSocket latency and API error rate tracking
 * 
 * Author: Claude Code
 * Created: 2025-08-30
 */

// Add performance analytics methods to the EnhancedDashboard class
if (typeof EnhancedDashboard !== 'undefined') {
    
    // Extend the EnhancedDashboard prototype with performance analytics methods
    Object.assign(EnhancedDashboard.prototype, {
        
        setupPerformanceAnalytics() {
            console.log('🔧 Setting up Performance Analytics dashboard integration');
            
            // Initialize performance analytics data structures
            this.performanceData = {
                trailingStops: {
                    activationsToday: 0,
                    successRate: 0,
                    pnlImprovement: 0,
                    trailDistance: '0.25%'
                },
                systemHealth: {
                    overallScore: 0,
                    websocketLatency: 0,
                    apiErrorRate: 0,
                    uptime: 100
                },
                exchanges: [],
                lastUpdate: null
            };
            
            // Create performance analytics section in the dashboard if it doesn't exist
            this.createPerformanceAnalyticsSection();
            
            // Set up performance charts
            this.setupPerformanceCharts();
            
            // Initial load
            this.updatePerformanceAnalytics();
        },
        
        createPerformanceAnalyticsSection() {
            // Check if performance analytics section already exists
            let performanceSection = document.getElementById('performance-analytics-section');
            
            if (!performanceSection) {
                // Find a container to insert the performance section
                const mainContainer = document.querySelector('.bg-gray-50.min-h-screen') || 
                                    document.querySelector('.container-fluid') ||
                                    document.querySelector('main') ||
                                    document.body;
                
                // Find the first card section as a reference point
                const referenceCard = document.querySelector('.bg-white.rounded-lg.shadow-sm');
                
                if (mainContainer) {
                    // Create the performance analytics card
                    const performanceCard = document.createElement('div');
                    performanceCard.className = 'bg-white rounded-lg shadow-sm p-6 mb-6';
                    performanceCard.innerHTML = `
                        <div id="performance-analytics-section">
                            <div class="flex items-center justify-between mb-4">
                                <h2 class="text-xl font-bold text-gray-900 flex items-center">
                                    <i class="fas fa-chart-line text-blue-600 mr-3"></i>
                                    Performance Analytics
                                </h2>
                                <span class="px-3 py-1 rounded-full text-sm font-medium" id="performance-health-badge">Loading...</span>
                            </div>
                            
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <!-- Trailing Stops Metrics -->
                                <div>
                                    <h3 class="text-lg font-semibold text-gray-800 mb-3 flex items-center">
                                        🎯 <span class="ml-2">Trailing Stops</span>
                                    </h3>
                                    <div class="space-y-3">
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">Activations Today:</span>
                                            <strong class="text-gray-900" id="trailing-activations">0</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">Success Rate:</span>
                                            <strong class="text-green-600" id="trailing-success-rate">0%</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">Trail Distance:</span>
                                            <strong class="text-blue-600" id="trailing-distance">0.25%</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2">
                                            <span class="text-gray-600">PnL Improvement:</span>
                                            <strong class="text-green-600" id="pnl-improvement">+0%</strong>
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- System Performance -->
                                <div>
                                    <h3 class="text-lg font-semibold text-gray-800 mb-3 flex items-center">
                                        ⚡ <span class="ml-2">System Performance</span>
                                    </h3>
                                    <div class="space-y-3">
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">WebSocket Latency:</span>
                                            <strong class="text-gray-900" id="websocket-latency">0ms</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">API Error Rate:</span>
                                            <strong class="text-yellow-600" id="api-error-rate">0%</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2 border-b border-gray-100">
                                            <span class="text-gray-600">System Uptime:</span>
                                            <strong class="text-green-600" id="system-uptime">100%</strong>
                                        </div>
                                        <div class="flex justify-between items-center py-2">
                                            <span class="text-gray-600">Health Score:</span>
                                            <strong class="text-gray-900" id="health-score">0</strong>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Performance Recommendation -->
                            <div class="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-lg" id="performance-recommendation">
                                <div class="flex items-center">
                                    <i class="fas fa-info-circle text-blue-600 mr-2"></i>
                                    <span class="text-blue-800 text-sm">Loading performance analysis...</span>
                                </div>
                            </div>
                        </div>
                    `;
                    
                    // Insert the performance card into the main container
                    if (referenceCard) {
                        // Insert after the first card (Bot & System Status)
                        referenceCard.parentElement.insertBefore(performanceCard, referenceCard.nextSibling);
                    } else {
                        // Fallback: append to main container
                        mainContainer.appendChild(performanceCard);
                    }
                    
                    console.log('✅ Performance Analytics section created');
                } else {
                    console.warn('⚠️ Could not find suitable container for performance analytics section');
                }
            }
        },
        
        setupPerformanceCharts() {
            // Placeholder for future chart implementation
            console.log('📊 Performance charts setup (ready for Chart.js integration)');
            // TODO: Implement Chart.js integration for performance trends
        },
        
        /**
         * Map /api/v1/performance/analytics payload (snake_case) into the shape
         * displayPerformanceMetrics() expects (camelCase).
         */
        normalizePerformanceApiPayload(raw) {
            const ts = raw.trailing_stops || {};
            const sh = raw.system_health || {};
            const num = (v, fallback = 0) => {
                const n = Number(v);
                return Number.isFinite(n) ? n : fallback;
            };
            return {
                trailingStops: {
                    activationsToday: num(ts.activations_today ?? ts.activationsToday),
                    successRate: num(ts.success_rate ?? ts.successRate),
                    pnlImprovement: num(ts.pnl_improvement ?? ts.pnlImprovement),
                    trailDistance: ts.trail_distance ?? ts.trailDistance ?? '0.25%',
                },
                systemHealth: {
                    overallScore: num(sh.overall_score ?? sh.overallScore),
                    websocketLatency: num(sh.websocket_latency ?? sh.websocketLatency),
                    apiErrorRate: num(sh.api_error_rate ?? sh.apiErrorRate),
                    uptime: num(sh.uptime ?? sh.system_uptime, 100),
                },
                exchanges: raw.exchanges || [],
                recommendation: raw.recommendation,
                insights: raw.insights || [],
                lastUpdate: new Date(),
            };
        },

        async updatePerformanceAnalytics() {
            try {
                console.log('📊 Updating performance analytics...');
                
                // Fetch performance analytics from the API
                const response = await fetch('/api/v1/performance/analytics');
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const analytics = await response.json();

                // Hard failure: exception path returns only { error } without metrics blocks
                if (analytics.error && !analytics.trailing_stops) {
                    console.warn('⚠️ Performance analytics error:', analytics.error);
                    this.displayBasicPerformanceMetrics();
                    return;
                }

                this.performanceData = this.normalizePerformanceApiPayload(analytics);

                this.displayPerformanceMetrics();

                if (analytics.performance_analytics_enabled === false) {
                    const healthBadge = document.getElementById('performance-health-badge');
                    if (healthBadge) {
                        healthBadge.textContent = 'Offline';
                        healthBadge.className =
                            'px-3 py-1 rounded-full text-sm font-medium bg-gray-100 text-gray-700';
                    }
                    if (analytics.performance_analytics_message) {
                        this.updateRecommendation(analytics.performance_analytics_message);
                    }
                }

                console.log('✅ Performance analytics updated successfully');
                
            } catch (error) {
                console.error('❌ Error updating performance analytics:', error);
                this.displayBasicPerformanceMetrics();
            }
        },
        
        displayPerformanceMetrics() {
            const data = this.performanceData;
            
            // Update trailing stops metrics
            this.updateElementText('trailing-activations', data.trailingStops.activationsToday || 0);
            this.updateElementText('trailing-success-rate', `${(data.trailingStops.successRate || 0).toFixed(1)}%`);
            this.updateElementText('trailing-distance', data.trailingStops.trailDistance || '0.25%');
            
            // Format PnL improvement
            const pnlImprovement = data.trailingStops.pnlImprovement || 0;
            const pnlText = pnlImprovement >= 0 ? `+${pnlImprovement.toFixed(1)}%` : `${pnlImprovement.toFixed(1)}%`;
            const pnlElement = document.getElementById('pnl-improvement');
            if (pnlElement) {
                pnlElement.textContent = pnlText;
                pnlElement.className = pnlImprovement >= 0 ? 'text-success' : 'text-danger';
            }
            
            // Update system performance metrics
            this.updateElementText('websocket-latency', `${(data.systemHealth.websocketLatency || 0).toFixed(0)}ms`);
            this.updateElementText('api-error-rate', `${(data.systemHealth.apiErrorRate || 0).toFixed(1)}%`);
            this.updateElementText('system-uptime', `${(data.systemHealth.uptime || 100).toFixed(1)}%`);
            this.updateElementText('health-score', (data.systemHealth.overallScore || 0).toFixed(0));
            
            // Update health badge
            this.updateHealthBadge(data.systemHealth.overallScore || 0);
            
            // Update recommendation
            this.updateRecommendation(data.recommendation);
            
            // Store latest update time
            this.lastPerformanceUpdate = new Date();
        },
        
        displayBasicPerformanceMetrics() {
            // Display basic/fallback metrics when full analytics aren't available
            console.log('📊 Displaying basic performance metrics');
            
            this.updateElementText('trailing-activations', 'N/A');
            this.updateElementText('trailing-success-rate', 'N/A');
            this.updateElementText('trailing-distance', '0.25%');
            this.updateElementText('pnl-improvement', 'N/A');
            this.updateElementText('websocket-latency', 'N/A');
            this.updateElementText('api-error-rate', 'N/A');
            this.updateElementText('system-uptime', '100%');
            this.updateElementText('health-score', 'N/A');
            
            // Update health badge
            const healthBadge = document.getElementById('performance-health-badge');
            if (healthBadge) {
                healthBadge.textContent = 'Partial Data';
                healthBadge.className = 'badge bg-warning';
            }
            
            // Update recommendation
            this.updateRecommendation('Performance analytics initializing. Full metrics will be available shortly.');
        },
        
        updateHealthBadge(healthScore) {
            const healthBadge = document.getElementById('performance-health-badge');
            if (!healthBadge) return;
            
            let badgeClass, badgeText;
            
            if (healthScore >= 90) {
                badgeClass = 'px-3 py-1 rounded-full text-sm font-medium bg-green-100 text-green-800';
                badgeText = 'Excellent';
            } else if (healthScore >= 75) {
                badgeClass = 'px-3 py-1 rounded-full text-sm font-medium bg-blue-100 text-blue-800';
                badgeText = 'Good';
            } else if (healthScore >= 50) {
                badgeClass = 'px-3 py-1 rounded-full text-sm font-medium bg-yellow-100 text-yellow-800';
                badgeText = 'Fair';
            } else {
                badgeClass = 'px-3 py-1 rounded-full text-sm font-medium bg-red-100 text-red-800';
                badgeText = 'Needs Attention';
            }
            
            healthBadge.className = badgeClass;
            healthBadge.textContent = `${badgeText} (${healthScore})`;
        },
        
        updateRecommendation(recommendation) {
            const recommendationElement = document.getElementById('performance-recommendation');
            if (!recommendationElement || !recommendation) return;
            
            // Parse recommendation priority
            let bgClass = 'bg-blue-50 border-blue-200';
            let textClass = 'text-blue-800';
            let iconClass = 'fas fa-info-circle text-blue-600';
            
            if (recommendation.includes('HIGH_PRIORITY')) {
                bgClass = 'bg-red-50 border-red-200';
                textClass = 'text-red-800';
                iconClass = 'fas fa-exclamation-triangle text-red-600';
            } else if (recommendation.includes('MEDIUM_PRIORITY')) {
                bgClass = 'bg-yellow-50 border-yellow-200';
                textClass = 'text-yellow-800';
                iconClass = 'fas fa-exclamation-circle text-yellow-600';
            } else if (recommendation.includes('SYSTEM_HEALTHY')) {
                bgClass = 'bg-green-50 border-green-200';
                textClass = 'text-green-800';
                iconClass = 'fas fa-check-circle text-green-600';
            }
            
            // Clean up the recommendation text
            const cleanRecommendation = recommendation
                .replace(/^(HIGH_PRIORITY|MEDIUM_PRIORITY|SYSTEM_HEALTHY):\s*/, '')
                .replace(/^(ANALYSIS_ERROR):\s*/, '');
            
            recommendationElement.className = `mt-6 p-4 ${bgClass} border rounded-lg`;
            recommendationElement.innerHTML = `
                <div class="flex items-center">
                    <i class="${iconClass} mr-2"></i>
                    <span class="${textClass} text-sm">${cleanRecommendation}</span>
                </div>
            `;
        },
        
        updateElementText(elementId, text) {
            const element = document.getElementById(elementId);
            if (element) {
                element.textContent = text;
            }
        },
        
        async getDetailedPerformanceReport() {
            try {
                const response = await fetch('/api/v1/performance/report');
                if (response.ok) {
                    return await response.json();
                }
                throw new Error('Failed to fetch detailed report');
            } catch (error) {
                console.error('Error fetching detailed performance report:', error);
                return null;
            }
        },
        
        // Utility method to format performance data for export/logging
        exportPerformanceData() {
            return {
                timestamp: new Date().toISOString(),
                metrics: this.performanceData,
                lastUpdate: this.lastPerformanceUpdate,
                dashboardVersion: '1.0.0-performance-analytics'
            };
        }
    });
    
    // Add CSS styles for performance analytics
    const performanceStyles = `
        <style>
        .performance-analytics-card .card-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .performance-metric {
            padding: 0.5rem 0;
            border-bottom: 1px solid #f8f9fa;
        }
        
        .performance-metric:last-child {
            border-bottom: none;
        }
        
        .performance-recommendation {
            font-size: 0.875rem;
        }
        
        .performance-analytics-card .card-body {
            background: linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%);
        }
        
        #health-score {
            font-weight: bold;
            font-size: 1.1rem;
        }
        
        .performance-metric strong {
            font-weight: 600;
        }
        
        @keyframes performance-update {
            0% { background-color: #e3f2fd; }
            100% { background-color: transparent; }
        }
        
        .performance-metric.updated {
            animation: performance-update 2s ease-out;
        }
        </style>
    `;
    
    // Inject styles into the page
    if (!document.getElementById('performance-analytics-styles')) {
        const styleElement = document.createElement('div');
        styleElement.id = 'performance-analytics-styles';
        styleElement.innerHTML = performanceStyles;
        document.head.appendChild(styleElement);
    }
    
    console.log('✅ Performance Analytics JavaScript extension loaded');
} else {
    console.warn('⚠️ EnhancedDashboard class not found - Performance Analytics extension cannot be loaded');
}