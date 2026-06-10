const STORAGE_KEYS = {
  theme: 'hlPerpsTheme',
  sort: 'hlPerpsTableSort',
  columns: 'hlPerpsColumnVisibility',
  watchlistStatus: 'hlPerpsWatchlistStatus',
  watchlistPageSize: 'hlPerpsWatchlistPageSize',
  pnlPageSize: 'hlPerpsPnlPageSize',
  adaptivePageSize: 'hlPerpsAdaptivePageSize',
  tradesPageSize: 'hlPerpsTradesPageSize',
  sdPageSize: 'hlPerpsSdPageSize',
  dsmaPageSize: 'hlPerpsDsmaPageSize',
  ebpPageSize: 'hlPerpsEbpPageSize',
  orbPageSize: 'hlPerpsOrbPageSize',
};

const PAGE_SIZES = [10, 50, 100];
const DEFAULT_PAGE_SIZE = 10;

const PNL_TAB_DEFAULT = 'strategy';

function loadPageSize(storageKey) {
  const saved = Number(loadStorage(storageKey, DEFAULT_PAGE_SIZE));
  return PAGE_SIZES.includes(saved) ? saved : DEFAULT_PAGE_SIZE;
}

const state = {
  payload: null,
  tradeFilter: 'open',
  pnlTab: PNL_TAB_DEFAULT,
  watchlistActiveOnly: false,
  watchlistStatusFilter: loadStorage(STORAGE_KEYS.watchlistStatus, 'all'),
  pagination: {
    watchlist: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.watchlistPageSize) },
    pnl: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.pnlPageSize) },
    adaptive: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.adaptivePageSize) },
    trades: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.tradesPageSize) },
    sd: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.sdPageSize) },
    dsma: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.dsmaPageSize) },
    ebp: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.ebpPageSize) },
    orb: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.orbPageSize) },
  },
  lastPrices: {},
  priceComparisonBaseline: {},
  lastSnapshotTimestamp: null,
  themeMode: loadStorage(STORAGE_KEYS.theme, 'system'),
  sort: loadStorage(STORAGE_KEYS.sort, {
    watchlist: { key: 'coin', direction: 'asc' },
    trades: { key: 'time', direction: 'desc' },
  }),
  columns: loadStorage(STORAGE_KEYS.columns, {}),
  watchlistExpandedCoins: new Set(),
};

const money = (value) => {
  const n = Number(value || 0);
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2 });
};

const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const num = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: digits });
const cls = (value) => Number(value || 0) < 0 ? 'negative' : Number(value || 0) > 0 ? 'positive' : '';
const decimalPct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;

const priceMoney = (value) => {
  const n = Number(value || 0);
  const digits = Math.abs(n) >= 100 ? 2 : Math.abs(n) >= 1 ? 4 : 6;
  return n.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  });
};

function loadStorage(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch (_) {
    return fallback;
  }
}

function saveStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // localStorage can be unavailable in private contexts; keep the session state.
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setText(id, value, toneValue = null) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  el.classList.remove('positive', 'negative');
  if (toneValue !== null) {
    const toneClass = cls(toneValue);
    if (toneClass) el.classList.add(toneClass);
  }
}

function safeRender(section, fn, data) {
  try {
    fn(data);
  } catch (err) {
    console.error(`[hl-dashboard] ${section} render failed:`, err);
  }
}

