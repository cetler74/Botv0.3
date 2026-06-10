const STORAGE_KEYS = {
  theme: 'spotDashboardTheme',
  sort: 'spotDashboardSort',
  pairExchange: 'spotPairExchangeFilter',
  pairStatus: 'spotPairStatusFilter',
  exchangesPageSize: 'spotExchangesPageSize',
  pairsPageSize: 'spotPairsPageSize',
  tradesPageSize: 'spotTradesPageSize',
  pnlPageSize: 'spotPnlPageSize',
  sdPageSize: 'spotSdPageSize',
  dsmaPageSize: 'spotDsmaPageSize',
  ebpPageSize: 'spotEbpPageSize',
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
  pairsActiveOnly: false,
  pairExchangeFilter: loadStorage(STORAGE_KEYS.pairExchange, 'all'),
  pairStatusFilter: loadStorage(STORAGE_KEYS.pairStatus, 'all'),
  themeMode: loadStorage(STORAGE_KEYS.theme, 'system'),
  sort: loadStorage(STORAGE_KEYS.sort, {
    exchanges: { key: 'exchange', direction: 'asc' },
    pairs: { key: 'exchange', direction: 'asc' },
    trades: { key: 'time', direction: 'desc' },
  }),
  pagination: {
    exchanges: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.exchangesPageSize) },
    pairs: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.pairsPageSize) },
    trades: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.tradesPageSize) },
    pnl: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.pnlPageSize) },
    sd: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.sdPageSize) },
    dsma: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.dsmaPageSize) },
    ebp: { page: 1, pageSize: loadPageSize(STORAGE_KEYS.ebpPageSize) },
  },
};

const money = (value) => {
  const n = Number(value || 0);
  return n.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2,
  });
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
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
};

function toDecimal(value) {
  const n = Number(value || 0);
  return Math.abs(n) > 1 ? n / 100 : n;
}

function sideTargetPrice(entry, side, decimal, favorable = true) {
  if (!entry || !decimal) return 0;
  const s = String(side || 'long').toLowerCase();
  if (s === 'short') return entry * (1 + (favorable ? -decimal : decimal));
  return entry * (1 + (favorable ? decimal : -decimal));
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

function spotExitRules(data) {
  return ((data.spot || {}).exitRules) || {};
}

function spotProtectionDetails(t, entryPrice, isOpen, data) {
  if (!isOpen || !entryPrice) {
    return { profitProtection: '—', trailingStop: '—', stopLoss: '—', sort: {} };
  }
  const rules = spotExitRules(data);
  const metadata = tradeMetadata(t);
  const side = 'long';
  const ppActivation = toDecimal(rules.profitProtectionActivationPct ?? 0.015);
  const trailActivation = toDecimal(rules.trailingActivationPct ?? 0.018);
  const breakeven = toDecimal(rules.breakevenFloorPct ?? 0.014);
  const trailStep = toDecimal(rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.003);
  const trailArm = Math.max(trailActivation, breakeven + trailStep);
  const ppArmPrice = sideTargetPrice(entryPrice, side, ppActivation, true);
  const trailArmPrice = sideTargetPrice(entryPrice, side, trailArm, true);
  const rawProtectionState = String(
    t.profit_protection || metadata.profit_protection || '',
  ).toLowerCase();
  const protectionActive = Boolean(rawProtectionState && rawProtectionState !== 'inactive');
  const trailState = String(t.trail_stop || metadata.trail_stop || '').toLowerCase() === 'active'
    ? 'active'
    : 'waiting';
  const trigger = Number(
    t.trail_stop_trigger || metadata.trail_stop_trigger || t.profit_protection_trigger || 0,
  );
  const stopLossEnabled = rules.fixedStopLossEnabled !== false;
  const stopLossDecimal = toDecimal(rules.stopLossPct ?? 1.5);
  const stopLossPrice = stopLossEnabled ? sideTargetPrice(entryPrice, side, stopLossDecimal, false) : 0;
  const ppEnabled = rules.profitProtectionEnabled !== false;
  const trailEnabled = rules.trailingStopEnabled !== false;

  return {
    profitProtection: !ppEnabled
      ? protectionStatusCell('disabled', 'Profit protection off in config', false)
      : protectionStatusCell(
        protectionActive ? 'active' : 'inactive',
        protectionActive && trigger
          ? `Exit: ${priceMoney(trigger)}`
          : `Activates: ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)})`,
        protectionActive,
      ),
    trailingStop: !trailEnabled
      ? protectionStatusCell('disabled', 'Trailing stop off in config', false)
      : protectionStatusCell(
        trailState === 'active' ? 'active' : 'inactive',
        trailState === 'active' && trigger
          ? `Exit: ${priceMoney(trigger)}`
          : `Activates: ${priceMoney(trailArmPrice)} (+${decimalPct(trailArm)})`,
        trailState === 'active',
      ),
    stopLoss: protectionStatusCell(
      stopLossEnabled ? 'enabled' : 'disabled',
      stopLossEnabled && stopLossPrice
        ? `Exit: ${priceMoney(stopLossPrice)} (-${decimalPct(stopLossDecimal)})`
        : 'Stop loss off',
      stopLossEnabled,
    ),
    sort: {
      profitProtection: protectionActive ? 1 : 0,
      trailingStop: trailState === 'active' ? 1 : 0,
      stopLoss: stopLossEnabled ? stopLossPrice : -1,
    },
  };
}

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
    // Keep session state if storage is unavailable.
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
    console.error(`[spot-dashboard] ${section} render failed:`, err);
  }
}

