# DOCKER SERVICES AUDIT - 17 Services Running

## CURRENT SERVICES STATUS

| Service | Status | Health | Purpose | Necessary? |
|---------|--------|--------|---------|------------|
| trading-bot-postgres | ✅ Healthy | ✅ | Database | **ESSENTIAL** |
| trading-bot-redis | ✅ Healthy | ✅ | Cache/Queue | **ESSENTIAL** |
| trading-bot-config | ✅ Healthy | ✅ | Configuration | **ESSENTIAL** |
| trading-bot-database | ✅ Healthy | ✅ | Database Service | **ESSENTIAL** |
| trading-bot-exchange | ✅ Healthy | ✅ | Exchange APIs | **ESSENTIAL** |
| trading-bot-strategy | ✅ Healthy | ✅ | Trading Strategy | **ESSENTIAL** |
| trading-bot-orchestrator | ✅ Healthy | ✅ | Trade Management | **ESSENTIAL** |
| trading-bot-web | ✅ Healthy | ✅ | Dashboard | **USEFUL** |
| trading-bot-fill-detection | ✅ Healthy | ✅ | Fill Detection | **REDUNDANT** ❌ |
| trading-bot-order-sync | ❌ Unhealthy | ❌ | Order Sync | **REDUNDANT** ❌ |
| trading-bot-order-queue | ✅ Healthy | ✅ | Order Queue | **UNCLEAR** ❓ |
| trading-bot-position-sync | ✅ Healthy | ✅ | Position Sync | **UNCLEAR** ❓ |
| trading-bot-price-feed | ✅ Healthy | ✅ | Price Data | **UNCLEAR** ❓ |
| trading-bot-mcp | ❌ Unhealthy | ❌ | MCP Service | **UNCLEAR** ❓ |
| trading-bot-prometheus | ✅ Running | - | Monitoring | **OPTIONAL** |
| trading-bot-grafana | ✅ Running | - | Visualization | **OPTIONAL** |
| trading-bot-redis-exporter | ❌ Unhealthy | ❌ | Metrics | **OPTIONAL** |

## ANALYSIS BY CATEGORY

### **CORE TRADING SYSTEM (Essential - 7 services)**
1. **postgres** - Database storage
2. **redis** - Session storage, queuing
3. **config-service** - Configuration management
4. **database-service** - Database API + auto-trade closure
5. **exchange-service** - Exchange APIs + fill detection
6. **strategy-service** - Trading strategies (Heikin Ashi, etc.)
7. **orchestrator-service** - Trading logic + trailing stops

### **SPECIALIZED SERVICES (Active dependencies found - 4 services)**
8. **fill-detection-service** - ✅ **ACTIVE** (Used by Crypto.com event processing + health monitoring)
9. **order-sync-service** - ✅ **ACTIVE** (Used by web dashboard + configured sync interval)
10. **mcp-service** - ✅ **ACTIVE** (Perplexity AI integration + news analysis features)
11. **redis-exporter** - ✅ **ACTIVE** (Prometheus metrics scraping target)

### **UNCLEAR/QUESTIONABLE SERVICES (Need investigation - 3 services)**
12. **order-queue-service** - ❓ Redis-based queuing (might be used by orchestrator)
13. **position-sync-service** - ❓ Position synchronization (might be redundant)
14. **price-feed-service** - ❓ Price data service (exchange service provides this)

### **USER INTERFACE (Useful - 1 service)**
15. **web-dashboard-service** - Dashboard for monitoring trades

### **MONITORING/OPTIONAL (Can be optimized - 2 services)**
16. **prometheus** - Metrics collection
17. **grafana** - Metrics visualization

## CORRECTED ANALYSIS - NO SERVICES CAN BE SAFELY REMOVED

### **CRITICAL FINDING**
Code validation revealed that ALL previously identified "redundant" services have active dependencies:

- **fill-detection-service**: Used by crypto.com event processors (`cryptocom_event_processors.py:118,229`) and health monitoring
- **order-sync-service**: Used by web dashboard service (`main.py:65`) with environment variable configuration
- **mcp-service**: Extensive configuration in `config.yaml` (lines 69-95) with Perplexity API integration
- **redis-exporter**: Active Prometheus scraping target in `monitoring/prometheus.yml`

### **INVESTIGATE USAGE (Still unclear)**
These services need deeper investigation to determine actual usage:
- **order-queue-service** - ❓ Redis-based queuing
- **position-sync-service** - ❓ Position synchronization  
- **price-feed-service** - ❓ Price data service

### **ARCHITECTURAL APPROACH (Instead of removal)**
Rather than removing services, focus on consolidating **functionality** within services:
1. Merge overlapping fill detection methods within existing services
2. Consolidate WebSocket connections into single managers
3. Reduce polling redundancy between services

## RESOURCE OPTIMIZATION THROUGH CONSOLIDATION

**Current**: 17 services with overlapping functionality
**Target**: 17 services with streamlined, non-overlapping functions
**Benefits**: 
- Reduced CPU through elimination of duplicate processing
- Lower network overhead from fewer inter-service calls
- Simplified debugging with clearer separation of concerns

## POST-CLEANUP ARCHITECTURE

### **Minimal Core (7 services)**
```
Infrastructure: postgres, redis
Core Services: config, database, exchange, strategy, orchestrator  
```

### **With UI (8 services)**
```
Core + web-dashboard-service
```

### **With Monitoring (11 services)**
```  
Core + UI + prometheus, grafana, redis-exporter
```

## RISKS OF STOPPING SERVICES

**LOW RISK** (confirmed redundant):
- fill-detection-service ✅ Exchange service handles this
- order-sync-service ✅ Auto-closure handles this
- redis-exporter ✅ Just metrics
- mcp-service ✅ Unhealthy anyway

**MEDIUM RISK** (need investigation):
- order-queue-service ❓ Check orchestrator usage
- position-sync-service ❓ Check if positions auto-sync
- price-feed-service ❓ Check if exchange service sufficient

**HIGH RISK** (don't stop):
- Core 7 services - Would break trading system