function formatRelativeTime(value) {
  if (!value) return 'N/A';
  const normalized = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && !/[zZ]|[+-]\d{2}:\d{2}$/.test(value)
    ? `${value}Z`
    : value;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return 'N/A';
  const seconds = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function watchlistRowKey(row) {
  return String(row?.coin || row?.displayCoin || '').trim().toUpperCase();
}

function cycleAnalysisOf(row) {
  return row?.cycleAnalysis || {};
}

function perpEntryCycleIntervalSec(data) {
  const cycles = (data?.tradingStatus || {}).cycles || {};
  const phase = cycles.perp_entry || {};
  const interval = Number(phase.interval_seconds || phase.intervalSec || 0);
  return interval > 0 ? interval : 60;
}

function cycleAnalysisIsStale(row, data) {
  const cycleAt = cycleAnalysisOf(row).cycleAt;
  if (!cycleAt) return false;
  const normalized = typeof cycleAt === 'string' && !/[zZ]|[+-]\d{2}:\d{2}$/.test(cycleAt)
    ? `${cycleAt}Z`
    : cycleAt;
  const ts = new Date(normalized).getTime();
  if (Number.isNaN(ts)) return false;
  const maxAgeMs = perpEntryCycleIntervalSec(data) * 2 * 1000;
  return Date.now() - ts > maxAgeMs;
}

function signalChip(side) {
  const normalized = String(side || 'hold').toLowerCase();
  if (normalized === 'long') return `<span class="pi-strategy-chip long">${escapeHtml(normalized.toUpperCase())}</span>`;
  if (normalized === 'short') return `<span class="pi-strategy-chip short">${escapeHtml(normalized.toUpperCase())}</span>`;
  return `<span class="pi-strategy-chip hold">${escapeHtml(normalized.toUpperCase())}</span>`;
}

function entryDecisionTagClass(outcome, primaryReason) {
  const o = String(outcome || '').toLowerCase();
  const r = String(primaryReason || '').toLowerCase();
  if (o === 'entered') return 'ok';
  if (o === 'not_scanned' && r === 'not_scanned') return 'muted';
  if (['blocked_pre_signal', 'blocked'].includes(o)) return 'risk';
  if (['edge_gate', 'reentry_cooldown', 'daily_halt', 'max_positions', 'session_block'].includes(r)) return 'risk';
  return 'watch';
}

function formatCycleEntryDecision(row, data) {
  if (row.hasOpenPosition || String(row.status || '').toLowerCase() === 'open') {
    return '<span class="pi-tag watch">Position open — no new entry</span>';
  }
  const ca = cycleAnalysisOf(row);
  const decision = ca.entryDecision || {};
  const outcome = String(decision.outcome || 'not_scanned').toLowerCase();
  const reason = decision.primaryReason || outcome;
  const message = decision.message || '';
  const stale = cycleAnalysisIsStale(row, data);
  const labelMap = {
    entered: 'Entered',
    skipped: 'Skipped',
    no_actionable_signal: 'No signal',
    blocked_pre_signal: 'Blocked',
    not_scanned: 'Not scanned',
  };
  const prefix = labelMap[outcome] || outcome.replace(/_/g, ' ');
  const detail = message || reason.replace(/_/g, ' ');
  const tagClass = entryDecisionTagClass(outcome, reason);
  const staleTag = stale ? ' <span class="pi-tag muted">Stale</span>' : '';
  if (outcome === 'not_scanned' && !message) {
    return `<span class="pi-tag muted">Not scanned · last cycle N/A</span>${staleTag}`;
  }
  return `<span class="pi-tag ${tagClass}">${escapeHtml(prefix)}${detail ? `: ${escapeHtml(detail)}` : ''}</span>${staleTag}`;
}

function currentEntryDecisionText(row) {
  const d = cycleAnalysisOf(row).entryDecision || {};
  const outcome = String(d.outcome || '').replace(/_/g, ' ');
  const reason = String(d.primaryReason || '').replace(/_/g, ' ');
  const message = String(d.message || '').trim();
  if (message) return `${outcome || 'entry'}: ${message}`;
  if (reason) return `${outcome || 'entry'}: ${reason}`;
  return '';
}

function formatCycleConsensus(row) {
  const consensus = cycleAnalysisOf(row).consensus || {};
  const signal = String(consensus.signal || 'hold').toUpperCase();
  const conf = Number(consensus.confidence || 0);
  const agreement = Number(consensus.agreement || 0);
  return `${signal} · ${conf.toFixed(2)} · ${agreement.toFixed(0)}%`;
}

function formatCycleBestSignal(row) {
  const best = cycleAnalysisOf(row).bestSignal;
  if (!best || !best.strategy) return '—';
  const side = String(best.side || 'hold').toUpperCase();
  const conf = Number(best.confidence || 0);
  return `${escapeHtml(best.strategy)} ${signalChip(side)} ${conf.toFixed(2)}`;
}

function renderCycleDetailRow(row, colspan) {
  const ca = cycleAnalysisOf(row);
  const strategies = ca.strategies || [];
  const gateChain = (ca.entryDecision || {}).gateChain || [];
  const meta = [
    ca.regime ? `Regime: ${escapeHtml(ca.regime)}` : null,
    ca.cycleAt ? `Scanned ${escapeHtml(formatRelativeTime(ca.cycleAt))}` : null,
    ca.cycleCount ? `Cycle #${escapeHtml(String(ca.cycleCount))}` : null,
  ].filter(Boolean).join(' · ');
  const matrix = strategies.length
    ? `<table class="pi-cycle-strategy-table">
        <thead><tr><th>Strategy</th><th>Signal</th><th>Conf</th><th>Strength</th><th>Reason</th></tr></thead>
        <tbody>${strategies.map((s) => `
          <tr>
            <td>${escapeHtml(s.name || '')}</td>
            <td>${signalChip(s.signal)}</td>
            <td>${Number(s.confidence || 0).toFixed(2)}</td>
            <td>${Number(s.strength || 0).toFixed(2)}</td>
            <td class="pi-cycle-reason">${escapeHtml(s.reason || '')}</td>
          </tr>`).join('')}
        </tbody>
      </table>`
    : '<p class="pi-muted-cell">No strategy data from the last scan.</p>';
  const gates = gateChain.length
    ? `<div class="pi-gate-chain-wrap"><h4 class="pi-cycle-subhead">Gate chain</h4>
        <ol class="pi-gate-chain">${gateChain.map((g, i) => {
          const cls = g.passed ? 'pi-gate-pass' : 'pi-gate-block';
          const msg = g.message ? `: ${escapeHtml(g.message)}` : '';
          return `<li class="${cls}"><span class="pi-gate-step">${i + 1}. ${escapeHtml(String(g.step || '').replace(/_/g, ' '))}${msg}</span></li>`;
        }).join('')}</ol></div>`
    : '';
  return `<tr class="pi-cycle-detail" data-detail-coin="${escapeHtml(watchlistRowKey(row))}">
    <td colspan="${colspan}">
      <div class="pi-cycle-detail-inner">
        ${meta ? `<div class="pi-cycle-meta">${meta}</div>` : ''}
        ${matrix}
        ${gates}
      </div>
    </td>
  </tr>`;
}

function toggleWatchlistExpand(coinKey) {
  const key = String(coinKey || '').trim().toUpperCase();
  if (!key) return;
  if (state.watchlistExpandedCoins.has(key)) state.watchlistExpandedCoins.delete(key);
  else state.watchlistExpandedCoins.add(key);
}

function renderCyclePill(id, label, phase, health) {
  const el = document.getElementById(id);
  if (!el) return;
  const phaseData = phase || {};
  const phaseHealth = health || {};
  const status = String(phaseData.status || 'never_run');
  const ranAt = phaseData.completed_at || phaseData.started_at;
  const cycle = Number(phaseData.cycle_count || 0);
  const cycleText = cycle > 0 ? ` · cycle ${cycle}` : '';
  el.textContent = `${label}: ${formatRelativeTime(ranAt)} · ${status.replace(/_/g, ' ')}${cycleText}`;
  el.classList.remove('ok', 'warn', 'bad');
  if (status === 'error' || phaseHealth.ok === false) {
    el.classList.add('bad');
  } else if (status === 'completed') {
    el.classList.add('ok');
  } else {
    el.classList.add('warn');
  }
}

function cycleShortText(phase) {
  const phaseData = phase || {};
  const status = String(phaseData.status || 'never_run').replace(/_/g, ' ');
  return `${formatRelativeTime(phaseData.completed_at || phaseData.started_at)} ${status}`;
}

function renderCycleStatus(data) {
  const trading = data.tradingStatus || {};
  const cycles = trading.cycles || {};
  const details = ((trading.cycle_health || {}).details) || {};
  renderCyclePill('perp-entry-cycle', 'Perps entry last run', cycles.perp_entry, details.perp_entry);
  renderCyclePill('perp-exit-cycle', 'Perps exit/update last run', cycles.perp_exit_update, details.perp_exit_update);
  renderCyclePill('perp-entry-cycle-top', 'Entry', cycles.perp_entry, details.perp_entry);
  renderCyclePill('perp-exit-cycle-top', 'Exit', cycles.perp_exit_update, details.perp_exit_update);
  setText(
    'trading-status',
    `Bot: ${trading.status || trading.trading_status || 'unknown'} · Entry ${cycleShortText(cycles.perp_entry)} · Exit ${cycleShortText(cycles.perp_exit_update)}`
  );
}

function statusLabel(status) {
  const key = String(status || '').toLowerCase();
  const map = {
    open: 'Open position',
    blocked: 'Blocked',
    under_analysis: 'Under analysis',
    active: 'Under analysis',
    mirroring: 'Under analysis',
    selected: 'Under analysis',
    allowed_no_mirror: 'Under analysis',
    inactive: 'Not in top 20',
    no_price: 'No price',
  };
  return map[key] || 'Under analysis';
}

function statusClass(status) {
  const key = String(status || '').toLowerCase();
  if (key === 'open') return 'hot';
  if (key === 'blocked' || key === 'no_price') return 'risk';
  return 'watch';
}

function entryBlockLabel(row) {
  if (!row?.entryBlocked) return '';
  const reason = String(row.entryBlockReason || '').toLowerCase();
  const map = {
    open_unrealized_negative: 'Blocked: open loss',
    recent_negative_realized: 'Blocked: 4h loss cooldown',
    recent_negative_realized_12h: 'Blocked: 4h loss cooldown',
    no_price: 'Blocked: no price',
    blacklisted: 'Blocked: blacklisted',
    excluded_selector: 'Blocked: dropped from top 20',
    engine_disabled: 'Blocked: engine off',
    trading_stopped: 'Blocked: bot stopped',
    max_open_positions: 'Blocked: max positions',
    daily_loss_halt: 'Blocked: daily loss halt',
    live_kill_switch: 'Blocked: kill switch',
  };
  if (row.excludedFromSelector && reason === 'open_unrealized_negative') {
    return 'Blocked: open loss (off list)';
  }
  if (row.excludedFromSelector && !map[reason]) {
    return 'Blocked: off top-20 list';
  }
  return map[reason] || `Blocked: ${reason.replace(/_/g, ' ')}`;
}

function blockedSides(row) {
  return Array.isArray(row?.blockedSides)
    ? row.blockedSides.map((side) => String(side).toLowerCase()).filter(Boolean)
    : [];
}

function sideBlockLabel(row) {
  const sides = blockedSides(row);
  const hasLong = sides.includes('long');
  const hasShort = sides.includes('short');
  if (hasLong && hasShort) return 'Long/Short blocked';
  if (hasLong) return 'Long blocked';
  if (hasShort) return 'Short blocked';
  return '';
}

function sideBlockTitle(row) {
  const bySide = row?.entryBlockBySide || {};
  return blockedSides(row)
    .map((side) => {
      const block = bySide[side] || {};
      const message = block.entryBlockMessage || block.entryBlockReason || '';
      return message ? `${side}: ${message}` : side;
    })
    .join('\n');
}

function ema50EntryBlockLabel(row) {
  const reason = String(row?.entry_block_reason || '').toLowerCase();
  const detail = String(row?.entry_block_detail || '').trim();
  if (!reason) return { label: '-', className: '', title: '' };
  if (reason === 'eligible') {
    return {
      label: 'Setup eligible',
      className: 'watch',
      title: detail
        ? `${detail}. Not order-ready until the orchestrator scan and sizing gates pass.`
        : 'Setup passes strategy pre-entry checks; order still requires orchestrator scan and sizing gates.',
    };
  }
  const map = {
    setup_incomplete: 'Setup incomplete',
    open_unrealized_negative: 'Open loss block',
    recent_negative_realized: 'Loss cooldown',
    position_already_open: 'Already open',
    engine_disabled: 'Engine off',
    trading_stopped: 'Bot stopped',
    max_open_positions: 'Max positions',
    daily_loss_halt: 'Daily loss halt',
    live_kill_switch: 'Kill switch',
    global_block: 'Global block',
    invalid_symbol: 'Invalid symbol',
  };
  let label = map[reason] || reason.replace(/_/g, ' ');
  if (reason === 'setup_incomplete' && detail) {
    if (detail.startsWith('setup:')) {
      label = detail.slice(6).replace(/_/g, ' ');
    } else if (detail.startsWith('regime_blocked:')) {
      label = detail.replace('regime_blocked:', 'Regime: ');
    } else if (detail.startsWith('awaiting_')) {
      label = detail.replace(/_/g, ' ');
    } else if (detail.startsWith('close_')) {
      label = 'Awaiting trigger';
    } else {
      label = detail.length > 36 ? `${detail.slice(0, 36)}…` : detail;
    }
  }
  return { label, className: 'risk', title: detail || label };
}

function strategyExecutionReason(row, setupReason = '') {
  const block = ema50EntryBlockLabel(row);
  const detail = String(block.title || block.label || '').trim();
  const setup = String(setupReason || '').trim();
  if (row.entry_block_reason) {
    if (String(row.entry_block_reason).toLowerCase() === 'eligible') {
      return setup
        ? `${setup} | Execution: setup eligible only; waiting for orchestrator scan, position limits, min notional, and sizing gates.`
        : 'Execution: setup eligible only; waiting for orchestrator scan, position limits, min notional, and sizing gates.';
    }
    return setup ? `${setup} | Execution blocked: ${detail}` : `Execution blocked: ${detail}`;
  }
  const signal = String(row.signal || '').toLowerCase();
  if (['buy', 'sell', 'long', 'short'].includes(signal)) {
    return setup
      ? `${setup} | Execution: strategy signal only; order still depends on orchestrator gates.`
      : 'Execution: strategy signal only; order still depends on orchestrator gates.';
  }
  return setup || 'No actionable setup.';
}

function listMembershipHint(row) {
  const m = String(row.listMembership || '').toLowerCase();
  const map = {
    active_selector: 'Top-20 active',
    excluded_selector: 'Excluded from top 20',
    side_blocked: 'One side blocked',
    blacklisted: 'Config blacklist',
    open_only: 'Open only (not in top 20)',
    global_halt: 'Global halt',
    candidate_only: 'Liquid market',
  };
  return map[m] || '';
}

function formatTradeTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function toDecimal(value) {
  const n = Number(value || 0);
  return Math.abs(n) > 1 ? n / 100 : n;
}

function sideTargetPrice(entry, side, decimal, favorable = true) {
  if (!entry || !decimal) return 0;
  const s = String(side || '').toLowerCase();
  if (s === 'short') return entry * (1 + (favorable ? -decimal : decimal));
  return entry * (1 + (favorable ? decimal : -decimal));
}

function currentPriceForTrade(t, data) {
  const direct = Number(t.current_price ?? t.currentPrice ?? 0);
  if (direct > 0) return direct;
  const coin = String(t.coin || t.symbol || '').toUpperCase();
  const row = (((data.hyperliquid || {}).watchlist) || [])
    .find((item) => String(item.coin || '').toUpperCase() === coin);
  return Number(row?.midPrice || 0);
}

function tradePriceKey(t) {
  return String(t.trade_id ?? t.tradeId ?? t.coin ?? t.symbol ?? '');
}

function priceMovementMeta(currentPrice, baselinePrice) {
  if (!currentPrice || !baselinePrice) return null;
  const delta = currentPrice - baselinePrice;
  const deltaPct = (delta / baselinePrice) * 100;
  const absDeltaPct = Math.abs(deltaPct);
  const direction = absDeltaPct < 0.0001 ? 'flat' : delta > 0 ? 'up' : 'down';
  const label = direction === 'flat'
    ? 'flat'
    : `${direction} ${deltaPct > 0 ? '+' : ''}${deltaPct.toFixed(2)}%`;
  return { direction, label, deltaPct };
}

function priceDirectionCell(label, direction, tone = 'neutral') {
  return `
    <span class="pi-direction ${direction} ${tone}" aria-label="${escapeHtml(label)}">
      <span class="pi-direction-arrow"></span>
      <span class="pi-direction-label">${escapeHtml(label)}</span>
    </span>`;
}

function currentPriceCell(t, currentPrice, entryPrice, side) {
  if (!currentPrice) return '—';
  const key = tradePriceKey(t);
  const previousPrice = Number(state.priceComparisonBaseline[key] || 0);
  const baseline = previousPrice > 0 ? previousPrice : entryPrice;
  const scope = previousPrice > 0 ? 'previous dashboard refresh' : 'entry';
  const movement = priceMovementMeta(currentPrice, baseline);
  const s = String(side || '').toLowerCase();
  const favorable = !movement || movement.direction === 'flat'
    ? 'neutral'
    : ((s === 'short' && movement.direction === 'down') || (s !== 'short' && movement.direction === 'up'))
      ? 'favorable'
      : 'adverse';
  const moveLabel = movement?.label || 'flat';
  return `
    <span class="pi-current-cell" title="Current price ${moveLabel} vs ${scope}">
      <span>${priceMoney(currentPrice)}</span>
      ${movement ? priceDirectionCell(movement.label, movement.direction, favorable) : ''}
    </span>`;
}

function watchlistPriceKey(row) {
  return String(row?.coin || '').toUpperCase();
}

function effectiveWatchlistPrice(row) {
  const hl = Number(row?.midPrice || 0);
  if (hl > 0) return { price: hl, source: 'hl' };
  const position = Number(row?.lastPrice || row?.currentPrice || 0);
  if (position > 0) return { price: position, source: 'position' };
  const cached = Number(state.lastPrices[watchlistPriceKey(row)] || 0);
  if (cached > 0) return { price: cached, source: 'cached' };
  return { price: 0, source: null };
}

function watchlistPriceCell(row) {
  const { price, source } = effectiveWatchlistPrice(row);
  if (!price) return '—';
  const sourceLabel = source === 'hl'
    ? 'Hyperliquid mid'
    : source === 'position'
      ? 'Last position mark'
      : 'Last dashboard price';
  const sourceBadge = source !== 'hl'
    ? `<span class="pi-tag watch pi-price-source">Last</span>`
    : '';
  return `
    <span class="pi-current-cell" title="${escapeHtml(sourceLabel)}">
      <span class="pi-price-line">${priceMoney(price)}${sourceBadge}</span>
    </span>`;
}

function watchlistMoveCell(row) {
  const { price } = effectiveWatchlistPrice(row);
  if (!price) return '—';
  const baseline = Number(state.priceComparisonBaseline[watchlistPriceKey(row)] || 0);
  const movement = priceMovementMeta(price, baseline);
  if (!movement) {
    return '<span class="pi-muted-cell" title="Movement appears after the next refresh">—</span>';
  }
  const tone = movement.direction === 'flat' ? 'neutral' : movement.direction === 'up' ? 'favorable' : 'adverse';
  return `
    <span class="pi-current-cell" title="Change since previous dashboard refresh (~30s)">
      ${priceDirectionCell(movement.label, movement.direction, tone)}
    </span>`;
}

function rememberWatchlistPrices(data) {
  (((data || {}).hyperliquid || {}).watchlist || []).forEach((row) => {
    const key = watchlistPriceKey(row);
    const { price } = effectiveWatchlistPrice(row);
    if (key && price > 0) state.lastPrices[key] = price;
  });
}

function tradeMetadata(t) {
  const raw = t.metadata ?? t.meta ?? {};
  if (!raw) return {};
  if (typeof raw === 'string') {
    try { return JSON.parse(raw); } catch (_) { return {}; }
  }
  return raw;
}

function protectionStatusCell(label, detail, active = false) {
  return `
    <div class="pi-protection-cell">
      <span class="pi-protection-state ${active ? 'active' : 'inactive'}">${escapeHtml(label)}</span>
      <span class="pi-protection-detail">${detail}</span>
    </div>`;
}

function exitRulesForTrade(t, data) {
  if (t.exitRules && typeof t.exitRules === 'object') {
    return t.exitRules;
  }
  return ((data.hyperliquid || {}).config || {}).entryRules || {};
}

function unrealizedPctForTrade({ side, entryPrice, currentPrice, margin, notional, unrealized, leverage }) {
  const u = Number(unrealized || 0);
  const m = Number(margin || 0);
  if (m > 0) return (u / m) * 100;
  const n = Number(notional || 0);
  if (n > 0) return (u / n) * 100;
  if (entryPrice > 0 && currentPrice > 0) {
    const s = String(side || '').toLowerCase();
    const move = s === 'short'
      ? (entryPrice - currentPrice) / entryPrice
      : (currentPrice - entryPrice) / entryPrice;
    return move * 100 * (Number(leverage || 0) > 0 ? Number(leverage) : 1);
  }
  return 0;
}

function unrealizedPnlCell({ unrealized, pctValue, isOpen, scope = 'margin' }) {
  const tone = cls(unrealized);
  const pctLabel = `${pctValue > 0 ? '+' : ''}${pctValue.toFixed(2)}%`;
  if (!isOpen) {
    return `<span class="pi-pnl-cell ${tone}"><span class="pi-pnl-value">${money(unrealized)}</span></span>`;
  }
  return `
    <span class="pi-pnl-cell ${tone}" title="Unrealized PnL (${scope} ROE)">
      <span class="pi-pnl-value">${money(unrealized)}</span>
      <span class="pi-pnl-pct ${tone}">${escapeHtml(pctLabel)}</span>
    </span>`;
}

function protectionDetails(t, entryPrice, side, isOpen, data) {
  if (!isOpen || !entryPrice) {
    return { profitProtection: '—', trailingStop: '—', stopLoss: '—', liquidation: '—', sort: {} };
  }
  const rules = exitRulesForTrade(t, data);
  const metadata = tradeMetadata(t);
  const ppActivation = toDecimal(rules.profitProtectionActivationPct ?? 0.0035);
  const trailActivation = toDecimal(rules.trailingActivationPct ?? 0.0035);
  const breakeven = toDecimal(rules.breakevenFloorPct ?? 0.0035);
  const trailStep = toDecimal(rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.0025);
  const trailArm = rules.trailArmPct != null
    ? toDecimal(rules.trailArmPct)
    : Math.max(trailActivation, breakeven + trailStep);
  const ppArmPrice = sideTargetPrice(entryPrice, side, ppActivation, true);
  const trailArmPrice = sideTargetPrice(entryPrice, side, trailArm, true);
  const rawProtectionState = String(metadata.profit_protection || '').toLowerCase();
  const protectionActive = Boolean(rawProtectionState && rawProtectionState !== 'inactive');
  const trailState = String(metadata.trail_stop || '').toLowerCase() === 'active' ? 'active' : 'waiting';
  const trigger = Number(metadata.trail_stop_trigger || t.trail_stop_trigger || 0);
  const stopLossEnabled = Boolean(rules.fixedStopLossEnabled);
  const stopLossDecimal = toDecimal(rules.stopLossPct ?? 0);
  const stopLossSuffix = rules.stopLossSource === 'atr'
    ? ' (ATR)'
    : rules.stopLossSource === 'coin_override'
      ? ' (coin)'
      : '';
  const stopLossPrice = stopLossEnabled ? sideTargetPrice(entryPrice, side, stopLossDecimal, false) : 0;
  const margin = Number(t.margin_used ?? t.marginUsed ?? 0);
  const notional = Number(t.notional_size ?? t.notionalSize ?? 0);
  const liquidationMove = notional > 0 ? Math.max(0, margin / notional) : 0;
  const estimatedLiquidation = liquidationMove > 0
    ? sideTargetPrice(entryPrice, side, liquidationMove, false)
    : 0;

  return {
    profitProtection: protectionStatusCell(
      protectionActive ? 'active' : 'inactive',
      protectionActive && trigger
        ? `Exit: ${priceMoney(trigger)}`
        : `Activates: ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)})`,
      protectionActive,
    ),
    trailingStop: protectionStatusCell(
      trailState === 'active' ? 'active' : 'inactive',
      trailState === 'active' && trigger
        ? `Exit: ${priceMoney(trigger)}`
        : `Activates: ${priceMoney(trailArmPrice)} (+${decimalPct(trailArm)})`,
      trailState === 'active',
    ),
    stopLoss: protectionStatusCell(
      stopLossEnabled ? 'enabled' : 'disabled',
      stopLossEnabled && stopLossPrice
        ? `Exit: ${priceMoney(stopLossPrice)} (-${decimalPct(stopLossDecimal)})${stopLossSuffix}`
        : 'Trailing-only config',
      stopLossEnabled,
    ),
    liquidation: protectionStatusCell(
      'estimate',
      estimatedLiquidation ? priceMoney(estimatedLiquidation) : '—',
      false,
    ),
    sort: {
      profitProtection: protectionActive ? 1 : 0,
      trailingStop: trailState === 'active' ? 1 : 0,
      stopLoss: stopLossEnabled ? stopLossPrice : -1,
      liquidation: estimatedLiquidation,
    },
  };
}

function normalizeTrade(t, data) {
  const status = String(t.status || '').toUpperCase();
  const isOpen = status === 'OPEN';
  const entryPrice = Number(t.entry_price ?? t.entryPrice ?? 0);
  const side = String(t.position_side ?? t.positionSide ?? '-').toLowerCase();
  const currentPrice = currentPriceForTrade(t, data);
  const margin = Number(t.margin_used ?? t.marginUsed ?? 0);
  let leverage = Number(t.leverage ?? t.leverageUsed ?? 0);
  const size = Number(t.position_size ?? t.positionSize ?? 0);
  let notional = Number(t.notional_size ?? t.notionalSize ?? 0);
  if (!notional && entryPrice > 0 && size > 0) notional = entryPrice * size;
  if (!notional && margin > 0 && leverage > 0) notional = margin * leverage;
  if (!leverage && margin > 0 && notional > 0) leverage = notional / margin;
  const time = isOpen ? (t.entry_time ?? t.entryTime) : (t.exit_time ?? t.exitTime ?? t.entry_time ?? t.entryTime);
  const protection = protectionDetails(t, entryPrice, side, isOpen, data);
  const unrealized = Number(t.unrealized_pnl ?? t.unrealizedPnl ?? 0);
  const unrealizedPct = unrealizedPctForTrade({
    side,
    entryPrice,
    currentPrice,
    margin,
    notional,
    unrealized,
    leverage,
  });

  return {
    ...t,
    _status: status,
    _isOpen: isOpen,
    _entryPrice: entryPrice,
    _exitPrice: Number(t.exit_price ?? t.exitPrice ?? 0),
    _currentPrice: currentPrice,
    _margin: margin,
    _leverage: leverage,
    _size: size,
    _notional: notional,
    _realized: Number(t.realized_pnl ?? t.realizedPnl ?? 0),
    _unrealized: unrealized,
    _unrealizedPct: unrealizedPct,
    _time: time,
    _timeValue: new Date(time || 0).getTime() || 0,
    _side: side,
    _strategy: String(t.source_strategy ?? t.sourceStrategy ?? ''),
    _exitReason: String(t.exit_reason ?? t.exitReason ?? ''),
    _protection: protection,
  };
}

function visibleColumns(tableKey, columns) {
  const stored = state.columns[tableKey] || {};
  return columns.filter((col) => stored[col.key] !== false);
}

function resetColumns(tableKey, columns) {
  state.columns[tableKey] = Object.fromEntries(columns.map((col) => [col.key, col.defaultVisible !== false]));
  saveStorage(STORAGE_KEYS.columns, state.columns);
}

function updateColumnVisibility(tableKey, key, checked) {
  if (!state.columns[tableKey]) {
    state.columns[tableKey] = {};
  }
  state.columns[tableKey][key] = checked;
  saveStorage(STORAGE_KEYS.columns, state.columns);
}

function compareValues(a, b, type) {
  if (type === 'number' || type === 'datetime' || type === 'boolean') {
    const na = Number(a ?? 0);
    const nb = Number(b ?? 0);
    return na === nb ? 0 : na > nb ? 1 : -1;
  }
  const sa = String(a ?? '').toLowerCase();
  const sb = String(b ?? '').toLowerCase();
  return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: 'base' });
}