function applyTheme(mode = state.themeMode) {
  state.themeMode = mode || 'system';
  saveStorage(STORAGE_KEYS.theme, state.themeMode);
  if (state.themeMode === 'system') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.dataset.theme = state.themeMode;
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

function exchangeLabel(value) {
  const key = String(value || '').toLowerCase();
  const map = { binance: 'Binance', bybit: 'Bybit', cryptocom: 'Crypto.com' };
  return map[key] || value || '-';
}

function formatTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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
  renderCyclePill('spot-entry-cycle', 'Entry last run', cycles.spot_entry, details.spot_entry);
  renderCyclePill('spot-exit-cycle', 'Exit last run', cycles.spot_exit, details.spot_exit);
  setText(
    'trading-status',
    `Bot: ${trading.status || trading.trading_status || 'unknown'} · Entry ${cycleShortText(cycles.spot_entry)} · Exit ${cycleShortText(cycles.spot_exit)}`
  );
}

function displayPair(value, exchange = '') {
  const raw = String(value || '').trim().toUpperCase();
  if (!raw) return '-';
  if (raw.includes('/')) return raw;
  const quote = String(exchange).toLowerCase() === 'cryptocom' ? 'USD' : 'USDC';
  for (const suffix of ['USDC', 'USDT', 'USD']) {
    if (raw.endsWith(suffix) && raw.length > suffix.length) {
      return `${raw.slice(0, -suffix.length)}/${suffix}`;
    }
  }
  return `${raw}/${quote}`;
}

function compareValues(a, b, type) {
  if (type === 'number' || type === 'datetime' || type === 'boolean') {
    const na = Number(a ?? 0);
    const nb = Number(b ?? 0);
    return na === nb ? 0 : na > nb ? 1 : -1;
  }
  return String(a ?? '').localeCompare(String(b ?? ''), undefined, { numeric: true, sensitivity: 'base' });
}

function sortedRows(rows, columns, tableKey) {
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

function renderTableHead(headId, tableKey, columns) {
  const head = document.getElementById(headId);
  if (!head) return;
  head.innerHTML = `<tr>${columns.map((col) => `
    <th title="${escapeHtml(col.title || '')}">
      <button type="button" class="pi-sort-btn" data-sort-table="${tableKey}" data-sort-key="${col.key}">
        <span>${escapeHtml(col.label)}</span>${sortGlyph(tableKey, col.key)}
      </button>
    </th>`).join('')}</tr>`;
}

function renderRows(bodyId, rows, columns, emptyText) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${columns.length}" class="pi-empty">${escapeHtml(emptyText)}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>${columns.map((col) => `<td data-label="${escapeHtml(col.label)}">${col.render(row)}</td>`).join('')}</tr>`).join('');
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
  state.pagination[tableKey].page = 1;
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
    const pager = state.pagination[tableKey];
    const pageSize = Number(pager.pageSize) || DEFAULT_PAGE_SIZE;
    let totalFiltered = 0;
    if (tableKey === 'exchanges') {
      totalFiltered = sortedRows(((state.payload.spot || {}).exchanges || []), exchangeColumns, 'exchanges').length;
    } else     if (tableKey === 'pairs') {
      totalFiltered = filteredPairRows(state.payload).rows.length;
    } else if (tableKey === 'pnl') {
      const report = state.payload.spotPnlReport || {};
      totalFiltered = ((report.breakdowns || {})[state.pnlTab] || []).length;
    } else if (tableKey === 'sd') {
      totalFiltered = auditTableRows(state.payload.supplyDemandAudit).length;
    } else if (tableKey === 'dsma') {
      totalFiltered = auditTableRows(state.payload.dualSmaAudit).length;
    } else if (tableKey === 'ebp') {
      totalFiltered = auditTableRows(state.payload.ema50BreakoutPullbackAudit).length;
    } else {
      totalFiltered = tradesForFilter(state.payload).length;
    }
    const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize) || 1);
    if (pager.page < totalPages) {
      pager.page += 1;
      rerender();
    }
  });
}

