#!/usr/bin/env python3
"""
Balance Update Failure Analysis Script
Analyzes potential balance update failure patterns in the orchestrator service
"""

import os
import re
import json
from datetime import datetime
from typing import Dict, List, Any

def analyze_balance_update_patterns():
    """Analyze balance update patterns in the codebase"""
    
    print("=== BALANCE UPDATE FAILURE ANALYSIS ===")
    print(f"Analysis started at: {datetime.now()}")
    print()
    
    # 1. Analyze orchestrator service balance update logic
    print("1. ANALYZING ORCHESTRATOR SERVICE BALANCE UPDATE LOGIC")
    print("=" * 60)
    
    orchestrator_file = "/Volumes/OWC Volume/Projects2025/Botv0.3/services/orchestrator-service/main.py"
    
    if os.path.exists(orchestrator_file):
        with open(orchestrator_file, 'r') as f:
            content = f.read()
            
        # Find balance update patterns
        balance_update_patterns = [
            # Trade exit balance updates
            {
                'pattern': r'balance_response = await client\.put\(f"\{database_service_url\}/api/v1/balances/\{exchange\}", json=balance_update_data\)',
                'context': 'Trade Exit Balance Update',
                'line_regex': r'.*balance_response.*put.*balances.*'
            },
            # Trade entry balance updates
            {
                'pattern': r'balance_response = await client\.put\(f"\{database_service_url\}/api/v1/balances/\{exchange_name\}", json=balance_update_data\)',
                'context': 'Trade Entry Balance Update',
                'line_regex': r'.*balance_response.*put.*balances.*'
            },
            # Balance error handling
            {
                'pattern': r'logger\.error\(f"Failed to update balance for \{.*\}: \{.*\}"\)',
                'context': 'Balance Update Error Logging',
                'line_regex': r'.*Failed to update balance.*'
            }
        ]
        
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            for pattern_info in balance_update_patterns:
                if re.search(pattern_info['line_regex'], line, re.IGNORECASE):
                    print(f"FOUND: {pattern_info['context']}")
                    print(f"Line {i}: {line.strip()}")
                    
                    # Show context around the line
                    start = max(0, i - 3)
                    end = min(len(lines), i + 3)
                    print("Context:")
                    for j in range(start, end):
                        prefix = ">>>" if j == i - 1 else "   "
                        print(f"{prefix} {j + 1}: {lines[j]}")
                    print("-" * 50)
    
    print()
    
    # 2. Analyze database service balance handling
    print("2. ANALYZING DATABASE SERVICE BALANCE HANDLING")
    print("=" * 60)
    
    db_service_file = "/Volumes/OWC Volume/Projects2025/Botv0.3/services/database-service/main.py"
    
    if os.path.exists(db_service_file):
        with open(db_service_file, 'r') as f:
            content = f.read()
            
        # Look for balance update endpoint implementation
        lines = content.split('\n')
        in_balance_update = False
        balance_update_lines = []
        
        for i, line in enumerate(lines, 1):
            if '@app.put("/api/v1/balances/{exchange_name}")' in line:
                in_balance_update = True
                start_line = i
            elif in_balance_update and line.strip().startswith('@app.') and i > start_line:
                in_balance_update = False
                break
            
            if in_balance_update:
                balance_update_lines.append((i, line))
        
        if balance_update_lines:
            print("BALANCE UPDATE ENDPOINT IMPLEMENTATION:")
            for line_num, line in balance_update_lines:
                print(f"{line_num}: {line}")
        print("-" * 50)
    
    print()
    
    # 3. Identify potential failure points
    print("3. POTENTIAL BALANCE UPDATE FAILURE POINTS")
    print("=" * 60)
    
    failure_points = [
        {
            'location': 'Orchestrator Service - Trade Exit',
            'issue': 'Balance update happens after trade close but may fail silently',
            'file': 'services/orchestrator-service/main.py',
            'line_range': '587-596',
            'code_snippet': 'balance_response = await client.put(...) \nif balance_response.status_code == 200:\n    # Update local cache\nelse:\n    logger.error(...)',
            'risk': 'HIGH - Trade closed but balance not updated'
        },
        {
            'location': 'Orchestrator Service - Trade Entry',  
            'issue': 'Balance deduction happens after trade creation, could fail',
            'file': 'services/orchestrator-service/main.py',
            'line_range': '775-784',
            'code_snippet': 'balance_response = await client.put(...)\nif balance_response.status_code == 200:\n    # Update local cache\nelse:\n    logger.error(...)',
            'risk': 'HIGH - Trade created but balance not deducted'
        },
        {
            'location': 'Database Service - Balance Update',
            'issue': 'Database transaction may fail without proper rollback',
            'file': 'services/database-service/main.py',
            'line_range': '590-611',
            'code_snippet': 'INSERT ... ON CONFLICT ... DO UPDATE SET ...',
            'risk': 'MEDIUM - Database constraint or connection issues'
        },
        {
            'location': 'Network Communication',
            'issue': 'HTTP requests between services may timeout or fail',
            'file': 'services/orchestrator-service/main.py',
            'line_range': 'Multiple locations',
            'code_snippet': 'async with httpx.AsyncClient() as client:',
            'risk': 'MEDIUM - Network issues between microservices'
        }
    ]
    
    for point in failure_points:
        print(f"FAILURE POINT: {point['location']}")
        print(f"Risk Level: {point['risk']}")
        print(f"Issue: {point['issue']}")
        print(f"File: {point['file']}")
        print(f"Lines: {point['line_range']}")
        print(f"Code Pattern: {point['code_snippet']}")
        print("-" * 50)
    
    print()
    
    # 4. Recommendations for debugging
    print("4. DEBUGGING RECOMMENDATIONS")
    print("=" * 60)
    
    recommendations = [
        "Add detailed logging before AND after each balance update attempt",
        "Implement balance update as database transactions with rollback capability",
        "Add balance validation checks after each update",
        "Create balance audit trail to track all changes",
        "Implement balance reconciliation checks periodically",
        "Add timeout and retry mechanisms for balance update HTTP calls",
        "Create balance consistency monitoring alerts",
        "Add database connection health checks before balance operations",
        "Implement balance update queues to handle failures gracefully",
        "Create balance backup/restore mechanisms"
    ]
    
    for i, rec in enumerate(recommendations, 1):
        print(f"{i}. {rec}")
    
    print()
    
    # 5. Suggested monitoring queries
    print("5. SUGGESTED DATABASE MONITORING QUERIES")
    print("=" * 60)
    
    queries = [
        {
            'purpose': 'Check for trades without corresponding balance updates',
            'query': """
SELECT t.trade_id, t.exchange, t.entry_time, t.exit_time, t.realized_pnl
FROM trading.trades t
LEFT JOIN trading.balance b ON t.exchange = b.exchange 
WHERE t.status = 'CLOSED' 
  AND t.realized_pnl IS NOT NULL
  AND (b.timestamp < t.exit_time OR b.timestamp IS NULL)
ORDER BY t.exit_time DESC;
"""
        },
        {
            'purpose': 'Check for balance inconsistencies',
            'query': """
SELECT exchange, 
       balance, 
       available_balance, 
       total_pnl, 
       daily_pnl,
       timestamp,
       (balance - available_balance) as locked_balance
FROM trading.balance 
ORDER BY exchange, timestamp DESC;
"""
        },
        {
            'purpose': 'Check for failed balance updates in recent period',
            'query': """
SELECT exchange, 
       COUNT(*) as update_count,
       MAX(timestamp) as last_update,
       MIN(timestamp) as first_update
FROM trading.balance 
WHERE timestamp >= NOW() - INTERVAL '24 hours'
GROUP BY exchange
ORDER BY update_count ASC;
"""
        },
        {
            'purpose': 'Check for trades with zero or negative PnL balance updates',
            'query': """
SELECT t.trade_id, t.exchange, t.realized_pnl, 
       b.timestamp as balance_update_time,
       b.total_pnl as balance_total_pnl
FROM trading.trades t
JOIN trading.balance b ON t.exchange = b.exchange
WHERE t.status = 'CLOSED' 
  AND t.realized_pnl != 0
  AND b.timestamp >= t.exit_time - INTERVAL '1 minute'
  AND b.timestamp <= t.exit_time + INTERVAL '5 minutes'
ORDER BY t.exit_time DESC;
"""
        }
    ]
    
    for query_info in queries:
        print(f"PURPOSE: {query_info['purpose']}")
        print("QUERY:")
        print(query_info['query'])
        print("-" * 50)
    
    print()
    print("=== ANALYSIS COMPLETE ===")
    print(f"Analysis completed at: {datetime.now()}")

if __name__ == "__main__":
    analyze_balance_update_patterns()