function sortRows(rows, columns, tableKey) {
  const sort = state.sort[tableKey];
  if (!sort?.key) return rows;
  const column = columns.find((col) => col.key === sort.key);
  if (!column) return rows;
  const direction = sort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => compareValues(column.sortValue(a), column.sortValue(b), column.sortType) * direction);
}

function setSort(tableKey, key) {
  const current = state.sort[tableKey] || {};
  state.sort[tableKey] = {
    key,
    direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc',
  };
  saveStorage(STORAGE_KEYS.sort, state.sort);
}

function sortGlyph(tableKey, key) {
  const sort = state.sort[tableKey];
  if (sort?.key !== key) return '<span class="pi-sort-glyph">↕</span>';
  return `<span class="pi-sort-glyph active">${sort.direction === 'asc' ? '↑' : '↓'}</span>`;
}

function paginateRows(rows, tableKey) {
  const pager = state.pagination[tableKey];
  const pageSize = Number(pager.pageSize) || DEFAULT_PAGE_SIZE;
  const totalFiltered = rows.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize) || 1);
  if (pager.page > totalPages) pager.page = totalPages;
  if (pager.page < 1) pager.page = 1;
  const start = (pager.page - 1) * pageSize;
  return {
    allCount: totalFiltered,
    filteredCount: totalFiltered,
    totalPages,
    pageSize,
    page: pager.page,
    rows: rows.slice(start, start + pageSize),
  };
}