function statusTag(status, analysis = '') {
  const key = String(status || '').toLowerCase();
  if (key === 'open') return '<span class="pi-tag hot">Open position</span>';
  if (String(analysis || '').toLowerCase().includes('review')) return '<span class="pi-tag risk">Needs review</span>';
  return '<span class="pi-tag watch">Under analysis</span>';
}

function spotExecutionReason(row, setupReason = '') {
  const setup = String(setupReason || '').trim();
  const analysis = String(row.analysis || '').trim();
  const status = String(row.status || '').toLowerCase();
  if (status === 'open') {
    return setup || 'Position is open; exit rules now manage the trade.';
  }
  if (row.lastExitReason) {
    return setup
      ? `${setup} | Last execution: ${row.lastExitReason}`
      : `Last execution: ${row.lastExitReason}`;
  }
  const signal = String(row.signal || row.entry_signal_5m || '').toLowerCase();
  if (['buy', 'sell', 'long', 'short'].includes(signal)) {
    return setup
      ? `${setup} | Execution: strategy signal only; order still depends on spot entry gates, balance, min order size, cooldowns, and pair limits.`
      : 'Execution: strategy signal only; order still depends on spot entry gates, balance, min order size, cooldowns, and pair limits.';
  }
  if (analysis && analysis !== 'Under analysis') {
    return setup ? `${setup} | Pair state: ${analysis}` : analysis;
  }
  return setup || 'No actionable setup from this strategy in the latest audit row.';
}

const exchangeColumns = [
  { key: 'exchange', label: 'Exchange', sortType: 'text', sortValue: (r) => r.exchange, render: (r) => `<span class="pi-pair">${escapeHtml(exchangeLabel(r.exchange))}</span>` },
  { key: 'quote', label: 'Quote', sortType: 'text', sortValue: (r) => r.quote, render: (r) => escapeHtml(r.quote || '-') },
  { key: 'balance', label: 'Balance', sortType: 'number', sortValue: (r) => r.balance, render: (r) => money(r.balance) },
  { key: 'available', label: 'Available', sortType: 'number', sortValue: (r) => r.availableBalance, render: (r) => money(r.availableBalance) },
  { key: 'invested', label: 'Invested', sortType: 'number', sortValue: (r) => r.investedAmount, render: (r) => money(r.investedAmount) },
  { key: 'open', label: 'Open', sortType: 'number', sortValue: (r) => r.openTrades, render: (r) => String(r.openTrades || 0) },
  { key: 'pairs', label: 'Pairs', sortType: 'number', sortValue: (r) => r.pairCount, render: (r) => String(r.pairCount || 0) },
  { key: 'daily', label: 'Daily PnL', sortType: 'number', sortValue: (r) => r.dailyPnl, render: (r) => `<span class="${cls(r.dailyPnl)}">${money(r.dailyPnl)}</span>` },
  { key: 'total', label: 'Total PnL', sortType: 'number', sortValue: (r) => r.totalPnl, render: (r) => `<span class="${cls(r.totalPnl)}">${money(r.totalPnl)}</span>` },
  { key: 'unrealized', label: 'Unrealized', sortType: 'number', sortValue: (r) => r.unrealizedPnl, render: (r) => `<span class="${cls(r.unrealizedPnl)}">${money(r.unrealizedPnl)}</span>` },
  { key: 'winRate', label: 'Win rate', sortType: 'number', sortValue: (r) => r.winRate, render: (r) => pct(r.winRate) },
];

const pairColumns = [
  { key: 'exchange', label: 'Exchange', sortType: 'text', sortValue: (r) => r.exchange, render: (r) => escapeHtml(exchangeLabel(r.exchange)) },
  { key: 'pair', label: 'Pair', sortType: 'text', sortValue: (r) => r.pair, render: (r) => `<span class="pi-pair">${escapeHtml(r.pair || '-')}</span>` },
  { key: 'status', label: 'Status', sortType: 'text', sortValue: (r) => r.status, render: (r) => statusTag(r.status, r.analysis) },
  { key: 'analysis', label: 'Analysis', sortType: 'text', sortValue: (r) => r.analysis, render: (r) => escapeHtml(spotExecutionReason(r, r.analysis || '')) },
  { key: 'entry', label: 'Entry', sortType: 'number', sortValue: (r) => r.entryPrice, render: (r) => r.hasOpenPosition ? money(r.entryPrice) : '-' },
  { key: 'current', label: 'Current', sortType: 'number', sortValue: (r) => r.currentPrice, render: (r) => r.hasOpenPosition ? money(r.currentPrice) : '-' },
  { key: 'size', label: 'Size', sortType: 'number', sortValue: (r) => r.positionSize, render: (r) => r.hasOpenPosition ? num(r.positionSize, 6) : '-' },
  { key: 'unrealized', label: 'Unrealized', sortType: 'number', sortValue: (r) => r.unrealizedPnl, render: (r) => r.hasOpenPosition ? `<span class="${cls(r.unrealizedPnl)}">${money(r.unrealizedPnl)}</span>` : '-' },
  { key: 'pnl24h', label: '24h PnL', sortType: 'number', sortValue: (r) => r.pnl24h, render: (r) => `<span class="${cls(r.pnl24h)}">${money(r.pnl24h)}</span>` },
  { key: 'win24h', label: '24h WR', sortType: 'number', sortValue: (r) => r.winRate24h, render: (r) => pct(r.winRate24h) },
  { key: 'strategy', label: 'Strategy', sortType: 'text', sortValue: (r) => r.strategy, render: (r) => `<span class="pi-muted-cell">${escapeHtml(r.strategy || '-')}</span>` },
];

