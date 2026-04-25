"""
Performance Metrics Tracker for Scalping Pair Selection
Tracks persistent performance metrics for spread width, slippage, and PnL per pair
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass, asdict
from collections import deque
import json
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass
class PairPerformanceMetrics:
    """Comprehensive performance metrics for a trading pair"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Spread performance
    avg_spread_1h: float
    avg_spread_1d: float
    avg_spread_1w: float
    spread_consistency: float
    spread_tightness_score: float
    
    # Slippage performance
    avg_slippage_1h: float
    avg_slippage_1d: float
    avg_slippage_1w: float
    slippage_consistency: float
    slippage_stability: float
    
    # Execution performance
    avg_execution_time: float
    execution_success_rate: float
    order_fill_rate: float
    market_impact_avg: float
    
    # PnL performance
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_profit_per_trade: float
    avg_loss_per_trade: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    
    # Risk metrics
    volatility_risk: float
    liquidity_risk: float
    execution_risk: float
    overall_risk_score: float
    
    # Quality scores
    performance_score: float  # 0-100 overall performance
    scalping_suitability: float  # 0-100 scalping suitability

class PerformanceMetricsTracker:
    """Tracks and analyzes performance metrics for trading pairs"""
    
    def __init__(self, config_manager, database_path: str = "performance_metrics.db"):
        self.config_manager = config_manager
        self.database_path = database_path
        self.metrics_cache: Dict[str, PairPerformanceMetrics] = {}
        self.trade_history: Dict[str, deque] = {}  # Store recent trade data
        self.max_trade_history = 1000  # Keep last 1000 trades per pair
        
        # Initialize database
        self._init_database()
        
    def _init_database(self):
        """Initialize SQLite database for performance metrics storage"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # Create performance_metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    avg_spread_1h REAL,
                    avg_spread_1d REAL,
                    avg_spread_1w REAL,
                    spread_consistency REAL,
                    spread_tightness_score REAL,
                    avg_slippage_1h REAL,
                    avg_slippage_1d REAL,
                    avg_slippage_1w REAL,
                    slippage_consistency REAL,
                    slippage_stability REAL,
                    avg_execution_time REAL,
                    execution_success_rate REAL,
                    order_fill_rate REAL,
                    market_impact_avg REAL,
                    total_trades INTEGER,
                    winning_trades INTEGER,
                    losing_trades INTEGER,
                    win_rate REAL,
                    avg_profit_per_trade REAL,
                    avg_loss_per_trade REAL,
                    profit_factor REAL,
                    sharpe_ratio REAL,
                    max_drawdown REAL,
                    volatility_risk REAL,
                    liquidity_risk REAL,
                    execution_risk REAL,
                    overall_risk_score REAL,
                    performance_score REAL,
                    scalping_suitability REAL,
                    UNIQUE(symbol, exchange, timestamp)
                )
            ''')
            
            # Create trade_history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    trade_type TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    quantity REAL,
                    pnl REAL,
                    spread_at_entry REAL,
                    slippage REAL,
                    execution_time REAL,
                    success BOOLEAN
                )
            ''')
            
            # Create indexes for better performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_performance_symbol ON performance_metrics(symbol, exchange)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_performance_timestamp ON performance_metrics(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_symbol ON trade_history(symbol, exchange)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_timestamp ON trade_history(timestamp)')
            
            conn.commit()
            conn.close()
            logger.info("Performance metrics database initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing performance metrics database: {e}")
    
    async def record_trade(self, symbol: str, exchange: str, trade_data: Dict):
        """Record a trade for performance analysis"""
        try:
            # Add to in-memory cache
            if symbol not in self.trade_history:
                self.trade_history[symbol] = deque(maxlen=self.max_trade_history)
            
            trade_record = {
                'timestamp': datetime.utcnow(),
                'symbol': symbol,
                'exchange': exchange,
                'trade_type': trade_data.get('type', 'unknown'),
                'entry_price': trade_data.get('entry_price', 0.0),
                'exit_price': trade_data.get('exit_price', 0.0),
                'quantity': trade_data.get('quantity', 0.0),
                'pnl': trade_data.get('pnl', 0.0),
                'spread_at_entry': trade_data.get('spread_at_entry', 0.0),
                'slippage': trade_data.get('slippage', 0.0),
                'execution_time': trade_data.get('execution_time', 0.0),
                'success': trade_data.get('success', False)
            }
            
            self.trade_history[symbol].append(trade_record)
            
            # Store in database
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO trade_history 
                (symbol, exchange, timestamp, trade_type, entry_price, exit_price, 
                 quantity, pnl, spread_at_entry, slippage, execution_time, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol, exchange, trade_record['timestamp'], trade_record['trade_type'],
                trade_record['entry_price'], trade_record['exit_price'], trade_record['quantity'],
                trade_record['pnl'], trade_record['spread_at_entry'], trade_record['slippage'],
                trade_record['execution_time'], trade_record['success']
            ))
            
            conn.commit()
            conn.close()
            
            logger.debug(f"Recorded trade for {symbol}: {trade_record}")
            
        except Exception as e:
            logger.error(f"Error recording trade for {symbol}: {e}")
    
    async def calculate_performance_metrics(self, symbol: str, exchange: str) -> Optional[PairPerformanceMetrics]:
        """Calculate comprehensive performance metrics for a trading pair"""
        try:
            # Get trade history from database
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # Get trades from last week
            week_ago = datetime.utcnow() - timedelta(days=7)
            cursor.execute('''
                SELECT * FROM trade_history 
                WHERE symbol = ? AND exchange = ? AND timestamp >= ?
                ORDER BY timestamp DESC
            ''', (symbol, exchange, week_ago))
            
            trades = cursor.fetchall()
            conn.close()
            
            if not trades:
                logger.warning(f"No trade history found for {symbol} on {exchange}")
                return None
            
            # Calculate spread performance metrics
            spread_metrics = self._calculate_spread_performance(trades)
            
            # Calculate slippage performance metrics
            slippage_metrics = self._calculate_slippage_performance(trades)
            
            # Calculate execution performance metrics
            execution_metrics = self._calculate_execution_performance(trades)
            
            # Calculate PnL performance metrics
            pnl_metrics = self._calculate_pnl_performance(trades)
            
            # Calculate risk metrics
            risk_metrics = self._calculate_risk_metrics(trades, spread_metrics, slippage_metrics)
            
            # Calculate quality scores
            performance_score = self._calculate_performance_score(pnl_metrics, execution_metrics, risk_metrics)
            scalping_suitability = self._calculate_scalping_suitability(
                spread_metrics, slippage_metrics, execution_metrics, pnl_metrics
            )
            
            metrics = PairPerformanceMetrics(
                symbol=symbol,
                exchange=exchange,
                timestamp=datetime.utcnow(),
                **spread_metrics,
                **slippage_metrics,
                **execution_metrics,
                **pnl_metrics,
                **risk_metrics,
                performance_score=performance_score,
                scalping_suitability=scalping_suitability
            )
            
            # Cache the metrics
            self.metrics_cache[f"{symbol}_{exchange}"] = metrics
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error calculating performance metrics for {symbol}: {e}")
            return None
    
    def _calculate_spread_performance(self, trades: List) -> Dict:
        """Calculate spread-related performance metrics"""
        if not trades:
            return {
                'avg_spread_1h': 0.0, 'avg_spread_1d': 0.0, 'avg_spread_1w': 0.0,
                'spread_consistency': 0.0, 'spread_tightness_score': 0.0
            }
        
        # Extract spread data (assuming spread_at_entry is in column 9)
        spreads = [trade[9] for trade in trades if trade[9] is not None and trade[9] > 0]
        
        if not spreads:
            return {
                'avg_spread_1h': 0.0, 'avg_spread_1d': 0.0, 'avg_spread_1w': 0.0,
                'spread_consistency': 0.0, 'spread_tightness_score': 0.0
            }
        
        # Calculate time-based averages
        now = datetime.utcnow()
        spreads_1h = [spread for i, trade in enumerate(trades) 
                     if (now - datetime.fromisoformat(trade[3])).total_seconds() <= 3600]
        spreads_1d = [spread for i, trade in enumerate(trades) 
                     if (now - datetime.fromisoformat(trade[3])).total_seconds() <= 86400]
        
        avg_spread_1h = np.mean(spreads_1h) if spreads_1h else 0.0
        avg_spread_1d = np.mean(spreads_1d) if spreads_1d else 0.0
        avg_spread_1w = np.mean(spreads)
        
        # Calculate consistency (inverse of coefficient of variation)
        spread_std = np.std(spreads)
        spread_mean = np.mean(spreads)
        spread_consistency = max(0, 100 - (spread_std / spread_mean * 100)) if spread_mean > 0 else 0.0
        
        # Calculate tightness score (lower spread = higher score)
        spread_tightness_score = max(0, 100 - (avg_spread_1w * 100)) if avg_spread_1w > 0 else 0.0
        
        return {
            'avg_spread_1h': avg_spread_1h,
            'avg_spread_1d': avg_spread_1d,
            'avg_spread_1w': avg_spread_1w,
            'spread_consistency': spread_consistency,
            'spread_tightness_score': spread_tightness_score
        }
    
    def _calculate_slippage_performance(self, trades: List) -> Dict:
        """Calculate slippage-related performance metrics"""
        if not trades:
            return {
                'avg_slippage_1h': 0.0, 'avg_slippage_1d': 0.0, 'avg_slippage_1w': 0.0,
                'slippage_consistency': 0.0, 'slippage_stability': 0.0
            }
        
        # Extract slippage data (assuming slippage is in column 10)
        slippages = [trade[10] for trade in trades if trade[10] is not None and trade[10] >= 0]
        
        if not slippages:
            return {
                'avg_slippage_1h': 0.0, 'avg_slippage_1d': 0.0, 'avg_slippage_1w': 0.0,
                'slippage_consistency': 0.0, 'slippage_stability': 0.0
            }
        
        # Calculate time-based averages
        now = datetime.utcnow()
        slippages_1h = [slippage for i, trade in enumerate(trades) 
                       if (now - datetime.fromisoformat(trade[3])).total_seconds() <= 3600]
        slippages_1d = [slippage for i, trade in enumerate(trades) 
                       if (now - datetime.fromisoformat(trade[3])).total_seconds() <= 86400]
        
        avg_slippage_1h = np.mean(slippages_1h) if slippages_1h else 0.0
        avg_slippage_1d = np.mean(slippages_1d) if slippages_1d else 0.0
        avg_slippage_1w = np.mean(slippages)
        
        # Calculate consistency
        slippage_std = np.std(slippages)
        slippage_mean = np.mean(slippages)
        slippage_consistency = max(0, 100 - (slippage_std / slippage_mean * 100)) if slippage_mean > 0 else 0.0
        
        # Calculate stability (inverse of average slippage)
        slippage_stability = max(0, 100 - (avg_slippage_1w * 100)) if avg_slippage_1w > 0 else 100.0
        
        return {
            'avg_slippage_1h': avg_slippage_1h,
            'avg_slippage_1d': avg_slippage_1d,
            'avg_slippage_1w': avg_slippage_1w,
            'slippage_consistency': slippage_consistency,
            'slippage_stability': slippage_stability
        }
    
    def _calculate_execution_performance(self, trades: List) -> Dict:
        """Calculate execution-related performance metrics"""
        if not trades:
            return {
                'avg_execution_time': 0.0, 'execution_success_rate': 0.0,
                'order_fill_rate': 0.0, 'market_impact_avg': 0.0
            }
        
        # Extract execution data
        execution_times = [trade[11] for trade in trades if trade[11] is not None and trade[11] > 0]
        successes = [trade[12] for trade in trades if trade[12] is not None]
        
        avg_execution_time = np.mean(execution_times) if execution_times else 0.0
        execution_success_rate = (sum(successes) / len(successes) * 100) if successes else 0.0
        order_fill_rate = execution_success_rate  # Assuming same for now
        
        # Market impact calculation (simplified)
        market_impact_avg = 0.0  # Would need more complex calculation
        
        return {
            'avg_execution_time': avg_execution_time,
            'execution_success_rate': execution_success_rate,
            'order_fill_rate': order_fill_rate,
            'market_impact_avg': market_impact_avg
        }
    
    def _calculate_pnl_performance(self, trades: List) -> Dict:
        """Calculate PnL-related performance metrics"""
        if not trades:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0.0, 'avg_profit_per_trade': 0.0, 'avg_loss_per_trade': 0.0,
                'profit_factor': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0
            }
        
        # Extract PnL data (assuming pnl is in column 7)
        pnls = [trade[7] for trade in trades if trade[7] is not None]
        
        if not pnls:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0.0, 'avg_profit_per_trade': 0.0, 'avg_loss_per_trade': 0.0,
                'profit_factor': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0
            }
        
        total_trades = len(pnls)
        winning_trades = sum(1 for pnl in pnls if pnl > 0)
        losing_trades = sum(1 for pnl in pnls if pnl < 0)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        
        profits = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl < 0]
        
        avg_profit_per_trade = np.mean(profits) if profits else 0.0
        avg_loss_per_trade = np.mean(losses) if losses else 0.0
        
        # Profit factor
        total_profit = sum(profits) if profits else 0.0
        total_loss = sum(losses) if losses else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
        
        # Sharpe ratio (simplified)
        if len(pnls) > 1:
            sharpe_ratio = np.mean(pnls) / np.std(pnls) if np.std(pnls) > 0 else 0.0
        else:
            sharpe_ratio = 0.0
        
        # Max drawdown
        cumulative_pnl = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative_pnl)
        drawdowns = running_max - cumulative_pnl
        max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0.0
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'avg_profit_per_trade': avg_profit_per_trade,
            'avg_loss_per_trade': avg_loss_per_trade,
            'profit_factor': profit_factor,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown
        }
    
    def _calculate_risk_metrics(self, trades: List, spread_metrics: Dict, slippage_metrics: Dict) -> Dict:
        """Calculate risk-related metrics"""
        # Volatility risk (based on PnL variance)
        pnls = [trade[7] for trade in trades if trade[7] is not None]
        volatility_risk = np.std(pnls) if len(pnls) > 1 else 0.0
        
        # Liquidity risk (based on spread consistency)
        liquidity_risk = 100 - spread_metrics.get('spread_consistency', 0.0)
        
        # Execution risk (based on slippage stability)
        execution_risk = 100 - slippage_metrics.get('slippage_stability', 0.0)
        
        # Overall risk score
        overall_risk_score = (volatility_risk + liquidity_risk + execution_risk) / 3
        
        return {
            'volatility_risk': volatility_risk,
            'liquidity_risk': liquidity_risk,
            'execution_risk': execution_risk,
            'overall_risk_score': overall_risk_score
        }
    
    def _calculate_performance_score(self, pnl_metrics: Dict, execution_metrics: Dict, risk_metrics: Dict) -> float:
        """Calculate overall performance score (0-100)"""
        score = 0.0
        
        # PnL component (50% weight)
        win_rate = pnl_metrics.get('win_rate', 0.0)
        profit_factor = pnl_metrics.get('profit_factor', 0.0)
        pnl_score = (win_rate * 0.6) + (min(100, profit_factor * 20) * 0.4)
        score += pnl_score * 0.5
        
        # Execution component (30% weight)
        execution_score = execution_metrics.get('execution_success_rate', 0.0)
        score += execution_score * 0.3
        
        # Risk component (20% weight)
        risk_score = max(0, 100 - risk_metrics.get('overall_risk_score', 0.0))
        score += risk_score * 0.2
        
        return min(100, max(0, score))
    
    def _calculate_scalping_suitability(self, spread_metrics: Dict, slippage_metrics: Dict,
                                      execution_metrics: Dict, pnl_metrics: Dict) -> float:
        """Calculate scalping suitability score (0-100)"""
        score = 0.0
        
        # Spread tightness (30% weight)
        spread_score = spread_metrics.get('spread_tightness_score', 0.0)
        score += spread_score * 0.3
        
        # Slippage stability (25% weight)
        slippage_score = slippage_metrics.get('slippage_stability', 0.0)
        score += slippage_score * 0.25
        
        # Execution success (25% weight)
        execution_score = execution_metrics.get('execution_success_rate', 0.0)
        score += execution_score * 0.25
        
        # Profitability (20% weight)
        profit_score = pnl_metrics.get('win_rate', 0.0)
        score += profit_score * 0.2
        
        return min(100, max(0, score))
    
    async def get_performance_ranked_pairs(self, exchange_name: str, base_currency: str, 
                                         num_pairs: int = 15) -> List[Tuple[str, float]]:
        """Get pairs ranked by performance metrics for scalping suitability"""
        try:
            # Get all pairs with performance data
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT DISTINCT symbol FROM trade_history 
                WHERE exchange = ? AND timestamp >= ?
            ''', (exchange_name, datetime.utcnow() - timedelta(days=7)))
            
            symbols = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            # Calculate performance metrics for each pair
            pair_scores = []
            for symbol in symbols:
                metrics = await self.calculate_performance_metrics(symbol, exchange_name)
                if metrics and metrics.scalping_suitability > 60:
                    pair_scores.append((symbol, metrics.scalping_suitability))
            
            # Sort by performance score and return top pairs
            pair_scores.sort(key=lambda x: x[1], reverse=True)
            return pair_scores[:num_pairs]
            
        except Exception as e:
            logger.error(f"Error getting performance-ranked pairs for {exchange_name}: {e}")
            return []
    
    async def save_performance_metrics(self, metrics: PairPerformanceMetrics):
        """Save performance metrics to database"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO performance_metrics 
                (symbol, exchange, timestamp, avg_spread_1h, avg_spread_1d, avg_spread_1w,
                 spread_consistency, spread_tightness_score, avg_slippage_1h, avg_slippage_1d,
                 avg_slippage_1w, slippage_consistency, slippage_stability, avg_execution_time,
                 execution_success_rate, order_fill_rate, market_impact_avg, total_trades,
                 winning_trades, losing_trades, win_rate, avg_profit_per_trade, avg_loss_per_trade,
                 profit_factor, sharpe_ratio, max_drawdown, volatility_risk, liquidity_risk,
                 execution_risk, overall_risk_score, performance_score, scalping_suitability)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                metrics.symbol, metrics.exchange, metrics.timestamp,
                metrics.avg_spread_1h, metrics.avg_spread_1d, metrics.avg_spread_1w,
                metrics.spread_consistency, metrics.spread_tightness_score,
                metrics.avg_slippage_1h, metrics.avg_slippage_1d, metrics.avg_slippage_1w,
                metrics.slippage_consistency, metrics.slippage_stability,
                metrics.avg_execution_time, metrics.execution_success_rate,
                metrics.order_fill_rate, metrics.market_impact_avg,
                metrics.total_trades, metrics.winning_trades, metrics.losing_trades,
                metrics.win_rate, metrics.avg_profit_per_trade, metrics.avg_loss_per_trade,
                metrics.profit_factor, metrics.sharpe_ratio, metrics.max_drawdown,
                metrics.volatility_risk, metrics.liquidity_risk, metrics.execution_risk,
                metrics.overall_risk_score, metrics.performance_score, metrics.scalping_suitability
            ))
            
            conn.commit()
            conn.close()
            
            logger.debug(f"Saved performance metrics for {metrics.symbol}")
            
        except Exception as e:
            logger.error(f"Error saving performance metrics for {metrics.symbol}: {e}")