function resetPage(tableKey) {
  if (state.pagination[tableKey]) state.pagination[tableKey].page = 1;
}

function renderPagination(tableKey, meta, { filterNote = '' } = {}) {
  const info = document.getElementById(`${tableKey}-page-info`);
  const prev = document.getElementById(`${tableKey}-prev`);
  const next = document.getElementById(`${tableKey}-next`);
  const sizeSelect = document.getElementById(`${tableKey}-page-size`);
  if (sizeSelect && Number(sizeSelect.value) !== meta.pageSize) {
    sizeSelect.value = String(meta.pageSize);
  }
  if (!meta.filteredCount) {
    if (info) info.textContent = 'Showing 0 of 0';
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }
  const start = (meta.page - 1) * meta.pageSize + 1;
  const end = Math.min(meta.page * meta.pageSize, meta.filteredCount);
  if (info) {
    info.textContent = `Showing ${start}–${end} of ${meta.filteredCount}${filterNote} · Page ${meta.page} of ${meta.totalPages}`;
  }
  if (prev) prev.disabled = meta.page <= 1;
  if (next) next.disabled = meta.page >= meta.totalPages;
}

function paginationRowCount(tableKey, data) {
  if (tableKey === 'watchlist') {
    return watchlistRows(data, { paginate: false }).filteredCount;
  }
  if (tableKey === 'pnl') {
    const report = data.paperPnlReport || {};
    return ((report.breakdowns || {})[state.pnlTab] || []).length;
  }
  if (tableKey === 'adaptive') {
    const payload = data.adaptiveControl || {};
    const control = payload.control || {};
    const durableHistory = payload.decisionHistory || {};
    const decisions = Array.isArray(control.decisions) ? control.decisions : [];
    const history = Array.isArray(durableHistory.records)
      ? durableHistory.records
      : Array.isArray(control.history) ? control.history : [];
    return adaptiveAuditRows(decisions, history).length;
  }
  if (tableKey === 'sd') return auditTableRows(data.supplyDemandAudit).length;
  if (tableKey === 'dsma') return auditTableRows(data.dualSmaAudit).length;
  if (tableKey === 'ebp') return auditTableRows(data.ema50BreakoutPullbackAudit).length;
  if (tableKey === 'orb') return auditTableRows(data.orb5mScalpAudit).length;
  return tradesForFilter(data).length;
}

function bindPaginationControls(tableKey, rerender) {
  const pager = state.pagination[tableKey];
  const sizeSelect = document.getElementById(`${tableKey}-page-size`);
  if (sizeSelect) {
    sizeSelect.value = String(pager.pageSize);
    sizeSelect.addEventListener('change', () => {
      const next = Number(sizeSelect.value);
      pager.pageSize = PAGE_SIZES.includes(next) ? next : DEFAULT_PAGE_SIZE;
      saveStorage(STORAGE_KEYS[`${tableKey}PageSize`], pager.pageSize);
      resetPage(tableKey);
      rerender();
    });
  }
  document.getElementById(`${tableKey}-prev`)?.addEventListener('click', () => {
    if (pager.page > 1) {
      pager.page -= 1;
      rerender();
    }
  });
  document.getElementById(`${tableKey}-next`)?.addEventListener('click', () => {
    if (!state.payload) return;
    const pageSize = Number(pager.pageSize) || DEFAULT_PAGE_SIZE;
    const totalFiltered = paginationRowCount(tableKey, state.payload);
    const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize) || 1);
    if (pager.page < totalPages) {
      pager.page += 1;
      rerender();
    }
  });
}

function renderTableHead(headId, tableKey, columns) {
  const head = document.getElementById(headId);
  if (!head) return;
  const visible = visibleColumns(tableKey, columns);
  if (!visible.length) {
    head.innerHTML = '<tr><th>No columns selected</th></tr>';
    return;
  }
  head.innerHTML = `<tr>${visible.map((col) => `
    <th title="${escapeHtml(col.title || '')}">
      <button type="button" class="pi-sort-btn" data-sort-table="${tableKey}" data-sort-key="${col.key}">
        <span>${escapeHtml(col.label)}</span>${sortGlyph(tableKey, col.key)}
      </button>
    </th>`).join('')}</tr>`;
}

function renderColumnMenu(containerId, tableKey, columns) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!state.columns[tableKey]) resetColumns(tableKey, columns);
  const stored = state.columns[tableKey] || {};
  container.innerHTML = columns.map((col) => `
    <label class="pi-column-option">
      <input type="checkbox" data-column-table="${tableKey}" data-column-key="${col.key}" ${stored[col.key] !== false ? 'checked' : ''}>
      <span>${escapeHtml(col.label)}</span>
    </label>`).join('');
}

function renderNoColumns(body, colspan = 1) {
  body.innerHTML = `<tr><td colspan="${colspan}" class="pi-empty">No columns selected. Use Columns → Reset to restore the table.</td></tr>`;
}

function applyTheme(mode = state.themeMode) {
  state.themeMode = mode || 'system';
  saveStorage(STORAGE_KEYS.theme, state.themeMode);
  const root = document.documentElement;
  if (state.themeMode === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.dataset.theme = state.themeMode;
  }
  const label = state.themeMode === 'system' ? 'System' : state.themeMode === 'dark' ? 'Dark' : 'Light';
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.innerHTML = `<i class="fas fa-circle-half-stroke"></i><span>${label}</span>`;
    btn.title = `Theme: ${label}`;
  }
}

function nextThemeMode() {
  const modes = ['system', 'dark', 'light'];
  const index = modes.indexOf(state.themeMode);
  return modes[(index + 1) % modes.length];
}

function displayHlCoin(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const coin = raw.includes('/') ? raw.split('/')[0] : raw;
  return coin.includes(':') ? coin.split(':').pop() : coin;
}

function displayHlPair(row) {
  if (row.displayPair) return String(row.displayPair);
  const base = row.displayCoin || displayHlCoin(row.pair || row.coin || row.symbol);
  return base ? `${base}/USD-PERP` : '';
}

const watchlistColumns = [
  {
    key: 'coin',
    label: 'Coin',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.displayCoin || displayHlCoin(row.coin),
    render: (row) => {
      const key = watchlistRowKey(row);
      const expanded = state.watchlistExpandedCoins.has(key);
      const coin = escapeHtml(row.displayCoin || displayHlCoin(row.coin));
      return [
        `<button type="button" class="pi-cycle-expand" data-expand-coin="${escapeHtml(key)}" aria-expanded="${expanded}" aria-label="Toggle cycle analysis">${expanded ? '▼' : '▶'}</button>`,
        `<span class="pi-pair">${coin}</span>`,
      ].join('');
    },
  },
  {
    key: 'pair',
    label: 'Perp pair',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.displayPair || displayHlPair(row),
    render: (row) => escapeHtml(displayHlPair(row)),
  },
  {
    key: 'assetClass',
    label: 'Class',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.assetClass || 'crypto',
    render: (row) => {
      const klass = String(row.assetClass || 'crypto').toLowerCase();
      return `<span class="pi-tag ${klass === 'tradfi' ? 'tradfi' : 'watch'}">${escapeHtml(klass)}</span>`;
    },
  },
  {
    key: 'assetCategory',
    label: 'Category',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.assetCategory || row.assetClass || 'crypto',
    render: (row) => {
      const category = String(row.assetCategory || row.assetClass || 'crypto').toLowerCase();
      return `<span class="pi-tag ${category === 'crypto' ? 'watch' : 'tradfi'}">${escapeHtml(category.replace(/_/g, ' '))}</span>`;
    },
  },
  {
    key: 'price',
    label: 'HL price',
    title: 'Hyperliquid mid when available; otherwise last position mark or cached price',
    sortType: 'number',
    defaultVisible: true,
    sortValue: (row) => Number(effectiveWatchlistPrice(row).price || 0),
    render: (row) => watchlistPriceCell(row),
  },
  {
    key: 'move',
    label: 'Move',
    title: 'Price change since the previous dashboard refresh',
    sortType: 'number',
    defaultVisible: true,
    sortValue: (row) => {
      const { price } = effectiveWatchlistPrice(row);
      const baseline = Number(state.priceComparisonBaseline[watchlistPriceKey(row)] || 0);
      const movement = priceMovementMeta(price, baseline);
      return movement ? movement.deltaPct : 0;
    },
    render: (row) => watchlistMoveCell(row),
  },
  {
    key: 'regime',
    label: 'Regime',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => cycleAnalysisOf(row).regime || '',
    render: (row) => {
      const regime = cycleAnalysisOf(row).regime;
      return regime ? escapeHtml(String(regime)) : '—';
    },
  },
  {
    key: 'consensus',
    label: 'Consensus',
    title: 'Consensus signal · confidence · agreement %',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => {
      const c = cycleAnalysisOf(row).consensus || {};
      return `${c.signal || ''} ${c.confidence || 0}`;
    },
    render: (row) => escapeHtml(formatCycleConsensus(row)),
  },
  {
    key: 'bestSignal',
    label: 'Best signal',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => {
      const best = cycleAnalysisOf(row).bestSignal || {};
      return `${best.strategy || ''} ${best.side || ''} ${best.confidence || 0}`;
    },
    render: (row) => formatCycleBestSignal(row),
  },
  {
    key: 'entryDecision',
    label: 'Entry decision',
    title: 'Orchestrator outcome from the last perp entry cycle',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => {
      const d = cycleAnalysisOf(row).entryDecision || {};
      return `${d.outcome || ''} ${d.primaryReason || ''}`;
    },
    render: (row) => formatCycleEntryDecision(row, state.payload),
  },
  {
    key: 'status',
    label: 'Status',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.entryBlocked ? `blocked ${statusLabel(row.status)}` : statusLabel(row.status),
    render: (row) => {
      const sideLabel = sideBlockLabel(row);
      const blockLabel = sideLabel || entryBlockLabel(row);
      const rawBlockTitle = sideLabel ? sideBlockTitle(row) : row.entryBlockMessage;
      const decisionText = currentEntryDecisionText(row);
      const blockTitle = rawBlockTitle ? ` title="${escapeHtml(rawBlockTitle)}"` : '';
      const decisionTitle = decisionText ? ` title="${escapeHtml(decisionText)}"` : '';
      const membership = listMembershipHint(row);
      const membershipTitle = membership ? ` title="${escapeHtml(membership)}"` : '';
      return [
        `<span class="pi-tag ${statusClass(row.status)}">${escapeHtml(statusLabel(row.status))}</span>`,
        membership && row.listMembership !== 'active_selector'
          ? `<span class="pi-tag watch"${membershipTitle}>${escapeHtml(membership)}</span>` : '',
        blockLabel ? `<span class="pi-tag ${sideLabel ? 'side-risk' : 'risk'}"${blockTitle}>${escapeHtml(blockLabel)}</span>` : '',
        decisionText ? `<span class="pi-tag watch"${decisionTitle}>${escapeHtml(decisionText.length > 34 ? `${decisionText.slice(0, 34)}…` : decisionText)}</span>` : '',
      ].filter(Boolean).join(' ');
    },
  },
  {
    key: 'blockedSides',
    label: 'Blocked side',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => sideBlockLabel(row) || 'none',
    render: (row) => {
      const label = sideBlockLabel(row);
      if (!label) return '—';
      const title = sideBlockTitle(row);
      return `<span class="pi-tag side-risk"${title ? ` title="${escapeHtml(title)}"` : ''}>${escapeHtml(label)}</span>`;
    },
  },
  {
    key: 'open',
    label: 'Open',
    sortType: 'boolean',
    defaultVisible: true,
    sortValue: (row) => row.hasOpenPosition ? 1 : 0,
    render: (row) => row.hasOpenPosition ? 'Yes' : '—',
  },
  {
    key: 'side',
    label: 'Side',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.openSide || '',
    render: (row) => {
      const side = row.openSide ? String(row.openSide).toLowerCase() : '—';
      const sideClass = side === 'long' ? 'pi-side-long' : side === 'short' ? 'pi-side-short' : '';
      return side !== '—' ? `<span class="pi-tag ${sideClass}">${side}</span>` : '—';
    },
  },
  {
    key: 'unrealized',
    label: 'Unrealized',
    sortType: 'number',
    defaultVisible: true,
    sortValue: (row) => Number(row.unrealizedPnl || 0),
    render: (row) => {
      if (!row.hasOpenPosition) return '—';
      const pct = unrealizedPctForTrade({
        side: row.openSide,
        entryPrice: Number(row.entryPrice || 0),
        currentPrice: Number(row.midPrice || 0),
        margin: Number(row.marginUsed || 0),
        notional: Number(row.notional || 0),
        unrealized: Number(row.unrealizedPnl || 0),
        leverage: Number(row.leverage || 0),
      });
      return unrealizedPnlCell({
        unrealized: row.unrealizedPnl,
        pctValue: pct,
        isOpen: true,
      });
    },
  },
];