function normalizeTrade(t, data) {
  const status = String(t.status || '').toUpperCase();
  const isOpen = status === 'OPEN';
  const exchange = String(t.exchange || t.exchange_name || '').toLowerCase();
  const entryPrice = Number(t.entry_price || t.entryPrice || 0);
  const exitPrice = Number(t.exit_price || t.exitPrice || 0);
  const currentPrice = Number(t.current_price || t.currentPrice || entryPrice || exitPrice || 0);
  const size = Number(t.position_size || t.positionSize || 0);
  const realized = Number(t.realized_pnl || t.realizedPnl || t.pnl || 0);
  const unrealized = Number(t.unrealized_pnl || t.unrealizedPnl || 0);
  const time = status === 'CLOSED' ? (t.exit_time || t.exitTime || t.entry_time || t.entryTime) : (t.entry_time || t.entryTime);
  const protection = spotProtectionDetails(t, entryPrice, isOpen, data || state.payload || {});
  return {
    ...t,
    _status: status,
    _isOpen: isOpen,
    _exchange: exchange,
    _pair: displayPair(t.pair || t.symbol || '', exchange),
    _entryPrice: entryPrice,
    _exitPrice: exitPrice,
    _currentPrice: currentPrice,
    _size: size,
    _notional: currentPrice * size,
    _realized: realized,
    _unrealized: unrealized,
    _time: time,
    _timeValue: new Date(time || 0).getTime() || 0,
    _strategy: String(t.strategy || ''),
    _exitReason: String(t.exit_reason || t.exitReason || ''),
    _protection: protection,
  };
}

const tradeColumns = [
  { key: 'time', label: 'Time', sortType: 'datetime', sortValue: (t) => t._timeValue, render: (t) => escapeHtml(formatTime(t._time)) },
  { key: 'exchange', label: 'Exchange', sortType: 'text', sortValue: (t) => t._exchange, render: (t) => escapeHtml(exchangeLabel(t._exchange)) },
  { key: 'pair', label: 'Pair', sortType: 'text', sortValue: (t) => t._pair, render: (t) => `<span class="pi-pair">${escapeHtml(t._pair)}</span>` },
  { key: 'status', label: 'Status', sortType: 'text', sortValue: (t) => t._status, render: (t) => `<span class="pi-signal">${escapeHtml(t._status || '-')}</span>` },
  { key: 'side', label: 'Side', sortType: 'text', sortValue: () => 'long', render: () => '<span class="pi-tag pi-side-long">long</span>' },
  { key: 'entry', label: 'Entry', sortType: 'number', sortValue: (t) => t._entryPrice, render: (t) => money(t._entryPrice) },
  { key: 'exit', label: 'Exit', sortType: 'number', sortValue: (t) => t._exitPrice, render: (t) => t._status === 'CLOSED' ? money(t._exitPrice) : '-' },
  { key: 'current', label: 'Current', sortType: 'number', sortValue: (t) => t._currentPrice, render: (t) => money(t._currentPrice) },
  { key: 'size', label: 'Size', sortType: 'number', sortValue: (t) => t._size, render: (t) => num(t._size, 6) },
  { key: 'notional', label: 'Value', sortType: 'number', sortValue: (t) => t._notional, render: (t) => money(t._notional) },
  { key: 'realized', label: 'Realized', sortType: 'number', sortValue: (t) => t._realized, render: (t) => `<span class="${cls(t._realized)}">${money(t._realized)}</span>` },
  { key: 'unrealized', label: 'Unrealized', sortType: 'number', sortValue: (t) => t._unrealized, render: (t) => `<span class="${cls(t._unrealized)}">${money(t._unrealized)}</span>` },
  { key: 'profitProtection', label: 'Profit protection', sortType: 'number', sortValue: (t) => t._protection.sort.profitProtection || 0, render: (t) => t._protection.profitProtection },
  { key: 'trailingStop', label: 'Trailing stop', sortType: 'number', sortValue: (t) => t._protection.sort.trailingStop || 0, render: (t) => t._protection.trailingStop },
  { key: 'stopLoss', label: 'Stop loss', sortType: 'number', sortValue: (t) => t._protection.sort.stopLoss || 0, render: (t) => t._protection.stopLoss },
  { key: 'strategy', label: 'Strategy', sortType: 'text', sortValue: (t) => t._strategy, render: (t) => `<span class="pi-muted-cell">${escapeHtml(t._strategy || '-')}</span>` },
  { key: 'exitReason', label: 'Exit reason', sortType: 'text', sortValue: (t) => t._exitReason, render: (t) => `<span class="pi-muted-cell">${escapeHtml(t._exitReason || (t._isOpen ? '' : '-'))}</span>` },
];