const tradeColumns = [
  { key: 'time', label: 'Time', sortType: 'datetime', defaultVisible: true, sortValue: (t) => t._timeValue, render: (t) => escapeHtml(formatTradeTime(t._time)) },
  { key: 'coin', label: 'Coin', sortType: 'text', defaultVisible: true, sortValue: (t) => displayHlCoin(t.coin || t.symbol || ''), render: (t) => `<span class="pi-pair">${escapeHtml(displayHlCoin(t.coin || t.symbol || '') || '-')}</span>` },
  { key: 'side', label: 'Side', sortType: 'text', defaultVisible: true, sortValue: (t) => t._side, render: (t) => `<span class="pi-tag ${t._side === 'long' ? 'pi-side-long' : t._side === 'short' ? 'pi-side-short' : ''}">${escapeHtml(t._side)}</span>` },
  { key: 'status', label: 'Status', sortType: 'text', defaultVisible: true, sortValue: (t) => t._status, render: (t) => `<span class="pi-signal">${escapeHtml(t._status || '-')}</span>` },
  { key: 'entry', label: 'Entry', sortType: 'number', defaultVisible: true, sortValue: (t) => t._entryPrice, render: (t) => priceMoney(t._entryPrice) },
  { key: 'exit', label: 'Exit', sortType: 'number', defaultVisible: true, sortValue: (t) => t._exitPrice, render: (t) => t._isOpen ? '—' : priceMoney(t._exitPrice) },
  { key: 'current', label: 'Current', title: 'Latest Hyperliquid mark/mid price used for paper PnL validation', sortType: 'number', defaultVisible: true, sortValue: (t) => t._currentPrice, render: (t) => currentPriceCell(t, t._currentPrice, t._entryPrice, t._side) },
  { key: 'size', label: 'Size', sortType: 'number', defaultVisible: true, sortValue: (t) => t._size, render: (t) => num(t._size, 6) },
  { key: 'margin', label: 'Margin', sortType: 'number', defaultVisible: true, sortValue: (t) => t._margin, render: (t) => money(t._margin) },
  { key: 'leverage', label: 'Leverage', sortType: 'number', defaultVisible: true, sortValue: (t) => t._leverage, render: (t) => t._leverage ? `${num(t._leverage, 1)}x` : '—' },
  { key: 'notional', label: 'Notional exposure', sortType: 'number', defaultVisible: true, sortValue: (t) => t._notional, render: (t) => money(t._notional) },
  { key: 'realized', label: 'Realized', sortType: 'number', defaultVisible: true, sortValue: (t) => t._realized, render: (t) => `<span class="${cls(t._realized)}">${money(t._realized)}</span>` },
  { key: 'unrealized', label: 'Unrealized', sortType: 'number', defaultVisible: true, sortValue: (t) => t._unrealized, render: (t) => unrealizedPnlCell({ unrealized: t._unrealized, pctValue: t._unrealizedPct, isOpen: t._isOpen }) },
  { key: 'profitProtection', label: 'Profit protection', sortType: 'number', defaultVisible: true, sortValue: (t) => t._protection.sort.profitProtection || 0, render: (t) => t._protection.profitProtection },
  { key: 'trailingStop', label: 'Trailing stop', sortType: 'number', defaultVisible: true, sortValue: (t) => t._protection.sort.trailingStop || 0, render: (t) => t._protection.trailingStop },
  { key: 'stopLoss', label: 'Stop loss', sortType: 'number', defaultVisible: true, sortValue: (t) => t._protection.sort.stopLoss || 0, render: (t) => t._protection.stopLoss },
  { key: 'liquidation', label: 'Liq est.', sortType: 'number', defaultVisible: true, sortValue: (t) => t._protection.sort.liquidation || 0, render: (t) => t._protection.liquidation },
  { key: 'strategy', label: 'Strategy', sortType: 'text', defaultVisible: true, sortValue: (t) => t._strategy, render: (t) => escapeHtml(t._strategy || '-') },
  { key: 'exitReason', label: 'Exit reason', sortType: 'text', defaultVisible: true, sortValue: (t) => t._exitReason, render: (t) => `<span class="pi-muted-cell">${escapeHtml(t._exitReason || (t._isOpen ? '' : '-'))}</span>` },
];

function renderHyperliquidTopbar(data) {
  const hl = data.hyperliquid || {};
  const cfg = hl.config || {};
  const health = {};
  (data.exchangeStatus || []).forEach((s) => { health[String(s.exchange || '').toLowerCase()] = s.status || ''; });

  const modeEl = document.getElementById('hl-mode-pill');
  if (modeEl) {
    modeEl.textContent = `HL: ${String(cfg.mode || 'paper').toUpperCase()}`;
    modeEl.classList.add('pi-pill-hl');
  }
  const engineEl = document.getElementById('hl-engine-pill');
  if (engineEl) {
    engineEl.textContent = cfg.enabled ? 'Engine: ON' : 'Engine: OFF';
    engineEl.classList.toggle('ok', cfg.enabled);
    engineEl.classList.toggle('bad', !cfg.enabled);
  }
  const orchEl = document.getElementById('hl-orchestrator-pill');
  if (orchEl) {
    const st = health.orchestrator || 'unknown';
    orchEl.textContent = `Orchestrator: ${st.includes('healthy') ? 'OK' : st}`;
    orchEl.classList.toggle('ok', st.includes('healthy'));
    orchEl.classList.toggle('bad', st.includes('unreachable'));
  }
}

function renderHero(data) {
  const p = data.portfolio || {};
  const summary = (data.paperPerps || {}).summary || {};
  const source = String(p.balanceSource || summary.balance_source || 'paper_derived');
  const settled = Number(
    p.settledBalance ?? p.equity ?? summary.equity ?? summary.balance ?? 0,
  );
  const starting = Number(p.startingBalance ?? summary.starting_balance ?? 0);
  const unrealized = Number(p.unrealizedPnl ?? summary.unrealized_pnl ?? 0);
  const markToMarket = Number(
    p.markToMarketEquity ?? summary.mark_to_market_equity ?? settled + unrealized,
  );
  setText('hl-balance-source', source.replace(/_/g, ' '));
  setText('hero-equity', money(settled));
  if (starting > 0) {
    setText('hero-balance-journey', `${money(starting)} → ${money(settled)}`);
  } else {
    setText('hero-balance-journey', '—');
  }
  setText('hero-mark-to-market', money(markToMarket), unrealized);
  setText('hero-available', `Available: ${money(p.availableBalance ?? summary.available_balance)}`);
  setText('hero-margin-used', money(p.marginUsed ?? summary.margin_used ?? p.openMargin));
  setText('hero-open-notional', money(p.openNotional ?? summary.open_notional ?? summary.openNotional));
  setText('perp-open-longs', String(p.openLongs ?? 0));
  setText('perp-open-shorts', String(p.openShorts ?? 0));
  setText('perp-open-margin', money(p.openMargin ?? summary.open_margin));
  setText('perp-realized-pnl', money(p.realizedPnl ?? summary.realized_pnl), p.realizedPnl ?? summary.realized_pnl);
  setText('perp-unrealized-pnl', money(p.unrealizedPnl ?? summary.unrealized_pnl), p.unrealizedPnl ?? summary.unrealized_pnl);
  setText('perp-win-rate', pct(p.winRate ?? summary.win_rate ?? 0));

  const trading = data.tradingStatus || {};
  setText('trading-status', `Bot: ${trading.status || trading.trading_status || 'unknown'}`);
  setText('updated-at', `Updated ${new Date(data.timestamp).toLocaleTimeString()}`);
  renderCycleStatus(data);
}

function renderEntryRulesHint(data) {
  const rules = ((data.hyperliquid || {}).config || {}).entryRules || {};
  const el = document.getElementById('entry-rules-hint');
  if (!el) return;
  const stopText = rules.fixedStopLossEnabled ? `SL ${rules.stopLossPct}%` : 'SL off';
  const feeFloor = rules.effectiveProfitFloorPct ?? ((rules.feeRatePerSide || 0.001) * 2 + (rules.profitProtectionFeeBuffer || 0.0015));
  const trailActivation = toDecimal(rules.trailingActivationPct ?? 0.0050);
  const trailStep = toDecimal(rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.0015);
  const trailFloor = toDecimal(rules.breakevenFloorPct ?? feeFloor ?? 0.0035);
  const trailArm = rules.trailArmPct != null
    ? toDecimal(rules.trailArmPct)
    : Math.max(trailActivation, trailFloor + trailStep);
  el.textContent = [
    `Min conf long ${pct((rules.minConfidenceLong || 0) * (rules.minConfidenceLong <= 1 ? 100 : 1))}`,
    `short ${pct((rules.minConfidenceShort || 0) * (rules.minConfidenceShort <= 1 ? 100 : 1))}`,
    `max ${rules.maxOpenPositions || 0} open`,
    `margin ≤ ${money(rules.maxMarginPerTrade)}`,
    `notional ≤ ${money(rules.maxNotionalPerTrade)}`,
    `lev ${num(rules.defaultLeverage || 0, 1)}x`,
    `${stopText} / TP ${rules.takeProfitPct}%`,
    `PP arm ${decimalPct(rules.profitProtectionActivationPct ?? 0.0035)}`,
    `PP floor ${decimalPct(feeFloor)}`,
    `trail arm ${decimalPct(trailArm)}`,
  ].join(' · ');
}

function watchlistRowStatusKey(row) {
  return String(row?.status || 'under_analysis').toLowerCase();
}

function watchlistMatchesStatusFilter(row) {
  const filter = String(state.watchlistStatusFilter || 'all').toLowerCase();
  if (filter === 'all') return true;
  return watchlistRowStatusKey(row) === filter;
}

function watchlistRows(data, { paginate = true } = {}) {
  const all = ((data.hyperliquid || {}).watchlist || []).slice();
  let rows = all;
  if (state.watchlistActiveOnly) {
    rows = rows.filter((r) => {
      const s = watchlistRowStatusKey(r);
      const m = String(r.listMembership || '').toLowerCase();
      if (s === 'open' || s === 'blocked') return true;
      if (s === 'under_analysis' && m === 'active_selector') return true;
      return false;
    });
  }
  rows = rows.filter(watchlistMatchesStatusFilter);
  const sorted = sortRows(rows, watchlistColumns, 'watchlist');
  const totalFiltered = sorted.length;
  const pager = state.pagination.watchlist;
  const pageSize = Number(pager.pageSize) || DEFAULT_PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize) || 1);
  if (pager.page > totalPages) pager.page = totalPages;
  if (pager.page < 1) pager.page = 1;
  const start = paginate ? (pager.page - 1) * pageSize : 0;
  const end = paginate ? start + pageSize : sorted.length;
  return {
    allCount: all.length,
    filteredCount: totalFiltered,
    totalPages,
    pageSize,
    page: pager.page,
    rows: sorted.slice(start, end),
  };
}

function renderHyperliquidGlobalBlock(data) {
  const el = document.getElementById('hl-global-block-banner');
  if (!el) return;
  const block = (data.hyperliquid || {}).globalEntryBlock || {};
  if (!block.entryBlocked) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  el.hidden = false;
  const reason = entryBlockLabel({ entryBlocked: true, entryBlockReason: block.entryBlockReason });
  const msg = block.entryBlockMessage ? ` — ${block.entryBlockMessage}` : '';
  el.textContent = `Global entry halt: ${reason || 'all new entries blocked'}${msg}. All liquid perps are shown below with block status.`;
}

function renderHyperliquidWatchlist(data) {
  renderHyperliquidGlobalBlock(data);
  const body = document.getElementById('hl-watchlist-body');
  const countEl = document.getElementById('watchlist-count');
  const statusFilter = document.getElementById('watchlist-status-filter');
  if (statusFilter && statusFilter.value !== state.watchlistStatusFilter) {
    statusFilter.value = state.watchlistStatusFilter;
  }
  const meta = watchlistRows(data);
  const { rows } = meta;
  const visible = visibleColumns('watchlist', watchlistColumns);
  renderTableHead('hl-watchlist-head', 'watchlist', watchlistColumns);
  renderColumnMenu('watchlist-column-options', 'watchlist', watchlistColumns);
  const filterNote = state.watchlistStatusFilter !== 'all' || state.watchlistActiveOnly
    ? ` (filtered from ${meta.allCount})`
    : '';
  renderPagination('watchlist', meta, { filterNote });

  if (countEl) {
    countEl.textContent = `${meta.filteredCount} coins`;
  }
  if (body) {
    if (!visible.length) {
      renderNoColumns(body);
    } else if (!meta.filteredCount) {
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">No pairs match the current filters.</td></tr>`;
    } else if (!rows.length) {
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">No pairs on this page.</td></tr>`;
    } else {
      body.innerHTML = rows.flatMap((row) => {
        const mainRow = `<tr class="pi-watchlist-row" data-coin="${escapeHtml(watchlistRowKey(row))}">${visible.map((col) => `<td data-label="${escapeHtml(col.label)}">${col.render(row)}</td>`).join('')}</tr>`;
        const key = watchlistRowKey(row);
        if (!state.watchlistExpandedCoins.has(key)) return [mainRow];
        return [mainRow, renderCycleDetailRow(row, visible.length)];
      }).join('');
    }
  }
  rememberWatchlistPrices(data);
}

function tradesForFilter(data) {
  const pt = data.perpTrades || {};
  const open = pt.open || [];
  const closed = pt.closed || [];
  const trades = state.tradeFilter === 'open'
    ? open
    : state.tradeFilter === 'closed'
      ? closed
      : [...open, ...closed];
  const normalized = trades.map((t) => normalizeTrade(t, data));
  return sortRows(normalized, tradeColumns, 'trades');
}

function renderPerpTrades(data) {
  const pt = data.perpTrades || {};
  const summary = (data.paperPerps || {}).summary || {};
  setText('perp-trades-meta', `${pt.totalOpen ?? 0} open · ${pt.totalClosed ?? summary.closed_trades ?? 0} closed`);

  document.querySelectorAll('[data-trade-filter]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tradeFilter === state.tradeFilter);
  });
  renderTableHead('perp-paper-trades-head', 'trades', tradeColumns);
  renderColumnMenu('trade-column-options', 'trades', tradeColumns);

  const body = document.getElementById('perp-paper-trades-body');
  const allTrades = tradesForFilter(data);
  const meta = paginateRows(allTrades, 'trades');
  const trades = meta.rows;
  const visible = visibleColumns('trades', tradeColumns);
  if (body) {
    if (!visible.length) {
      renderNoColumns(body);
    } else if (!allTrades.length) {
      const msg = state.tradeFilter === 'open'
        ? 'No open paper perpetual positions.'
        : state.tradeFilter === 'closed'
          ? 'No closed paper perpetual trades yet.'
          : 'No paper perpetual trades recorded.';
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">${msg}</td></tr>`;
    } else if (!trades.length) {
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">No trades on this page.</td></tr>`;
    } else {
      body.innerHTML = trades.map((t) => `
        <tr>${visible.map((col) => `<td data-label="${escapeHtml(col.label)}">${col.render(t)}</td>`).join('')}</tr>`).join('');
    }
  }
  renderPagination('trades', meta);
  allTrades.forEach((t) => {
    const key = tradePriceKey(t);
    if (key && t._currentPrice > 0) state.lastPrices[key] = t._currentPrice;
  });
}

function fmtProfitFactor(pf) {
  if (pf === null || pf === undefined || Number.isNaN(Number(pf))) return 'n/a';
  if (String(pf).toLowerCase() === 'inf') return '∞';
  return Number(pf).toFixed(2);
}

function formatReportWindow(start, end) {
  if (!start || !end) return '—';
  const fmt = (iso) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };
  return `${fmt(start)} → ${fmt(end)}`;
}