function renderTopbar(data) {
  const trading = data.tradingStatus || {};
  const status = trading.status || trading.trading_status || 'unknown';
  setText('spot-engine-pill', `Trading: ${String(status).toUpperCase()}`);
  const engine = document.getElementById('spot-engine-pill');
  if (engine) {
    engine.classList.toggle('ok', String(status).toLowerCase().includes('active') || String(status).toLowerCase().includes('running'));
    engine.classList.toggle('bad', String(status).toLowerCase().includes('stop'));
  }
  const health = {};
  (data.exchangeStatus || []).forEach((s) => { health[String(s.exchange || '').toLowerCase()] = s.status || ''; });
  const orch = document.getElementById('spot-orchestrator-pill');
  if (orch) {
    const st = health.orchestrator || 'unknown';
    orch.textContent = `Orchestrator: ${st.includes('healthy') ? 'OK' : st}`;
    orch.classList.toggle('ok', st.includes('healthy'));
    orch.classList.toggle('bad', st.includes('unreachable'));
  }
}

function renderHero(data) {
  const p = data.portfolio || {};
  setText('hero-equity', money(p.equity));
  setText('hero-available', `Available: ${money(p.availableBalance)}`);
  setText('hero-invested', money(p.investedAmount));
  setText('spot-open-trades', String(p.openTrades || 0));
  setText('spot-open-profit', String(p.openInProfit || 0));
  setText('spot-open-loss', String(p.openInLoss || 0));
  setText('spot-daily-pnl', money(p.dailyPnl), p.dailyPnl);
  setText('spot-realized-pnl', money(p.realizedPnl), p.realizedPnl);
  setText('spot-unrealized-pnl', money(p.unrealizedPnl), p.unrealizedPnl);
  setText('spot-win-rate', pct(p.winRate));

  const trading = data.tradingStatus || {};
  setText('trading-status', `Bot: ${trading.status || trading.trading_status || 'unknown'}`);
  setText('updated-at', `Updated ${new Date(data.timestamp).toLocaleTimeString()}`);
  renderCycleStatus(data);
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

function pnlBreakdownLabel(row) {
  if (state.pnlTab === 'exchange') return exchangeLabel(row.label);
  return row.label;
}

function renderSpotPnlReport(data) {
  const report = data.spotPnlReport || {};
  const agg = report.aggregate || {};
  const body = document.getElementById('spot-pnl-breakdown-body');

  setText('spot-pnl-report-window', formatReportWindow(report.windowStart, report.windowEnd));
  setText('spot-pnl-closed', String(agg.closed ?? 0));
  setText('spot-pnl-open', String(agg.open ?? 0));
  setText('spot-pnl-realized', money(agg.realized), agg.realized);
  setText('spot-pnl-gross-before-fees', money(agg.grossBeforeFees), agg.grossBeforeFees);
  setText('spot-pnl-fees', money(agg.fees));
  setText('spot-pnl-fee-drag', agg.feeDragPct === null || agg.feeDragPct === undefined
    ? 'n/a'
    : pct(agg.feeDragPct));
  setText('spot-pnl-funding', money(agg.funding), agg.funding);
  setText('spot-pnl-win-rate', pct(agg.winRate));
  setText('spot-pnl-avg-win', money(agg.avgWin), agg.avgWin);
  setText('spot-pnl-avg-loss', money(agg.avgLoss), agg.avgLoss);
  setText('spot-pnl-pf', fmtProfitFactor(agg.profitFactor));
  setText('spot-pnl-win-hold', `${num(agg.avgWinHoldMin ?? 0, 1)} min`);
  setText('spot-pnl-loss-hold', `${num(agg.avgLossHoldMin ?? 0, 1)} min`);

  document.querySelectorAll('[data-spot-pnl-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.spotPnlTab === state.pnlTab);
  });

  const breakdowns = report.breakdowns || {};
  const allRows = breakdowns[state.pnlTab] || [];
  const meta = paginateRows(allRows, 'pnl');
  const rows = meta.rows;
  if (!body) return;
  if (!report.tradeCount) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No spot trades in the last 24 hours.</td></tr>';
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
      <td data-label="Label">${escapeHtml(pnlBreakdownLabel(row))}</td>
      <td data-label="Closed">${row.nClosed ?? 0}</td>
      <td data-label="Open">${row.nOpen ?? 0}</td>
      <td data-label="PnL"><span class="${cls(row.pnl)}">${money(row.pnl)}</span></td>
      <td data-label="Avg"><span class="${cls(row.avg)}">${money(row.avg)}</span></td>
      <td data-label="WR%">${pct(row.winRate)}</td>
      <td data-label="PF">${escapeHtml(fmtProfitFactor(row.profitFactor))}</td>
    </tr>`).join('');
  renderPagination('pnl', meta);
}

function renderExchangeAnalysis(data) {
  const allRows = sortedRows(((data.spot || {}).exchanges || []), exchangeColumns, 'exchanges');
  const meta = paginateRows(allRows, 'exchanges');
  renderTableHead('spot-exchange-head', 'exchanges', exchangeColumns);
  renderRows(
    'spot-exchange-body',
    meta.rows,
    exchangeColumns,
    allRows.length ? 'No exchanges on this page.' : 'No exchange analysis available.'
  );
  renderPagination('exchanges', meta);
  setText('exchange-analysis-meta', `${allRows.length} exchanges`);
}

function filteredPairRows(data) {
  const all = ((data.spot || {}).pairs || []).slice();
  let rows = all;
  if (state.pairExchangeFilter !== 'all') {
    rows = rows.filter((r) => String(r.exchange || '').toLowerCase() === state.pairExchangeFilter);
  }
  if (state.pairStatusFilter !== 'all') {
    rows = rows.filter((r) => String(r.status || '').toLowerCase() === state.pairStatusFilter);
  }
  if (state.pairsActiveOnly) {
    rows = rows.filter((r) => r.hasOpenPosition || Number(r.closedTrades24h || 0) > 0);
  }
  return { all, rows: sortedRows(rows, pairColumns, 'pairs') };
}

function renderPairs(data) {
  const { all, rows } = filteredPairRows(data);
  const meta = paginateRows(rows, 'pairs');
  meta.allCount = all.length;
  renderTableHead('spot-pairs-head', 'pairs', pairColumns);
  renderRows(
    'spot-pairs-body',
    meta.rows,
    pairColumns,
    rows.length ? 'No pairs on this page.' : 'No spot pairs match the current filters.'
  );
  const filterNote = meta.filteredCount !== meta.allCount || state.pairsActiveOnly
    ? ` (filtered from ${meta.allCount})`
    : '';
  renderPagination('pairs', meta, { filterNote });
  setText('pair-count', `${rows.length} pairs`);
}

function tradesForFilter(data) {
  const st = data.spotTrades || {};
  const open = st.open || [];
  const closed = st.closed || [];
  const trades = state.tradeFilter === 'open'
    ? open
    : state.tradeFilter === 'closed'
      ? closed
      : [...open, ...closed];
  return sortedRows(trades.map((t) => normalizeTrade(t, data)), tradeColumns, 'trades');
}

function renderTrades(data) {
  const st = data.spotTrades || {};
  setText('spot-trades-meta', `${st.totalOpen || 0} open · ${st.totalClosed || 0} closed`);
  document.querySelectorAll('[data-trade-filter]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tradeFilter === state.tradeFilter);
  });
  renderTableHead('spot-trades-head', 'trades', tradeColumns);
  const allRows = tradesForFilter(data);
  const meta = paginateRows(allRows, 'trades');
  const msg = state.tradeFilter === 'open'
    ? 'No open spot positions.'
    : state.tradeFilter === 'closed'
      ? 'No closed spot trades found.'
      : 'No spot trades recorded.';
  renderRows(
    'spot-trades-body',
    meta.rows,
    tradeColumns,
    allRows.length ? 'No trades on this page.' : msg
  );
  renderPagination('trades', meta);
}

function auditTableRows(auditPayload) {
  const audit = auditPayload || {};
  const summary = audit.summary || {};
  return summary.recent || audit.rows || [];
}

function formatAuditVenue(venue) {
  const key = String(venue || '').toLowerCase();
  if (key === 'binance') return 'Binance';
  if (key === 'bybit') return 'Bybit';
  if (key === 'cryptocom') return 'Crypto.com';
  return venue || '-';
}

function auditExchangeCell(row) {
  return `<td>${escapeHtml(formatAuditVenue(row.venue))}</td>`;
}

function renderSupplyDemandAudit(data) {
  const audit = data.supplyDemandAudit || {};
  const summary = audit.summary || {};
  const rates = summary.stepPassRates || {};
  setText('spot-sd-step1', `${num(rates.step1 || 0, 1)}%`);
  setText('spot-sd-step2', `${num(rates.step2 || 0, 1)}%`);
  setText('spot-sd-step3', `${num(rates.step3 || 0, 1)}%`);
  setText('spot-sd-evals', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('spot-sd-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Trend mix: ${JSON.stringify(summary.byTrendDirection || {})}.`
      : 'No supply/demand evaluations in the last 24 hours.';
  }
  const body = document.getElementById('spot-sd-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'sd');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('sd', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('sd', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const steps = [
      row.step1_pass ? '1✓' : '1✗',
      row.step2_pass ? '2✓' : '2✗',
      row.step3_pass ? '3✓' : '3✗',
    ].join(' ');
    const reason = spotExecutionReason(row, row.entry_reason || row.step3_reason || row.step2_reason || row.step1_reason || '');
    return `<tr>
      ${auditExchangeCell(row)}
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

function renderArcAudit(data) {
  const audit = data.arcAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('spot-arc-area', `${num(rates.area || 0, 1)}%`);
  setText('spot-arc-range', `${num(rates.range || 0, 1)}%`);
  setText('spot-arc-candle', `${num(rates.candle || 0, 1)}%`);
  setText('spot-arc-evals', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('spot-arc-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Zones: ${JSON.stringify(summary.byZone || {})}.`
      : 'No ARC evaluations in the last 24 hours.';
  }
  const body = document.getElementById('spot-arc-audit-body');
  if (!body) return;
  const rows = auditTableRows(audit).slice(0, 50);
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No evaluations loaded.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => {
    const movePct = num((row.range_pct_move || 0) * 100, 1);
    const reason = spotExecutionReason(row, row.entry_reason || row.candle_reason || row.range_reason || row.area_reason || '');
    return `<tr>
      ${auditExchangeCell(row)}
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
  setText('spot-dsma-daily', `${num(rates.daily || 0, 1)}%`);
  setText('spot-dsma-15m', `${num(rates.confirm15m || 0, 1)}%`);
  setText('spot-dsma-5m', `${num(rates.entry5m || 0, 1)}%`);
  setText('spot-dsma-evals', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('spot-dsma-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. Bias mix: ${JSON.stringify(summary.byDailyBias || {})}.`
      : 'No dual-SMA evaluations in the last 24 hours.';
  }
  const body = document.getElementById('spot-dsma-audit-body');
  if (!body) return;
  const allRows = auditTableRows(audit);
  const meta = paginateRows(allRows, 'dsma');
  const rows = meta.rows;
  if (!allRows.length) {
    body.innerHTML = '<tr><td colspan="8" class="pi-empty">No evaluations loaded.</td></tr>';
    renderPagination('dsma', { ...meta, filteredCount: 0, totalPages: 1, page: 1 });
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="pi-empty">No evaluations on this page.</td></tr>';
    renderPagination('dsma', meta);
    return;
  }
  body.innerHTML = rows.map((row) => {
    const ext = num((row.extension_distance_pct || 0) * 100, 2);
    const reason = spotExecutionReason(row, row.entry_reason || row.entry_5m_reason || row.confirm_15m_reason || row.daily_reason || '');
    return `<tr>
      ${auditExchangeCell(row)}
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

function renderEma50BreakoutPullbackAudit(data) {
  const audit = data.ema50BreakoutPullbackAudit || {};
  const summary = audit.summary || {};
  const rates = summary.gatePassRates || {};
  setText('spot-ebp-breakout', `${num(rates.breakout || 0, 1)}%`);
  setText('spot-ebp-pullback', `${num(rates.pullback || 0, 1)}%`);
  setText('spot-ebp-trigger', `${num(rates.trigger || 0, 1)}%`);
  setText('spot-ebp-evals', String(summary.totalEvaluations || audit.count || 0));
  const note = document.getElementById('spot-ebp-summary');
  if (note) {
    note.textContent = summary.totalEvaluations
      ? `${summary.totalEvaluations} evaluation(s) in the last 24h. States: ${JSON.stringify(summary.setupStateCounts || {})}.`
      : 'No EMA50 breakout-pullback evaluations in the last 24 hours.';
  }
  const body = document.getElementById('spot-ebp-audit-body');
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
    const reason = spotExecutionReason(row, row.entry_reason || row.trigger_reason || row.pullback_reason || row.breakout_reason || '');
    return `<tr>
      ${auditExchangeCell(row)}
      <td>${escapeHtml(row.symbol || '')}</td>
      <td>${escapeHtml(row.setup_state || '')}</td>
      <td>${escapeHtml(row.direction || '')}</td>
      <td>${escapeHtml(row.signal || 'hold')}</td>
      <td>${num(row.reward_risk || 0, 2)}</td>
      <td class="pi-reason-cell">${escapeHtml(reason)}</td>
    </tr>`;
  }).join('');
  renderPagination('ebp', meta);
}

function renderSystemStrip(data) {
  const strip = document.getElementById('system-strip');
  if (!strip) return;
  strip.innerHTML = (data.exchangeStatus || [])
    .map((s) => `<span class="pi-health-pill">${escapeHtml(s.exchange)}: ${escapeHtml(s.status)}</span>`)
    .join('');
}

async function loadDashboard() {
  const response = await fetch('/api/v1/dashboard/spot-intelligence', { cache: 'no-store' });
  if (!response.ok) throw new Error('Spot dashboard payload failed');
  const data = await response.json();
  state.payload = data;
  safeRender('topbar', renderTopbar, data);
  safeRender('hero', renderHero, data);
  safeRender('spotPnlReport', renderSpotPnlReport, data);
  safeRender('supplyDemandAudit', renderSupplyDemandAudit, data);
  safeRender('arcAudit', renderArcAudit, data);
  safeRender('dualSmaAudit', renderDualSmaAudit, data);
  safeRender('ema50BreakoutPullbackAudit', renderEma50BreakoutPullbackAudit, data);
  safeRender('exchangeAnalysis', renderExchangeAnalysis, data);
  safeRender('pairs', renderPairs, data);
  safeRender('trades', renderTrades, data);
  safeRender('system', renderSystemStrip, data);
}

document.addEventListener('DOMContentLoaded', () => {
  if (typeof initPanelCollapse === 'function') {
    initPanelCollapse({ storageKey: 'spotDashboardPanelCollapse' });
  }
  applyTheme();
  document.getElementById('theme-toggle')?.addEventListener('click', () => applyTheme(nextThemeMode()));

  document.addEventListener('click', (event) => {
    const sortBtn = event.target.closest('[data-sort-table][data-sort-key]');
    if (sortBtn) {
      const tableKey = sortBtn.dataset.sortTable;
      setSort(tableKey, sortBtn.dataset.sortKey);
      if (state.pagination[tableKey]) resetPage(tableKey);
      if (state.payload) {
        renderExchangeAnalysis(state.payload);
        renderPairs(state.payload);
        renderTrades(state.payload);
      }
    }
  });

  document.querySelectorAll('[data-trade-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.tradeFilter = btn.dataset.tradeFilter || 'open';
      resetPage('trades');
      if (state.payload) renderTrades(state.payload);
    });
  });

  document.querySelectorAll('[data-spot-pnl-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.pnlTab = btn.dataset.spotPnlTab || PNL_TAB_DEFAULT;
      resetPage('pnl');
      if (state.payload) renderSpotPnlReport(state.payload);
    });
  });

  const activeOnly = document.getElementById('pairs-active-only');
  activeOnly?.addEventListener('change', () => {
    state.pairsActiveOnly = activeOnly.checked;
    resetPage('pairs');
    if (state.payload) renderPairs(state.payload);
  });

  const exchangeFilter = document.getElementById('pair-exchange-filter');
  if (exchangeFilter) {
    exchangeFilter.value = state.pairExchangeFilter;
    exchangeFilter.addEventListener('change', () => {
      state.pairExchangeFilter = exchangeFilter.value || 'all';
      saveStorage(STORAGE_KEYS.pairExchange, state.pairExchangeFilter);
      resetPage('pairs');
      if (state.payload) renderPairs(state.payload);
    });
  }

  const statusFilter = document.getElementById('pair-status-filter');
  if (statusFilter) {
    statusFilter.value = state.pairStatusFilter;
    statusFilter.addEventListener('change', () => {
      state.pairStatusFilter = statusFilter.value || 'all';
      saveStorage(STORAGE_KEYS.pairStatus, state.pairStatusFilter);
      resetPage('pairs');
      if (state.payload) renderPairs(state.payload);
    });
  }

  bindPaginationControls('exchanges', () => {
    if (state.payload) renderExchangeAnalysis(state.payload);
  });
  bindPaginationControls('pnl', () => {
    if (state.payload) renderSpotPnlReport(state.payload);
  });
  bindPaginationControls('pairs', () => {
    if (state.payload) renderPairs(state.payload);
  });
  bindPaginationControls('trades', () => {
    if (state.payload) renderTrades(state.payload);
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

  loadDashboard().catch((err) => {
    console.error(err);
    const exchanges = document.getElementById('spot-exchange-body');
    if (exchanges) exchanges.innerHTML = '<tr><td colspan="11" class="pi-empty">Failed to load exchange analysis.</td></tr>';
    const pairs = document.getElementById('spot-pairs-body');
    if (pairs) pairs.innerHTML = '<tr><td colspan="11" class="pi-empty">Failed to load spot pairs.</td></tr>';
    const trades = document.getElementById('spot-trades-body');
    if (trades) trades.innerHTML = '<tr><td colspan="17" class="pi-empty">Failed to load spot trades.</td></tr>';
  });
  setInterval(() => loadDashboard().catch(console.error), 30000);
});