function renderPaperPnlReport(data) {
  const report = data.paperPnlReport || {};
  const agg = report.aggregate || {};
  const body = document.getElementById('pnl-breakdown-body');

  setText('pnl-report-window', formatReportWindow(report.windowStart, report.windowEnd));
  setText('pnl-closed', String(agg.closed ?? 0));
  setText('pnl-open', String(agg.open ?? 0));
  setText('pnl-realized', money(agg.realized), agg.realized);
  setText('pnl-gross-before-fees', money(agg.grossBeforeFees), agg.grossBeforeFees);
  setText('pnl-fees', money(agg.fees));
  setText('pnl-fee-drag', agg.feeDragPct === null || agg.feeDragPct === undefined
    ? 'n/a'
    : pct(agg.feeDragPct));
  setText('pnl-funding', money(agg.funding), agg.funding);
  setText('pnl-win-rate', pct(agg.winRate));
  setText('pnl-avg-win', money(agg.avgWin), agg.avgWin);
  setText('pnl-avg-loss', money(agg.avgLoss), agg.avgLoss);
  setText('pnl-pf', fmtProfitFactor(agg.profitFactor));
  setText('pnl-win-hold', `${num(agg.avgWinHoldMin ?? 0, 1)} min`);
  setText('pnl-loss-hold', `${num(agg.avgLossHoldMin ?? 0, 1)} min`);

  document.querySelectorAll('[data-pnl-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.pnlTab === state.pnlTab);
  });

  const breakdowns = report.breakdowns || {};
  const allRows = breakdowns[state.pnlTab] || [];
  const meta = paginateRows(allRows, 'pnl');
  const rows = meta.rows;
  if (!body) return;
  if (!report.tradeCount) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No paper trades in the last 24 hours.</td></tr>';
    renderPagination('pnl', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No rows for this breakdown.</td></tr>';
    renderPagination('pnl', meta);
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No rows on this page.</td></tr>';
    renderPagination('pnl', meta);
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td data-label="Label">${escapeHtml(row.label)}</td>
      <td data-label="Closed">${row.nClosed ?? 0}</td>
      <td data-label="Open">${row.nOpen ?? 0}</td>
      <td data-label="PnL"><span class="${cls(row.pnl)}">${money(row.pnl)}</span></td>
      <td data-label="Avg"><span class="${cls(row.avg)}">${money(row.avg)}</span></td>
      <td data-label="WR%">${pct(row.winRate)}</td>
      <td data-label="PF">${escapeHtml(fmtProfitFactor(row.profitFactor))}</td>
    </tr>`).join('');
  renderPagination('pnl', meta);
}

function formatConfigValue(value) {
  if (value === null || value === undefined || value === '') return 'n/a';
  if (typeof value === 'number') return Number(value).toFixed(Math.abs(value) >= 10 ? 2 : 4).replace(/\.?0+$/, '');
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none';
  return Object.entries(value)
    .map(([key, val]) => `${key}: ${Array.isArray(val) ? val.join(', ') : typeof val === 'object' && val !== null ? JSON.stringify(val) : val}`)
    .join(' · ');
}

function formatAdaptiveTime(value) {
  if (!value) return 'N/A';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return `${d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} (${formatRelativeTime(value)})`;
}

function adaptiveEvidenceText(decision) {
  const ev = decision.evidence || {};
  const parts = [];
  if (ev.closed !== undefined) parts.push(`${ev.closed} closed`);
  if (ev.sampleClosed !== undefined) parts.push(`${ev.sampleClosed} sample`);
  if (ev.realized !== undefined && ev.realized !== null) parts.push(`net ${money(ev.realized)}`);
  if (ev.grossBeforeFees !== undefined && ev.grossBeforeFees !== null) parts.push(`gross ${money(ev.grossBeforeFees)}`);
  if (ev.fees !== undefined && ev.fees !== null) parts.push(`fees ${money(ev.fees)}`);
  if (ev.feeDragPct !== undefined && ev.feeDragPct !== null) parts.push(`fee drag ${pct(ev.feeDragPct)}`);
  if (ev.profitFactor !== undefined && ev.profitFactor !== null) parts.push(`PF ${fmtProfitFactor(ev.profitFactor)}`);
  if (ev.stopLossLoss !== undefined) parts.push(`SL loss ${money(ev.stopLossLoss)}`);
  if (ev.fastFailLoss !== undefined) parts.push(`fast-fail loss ${money(ev.fastFailLoss)}`);
  if (ev.trailingGain !== undefined) parts.push(`trailing gain ${money(ev.trailingGain)}`);
  return parts.join(' · ') || 'Evidence unavailable';
}

function adaptiveOutcomeText(record) {
  const out = record.currentOutcome || {};
  if (!out.closed) return 'No closed trades since change';
  const parts = [
    `${out.closed} closed`,
    `net ${money(out.realized)}`,
    `gross ${money(out.grossBeforeFees)}`,
    `fees ${money(out.fees)}`,
  ];
  if (out.winRatePct !== null && out.winRatePct !== undefined) parts.push(`WR ${pct(out.winRatePct)}`);
  if (out.profitFactor !== null && out.profitFactor !== undefined) parts.push(`PF ${fmtProfitFactor(out.profitFactor)}`);
  return parts.join(' · ');
}

function adaptiveActionLabel(record) {
  const action = String(record.action || record.type || '').replace(/_/g, ' ');
  const status = String(record.status || 'active').toLowerCase();
  const clsName = status === 'released' ? 'muted' : action.includes('block') ? 'risk' : action.includes('scale') ? 'ok' : 'watch';
  return `<span class="pi-tag ${clsName}">${escapeHtml(status)} · ${escapeHtml(action)}</span>`;
}

function adaptiveApplyStatus(record) {
  const raw = String(record.applyStatus || record.apply_status || (record.reloadRequired ? 'pending_reload' : 'applied_live')).toLowerCase();
  if (raw.includes('fail')) return 'apply failed';
  if (raw.includes('cycle')) return 'pending cycle';
  if (raw.includes('pending')) return 'pending reload';
  if (raw.includes('applied') || raw.includes('live')) return 'applied live';
  return raw.replace(/_/g, ' ') || 'pending reload';
}

function adaptiveAuditRows(decisions, history) {
  const rows = [];
  const seen = new Set();
  [...(decisions || []), ...(history || [])].forEach((record) => {
    const key = String(record.decisionKey || record.key || `${record.type || record.action}:${record.target || record.strategy}:${record.side || ''}:${record.liveSince || record.firstSeenAt || ''}`);
    if (!key || seen.has(key)) return;
    seen.add(key);
    rows.push(record);
  });
  return rows;
}

function auditTableRows(auditPayload) {
  const audit = auditPayload || {};
  const summary = audit.summary || {};
  return summary.recent || audit.rows || [];
}

function renderArcAudit(data) {
  const audit = data.arcAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('arc-area-rate', `${num(rates.area || 0, 1)}%`);
  setText('arc-range-rate', `${num(rates.range || 0, 1)}%`);
  setText('arc-candle-rate', `${num(rates.candle || 0, 1)}%`);
  setText('arc-eval-count', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('arc-audit-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Zones: ${JSON.stringify(summary.byZone || {})}.`
      : 'No ARC evaluations in the last 24 hours.';
  }
  const body = document.getElementById('arc-audit-body');
  if (!body) return;
  const rows = auditTableRows(audit).slice(0, 50);
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="pi-empty">No evaluations loaded.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => {
    const movePct = num((row.range_pct_move || 0) * 100, 1);
    const reason = strategyExecutionReason(
      row,
      row.entry_reason || row.candle_reason || row.range_reason || row.area_reason || ''
    );
    return `<tr>
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.zone || '')}</td>
      <td>${escapeHtml(row.signal || 'hold')}</td>
      <td>${movePct}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
}

function renderDualSmaAudit(data) {
  const audit = data.dualSmaAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('dsma-daily-rate', `${num(rates.daily || 0, 1)}%`);
  setText('dsma-15m-rate', `${num(rates.confirm15m || 0, 1)}%`);
  setText('dsma-5m-rate', `${num(rates.entry5m || 0, 1)}%`);
  setText('dsma-eval-count', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('dsma-audit-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Bias mix: ${JSON.stringify(summary.byDailyBias || {})}.`
      : 'No dual-SMA evaluations in the last 24 hours.';
  }
  const body = document.getElementById('dsma-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'dsma');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('dsma', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('dsma', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const ext = num((row.extension_distance_pct || 0) * 100, 2);
    const reason = strategyExecutionReason(
      row,
      row.entry_reason || row.entry_5m_reason || row.confirm_15m_reason || row.daily_reason || ''
    );
    return `<tr>
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.daily_bias || '')}</td>
      <td>${escapeHtml(row.trend_15m || '')}</td>
      <td>${escapeHtml(row.entry_signal_5m || row.signal || 'hold')}</td>
      <td>${ext}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
  renderPagination('dsma', meta);
}

function renderSupplyDemandAudit(data) {
  const audit = data.supplyDemandAudit || {};
  const summary = audit.summary || {};
  const rates = summary.stepPassRates || {};
  setText('sd-step1-rate', `${num(rates.step1 || 0, 1)}%`);
  setText('sd-step2-rate', `${num(rates.step2 || 0, 1)}%`);
  setText('sd-step3-rate', `${num(rates.step3 || 0, 1)}%`);
  setText('sd-eval-count', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('sd-audit-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Trend mix: ${JSON.stringify(summary.byTrendDirection || {})}.`
      : 'No supply/demand evaluations in the last 24 hours.';
  }
  const body = document.getElementById('sd-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'sd');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="6" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('sd', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('sd', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const steps = [
      row.step1_pass ? '1✓' : '1✗',
      row.step2_pass ? '2✓' : '2✗',
      row.step3_pass ? '3✓' : '3✗',
    ].join(' ');
    const reason = strategyExecutionReason(
      row,
      row.entry_reason || row.step3_reason || row.step2_reason || row.step1_reason || ''
    );
    return `<tr>
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.trend_direction || '')}</td>
      <td>${escapeHtml(row.signal || 'hold')}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td>${escapeHtml(steps)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
  renderPagination('sd', meta);
}

function renderOrb5mScalpAudit(data) {
  const audit = data.orb5mScalpAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('orb-breakout-rate', `${num(rates.breakout || 0, 1)}%`);
  setText('orb-retest-rate', `${num(rates.retest || 0, 1)}%`);
  setText('orb-rr-rate', String(summary.signalRewardRisk || 0));
  setText('orb-eval-count', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('orb-audit-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. States: ${JSON.stringify(summary.setupStateCounts || {})}.`
      : 'No ORB 5m scalp evaluations in the last 24 hours.';
  }
  const body = document.getElementById('orb-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'orb');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('orb', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('orb', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const reason = strategyExecutionReason(
      row,
      row.entry_reason || row.retest_reason || row.breakout_reason || row.rejection_reason || ''
    );
    return `<tr>
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.setup_state || '')}</td>
      <td>${escapeHtml(row.direction || '')}</td>
      <td>${escapeHtml(row.signal || 'hold')}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td>${num(row.or_mid || 0, 4)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
  renderPagination('orb', meta);
}

function renderEma50BreakoutPullbackAudit(data) {
  const audit = data.ema50BreakoutPullbackAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('ebp-breakout-rate', `${num(rates.breakout || 0, 1)}%`);
  setText('ebp-pullback-rate', `${num(rates.pullback || 0, 1)}%`);
  setText('ebp-trigger-rate', `${num(rates.trigger || 0, 1)}%`);
  setText('ebp-eval-count', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('ebp-audit-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. States: ${JSON.stringify(summary.setupStateCounts || {})}.`
      : 'No EMA50 breakout-pullback evaluations in the last 24 hours.';
  }
  const body = document.getElementById('ebp-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'ebp');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('ebp', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('ebp', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const reason = strategyExecutionReason(
      row,
      row.entry_reason || row.trigger_reason || row.pullback_reason || row.breakout_reason || ''
    );
    const entryBlock = ema50EntryBlockLabel(row);
    const blockClass = entryBlock.className ? ` class="pi-status-${entryBlock.className}"` : '';
    const blockTitle = entryBlock.title ? ` title="${escapeHtml(entryBlock.title)}"` : '';
    return `<tr>
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.setup_state || '')}</td>
      <td>${escapeHtml(row.direction || '')}</td>
      <td>${escapeHtml(row.signal || 'hold')}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td${blockClass}${blockTitle}>${escapeHtml(entryBlock.label)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
  renderPagination('ebp', meta);
}

function renderAdaptiveControl(data) {
  const payload = data.adaptiveControl || {};
  const control = payload.control || {};
  const durableHistory = payload.decisionHistory || {};
  const decisions = Array.isArray(control.decisions) ? control.decisions : [];
  const history = Array.isArray(durableHistory.records)
    ? durableHistory.records
    : Array.isArray(control.history) ? control.history : [];
  const rows = adaptiveAuditRows(decisions, history);
  const meta = paginateRows(rows, 'adaptive');
  const pageRows = meta.rows;
  const body = document.getElementById('adaptive-decisions-body');

  setText('adaptive-status', String(control.status || (control.enabled ? 'active' : 'disabled')).replace(/_/g, ' '));
  setText('adaptive-sample', String(control.sampleClosed ?? 0));
  setText('adaptive-lookback', `${num(control.lookbackHours ?? 0, 0)}h`);
  setText('adaptive-active-count', String(decisions.length));
  setText('adaptive-evaluated-at', control.lastEvaluatedAt ? `Evaluated ${formatRelativeTime(control.lastEvaluatedAt)}` : 'Not evaluated');

  const summary = document.getElementById('adaptive-summary');
  if (summary) {
    if (control.error) {
      summary.textContent = `Adaptive control error: ${control.error}`;
    } else if (decisions.length) {
      const source = durableHistory.source === 'postgres' ? 'Postgres audit history' : 'runtime audit history';
      summary.textContent = `${decisions.length} live adaptive change(s) are active. History source: ${source}. Each change is rebuilt every paper-perp cycle and is released automatically when rolling evidence improves.`;
    } else if (history.length) {
      summary.textContent = 'No live adaptive changes are active now. Recent released changes remain below for audit and future tuning.';
    } else {
      summary.textContent = payload.note || 'No adaptive decisions have been recorded yet.';
    }
  }

  const reloadStatus = document.getElementById('adaptive-reload-status');
  if (reloadStatus) {
    const statuses = rows.map(adaptiveApplyStatus);
    const hasFailed = statuses.includes('apply failed');
    const hasPending = statuses.includes('pending reload');
    const hasPendingCycle = statuses.includes('pending cycle');
    const status = hasFailed ? 'apply failed' : hasPending ? 'pending reload' : hasPendingCycle ? 'pending cycle' : rows.length ? 'applied live' : 'pending reload';
    const loadedAt = payload.configLoadedAt ? ` · orchestrator config loaded ${formatAdaptiveTime(payload.configLoadedAt)}` : '';
    reloadStatus.textContent = `Reload status: ${status}${loadedAt}`;
  }

  if (!body) return;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="pi-empty">No adaptive changes recorded yet.</td></tr>';
    renderPagination('adaptive', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!pageRows.length) {
    body.innerHTML = '<tr><td colspan="6" class="pi-empty">No adaptive changes on this page.</td></tr>';
    renderPagination('adaptive', meta);
    return;
  }

  body.innerHTML = pageRows.map((record) => {
    const target = [record.target, record.side].filter(Boolean).join(' ');
    const configPath = record.configPath || 'runtime overlay';
    const oldValue = formatConfigValue(record.oldValue);
    const newValue = formatConfigValue(record.newValue);
    const released = record.status === 'released' && record.releasedAt
      ? `<div class="pi-adaptive-release">Released ${escapeHtml(formatAdaptiveTime(record.releasedAt))}</div>`
      : '';
    const releaseReason = record.releaseReason
      ? `<div class="pi-muted-cell">Release: ${escapeHtml(record.releaseReason)}</div>`
      : '';
    const intendedEffect = record.intendedEffect
      ? `<div class="pi-muted-cell">Effect: ${escapeHtml(record.intendedEffect)}</div>`
      : '';
    const applyStatus = adaptiveApplyStatus(record);
    return `
      <tr>
        <td data-label="Status">${adaptiveActionLabel(record)}</td>
        <td data-label="Why">
          <div class="pi-adaptive-why">${escapeHtml(record.situation || 'Rolling evidence triggered an adaptive control.')}</div>
          <div class="pi-muted-cell">${escapeHtml(target || record.key || record.strategy || '')}</div>
          ${intendedEffect}
          ${releaseReason}
        </td>
        <td data-label="Config change">
          <div class="pi-code-pill">${escapeHtml(configPath)}</div>
          <div class="pi-adaptive-change">${escapeHtml(oldValue)} → <strong>${escapeHtml(newValue)}</strong></div>
          <div class="pi-muted-cell">Apply: ${escapeHtml(applyStatus)}</div>
        </td>
        <td data-label="Live since">
          ${escapeHtml(formatAdaptiveTime(record.liveSince || record.firstSeenAt))}
          ${released}
        </td>
        <td data-label="Evidence">${escapeHtml(adaptiveEvidenceText(record))}</td>
        <td data-label="Outcome since change">
          <span class="${cls((record.currentOutcome || {}).realized)}">${escapeHtml(adaptiveOutcomeText(record))}</span>
        </td>
      </tr>`;
  }).join('');
  renderPagination('adaptive', meta);
}

function downloadAdaptiveSnapshot() {
  const data = state.payload || {};
  const snapshot = {
    exportedAt: new Date().toISOString(),
    adaptiveControl: data.adaptiveControl || {},
    paperPnlReport: data.paperPnlReport || {},
    portfolio: data.portfolio || {},
  };
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, 'Z');
  a.href = url;
  a.download = `adaptive_pnl_control_${stamp}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function renderSystemStrip(data) {
  const strip = document.getElementById('system-strip');
  if (!strip) return;
  strip.innerHTML = (data.exchangeStatus || [])
    .map((s) => `<span class="pi-health-pill">${escapeHtml(s.exchange)}: ${escapeHtml(s.status)}</span>`)
    .join('');
}

async function loadDashboard() {
  const response = await fetch('/api/v1/dashboard/portfolio-intelligence', { cache: 'no-store' });
  if (!response.ok) throw new Error('Dashboard payload failed');
  const data = await response.json();
  if (data.timestamp !== state.lastSnapshotTimestamp) {
    state.priceComparisonBaseline = { ...state.lastPrices };
    state.lastSnapshotTimestamp = data.timestamp;
  }
  state.payload = data;
  safeRender('topbar', renderHyperliquidTopbar, data);
  safeRender('hero', renderHero, data);
  safeRender('entryRules', renderEntryRulesHint, data);
  safeRender('pnlReport', renderPaperPnlReport, data);
  safeRender('adaptiveControl', renderAdaptiveControl, data);
  safeRender('supplyDemandAudit', renderSupplyDemandAudit, data);
  safeRender('arcAudit', renderArcAudit, data);
  safeRender('dualSmaAudit', renderDualSmaAudit, data);
  safeRender('ema50BreakoutPullbackAudit', renderEma50BreakoutPullbackAudit, data);
  safeRender('orb5mScalpAudit', renderOrb5mScalpAudit, data);
  safeRender('watchlist', renderHyperliquidWatchlist, data);
  safeRender('trades', renderPerpTrades, data);
  safeRender('system', renderSystemStrip, data);
}

document.addEventListener('DOMContentLoaded', () => {
  if (typeof initPanelCollapse === 'function') {
    initPanelCollapse({ storageKey: 'hlPerpsPanelCollapse' });
  }
  applyTheme();
  document.getElementById('theme-toggle')?.addEventListener('click', () => applyTheme(nextThemeMode()));
  document.getElementById('adaptive-download')?.addEventListener('click', downloadAdaptiveSnapshot);

  document.addEventListener('click', (event) => {
    const expandBtn = event.target.closest('[data-expand-coin]');
    if (expandBtn) {
      event.preventDefault();
      toggleWatchlistExpand(expandBtn.dataset.expandCoin);
      if (state.payload) renderHyperliquidWatchlist(state.payload);
      return;
    }
    const sortBtn = event.target.closest('[data-sort-table][data-sort-key]');
    if (sortBtn) {
      const tableKey = sortBtn.dataset.sortTable;
      setSort(tableKey, sortBtn.dataset.sortKey);
      if (state.pagination[tableKey]) resetPage(tableKey);
      if (state.payload) {
        renderHyperliquidWatchlist(state.payload);
        renderPerpTrades(state.payload);
      }
    }
    const resetBtn = event.target.closest('[data-reset-columns]');
    if (resetBtn) {
      const tableKey = resetBtn.dataset.resetColumns;
      resetColumns(tableKey, tableKey === 'watchlist' ? watchlistColumns : tradeColumns);
      if (state.payload) {
        renderHyperliquidWatchlist(state.payload);
        renderPerpTrades(state.payload);
      }
    }
  });

  document.addEventListener('change', (event) => {
    const input = event.target.closest('[data-column-table][data-column-key]');
    if (input) {
      updateColumnVisibility(input.dataset.columnTable, input.dataset.columnKey, input.checked);
      if (state.payload) {
        renderHyperliquidWatchlist(state.payload);
        renderPerpTrades(state.payload);
      }
    }
  });

  document.querySelectorAll('[data-trade-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.tradeFilter = btn.dataset.tradeFilter || 'open';
      resetPage('trades');
      if (state.payload) renderPerpTrades(state.payload);
    });
  });

  document.querySelectorAll('[data-pnl-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.pnlTab = btn.dataset.pnlTab || PNL_TAB_DEFAULT;
      resetPage('pnl');
      if (state.payload) renderPaperPnlReport(state.payload);
    });
  });

  const activeOnly = document.getElementById('watchlist-active-only');
  if (activeOnly) {
    activeOnly.addEventListener('change', () => {
      state.watchlistActiveOnly = activeOnly.checked;
      resetPage('watchlist');
      if (state.payload) renderHyperliquidWatchlist(state.payload);
    });
  }

  const statusFilter = document.getElementById('watchlist-status-filter');
  if (statusFilter) {
    statusFilter.value = state.watchlistStatusFilter;
    statusFilter.addEventListener('change', () => {
      state.watchlistStatusFilter = statusFilter.value || 'all';
      saveStorage(STORAGE_KEYS.watchlistStatus, state.watchlistStatusFilter);
      resetPage('watchlist');
      if (state.payload) renderHyperliquidWatchlist(state.payload);
    });
  }

  bindPaginationControls('watchlist', () => {
    if (state.payload) renderHyperliquidWatchlist(state.payload);
  });
  bindPaginationControls('pnl', () => {
    if (state.payload) renderPaperPnlReport(state.payload);
  });
  bindPaginationControls('adaptive', () => {
    if (state.payload) renderAdaptiveControl(state.payload);
  });
  bindPaginationControls('trades', () => {
    if (state.payload) renderPerpTrades(state.payload);
  });
  bindPaginationControls('sd', () => {
    if (state.payload) renderSupplyDemandAudit(state.payload);
  });
  bindPaginationControls('dsma', () => {
    if (state.payload) renderDualSmaAudit(state.payload);
  });
  bindPaginationControls('ebp', () => {
    if (state.payload) renderEma50BreakoutPullbackAudit(state.payload);
  });
  bindPaginationControls('orb', () => {
    if (state.payload) renderOrb5mScalpAudit(state.payload);
  });

  loadDashboard().catch((err) => {
    console.error(err);
    const wl = document.getElementById('hl-watchlist-body');
    if (wl) wl.innerHTML = '<tr><td colspan="7" class="pi-empty">Failed to load watchlist.</td></tr>';
    const tb = document.getElementById('perp-paper-trades-body');
    if (tb) tb.innerHTML = '<tr><td colspan="19" class="pi-empty">Failed to load paper trades.</td></tr>';
    const adaptiveBody = document.getElementById('adaptive-decisions-body');
    if (adaptiveBody) adaptiveBody.innerHTML = '<tr><td colspan="6" class="pi-empty">Failed to load adaptive control data.</td></tr>';
    setText('adaptive-summary', `Dashboard payload failed: ${err.message || err}`);
  });
  setInterval(() => loadDashboard().catch(console.error), 30000);
});
