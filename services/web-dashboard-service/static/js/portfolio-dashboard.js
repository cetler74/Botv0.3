const STORAGE_KEYS = {
  theme: 'hlPerpsTheme',
  sort: 'hlPerpsTableSort',
  columns: 'hlPerpsColumnVisibility',
};

const state = {
  payload: null,
  tradeFilter: 'open',
  watchlistActiveOnly: false,
  lastPrices: {},
  priceComparisonBaseline: {},
  lastSnapshotTimestamp: null,
  themeMode: loadStorage(STORAGE_KEYS.theme, 'system'),
  sort: loadStorage(STORAGE_KEYS.sort, {
    watchlist: { key: 'coin', direction: 'asc' },
    trades: { key: 'time', direction: 'desc' },
  }),
  columns: loadStorage(STORAGE_KEYS.columns, {}),
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

function statusLabel(status) {
  const key = String(status || '').toLowerCase();
  const map = {
    open: 'Open position',
    under_analysis: 'Under analysis',
    active: 'Under analysis',
    mirroring: 'Under analysis',
    selected: 'Under analysis',
    allowed_no_mirror: 'Under analysis',
    no_price: 'No price',
  };
  return map[key] || 'Under analysis';
}

function statusClass(status) {
  const key = String(status || '').toLowerCase();
  if (key === 'open') return 'hot';
  if (key === 'no_price') return 'risk';
  return 'watch';
}

function entryBlockLabel(row) {
  if (!row?.entryBlocked) return '';
  const reason = String(row.entryBlockReason || '').toLowerCase();
  const map = {
    open_unrealized_negative: 'Blocked: open loss',
    recent_negative_realized_12h: 'Blocked: 12h loss cooldown',
    no_price: 'Blocked: no price',
  };
  return map[reason] || 'Blocked';
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

function currentPriceCell(t, currentPrice, entryPrice, side) {
  if (!currentPrice) return '—';
  const key = tradePriceKey(t);
  const previousPrice = Number(state.priceComparisonBaseline[key] || 0);
  const baseline = previousPrice > 0 ? previousPrice : entryPrice;
  const scope = previousPrice > 0 ? 'previous dashboard refresh' : 'entry';
  const delta = baseline > 0 ? currentPrice - baseline : 0;
  const deltaPct = baseline > 0 ? (delta / baseline) * 100 : 0;
  const absDeltaPct = Math.abs(deltaPct);
  const rawDirection = absDeltaPct < 0.0001 ? 'flat' : delta > 0 ? 'up' : 'down';
  const s = String(side || '').toLowerCase();
  const favorable = rawDirection === 'flat'
    ? 'neutral'
    : ((s === 'short' && rawDirection === 'down') || (s !== 'short' && rawDirection === 'up'))
      ? 'favorable'
      : 'adverse';
  const label = rawDirection === 'flat'
    ? 'flat'
    : `${rawDirection} ${deltaPct > 0 ? '+' : ''}${deltaPct.toFixed(2)}%`;
  return `
    <span class="pi-current-cell" title="Current price ${label} vs ${scope}">
      <span>${priceMoney(currentPrice)}</span>
      <span class="pi-direction ${rawDirection} ${favorable}" aria-label="${escapeHtml(label)}">
        <span class="pi-direction-arrow"></span>
        <span class="pi-direction-label">${escapeHtml(label)}</span>
      </span>
    </span>`;
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

function protectionDetails(t, entryPrice, side, isOpen, data) {
  if (!isOpen || !entryPrice) {
    return { profitProtection: '—', trailingStop: '—', stopLoss: '—', liquidation: '—', sort: {} };
  }
  const rules = ((data.hyperliquid || {}).config || {}).entryRules || {};
  const metadata = tradeMetadata(t);
  const ppActivation = toDecimal(rules.profitProtectionActivationPct ?? 0.0035);
  const trailActivation = toDecimal(rules.trailingActivationPct ?? 0.0035);
  const breakeven = toDecimal(rules.breakevenFloorPct ?? 0.0035);
  const trailStep = toDecimal(rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.0025);
  const trailArm = Math.max(trailActivation, breakeven + trailStep);
  const ppArmPrice = sideTargetPrice(entryPrice, side, ppActivation, true);
  const trailArmPrice = sideTargetPrice(entryPrice, side, trailArm, true);
  const rawProtectionState = String(metadata.profit_protection || '').toLowerCase();
  const protectionActive = Boolean(rawProtectionState && rawProtectionState !== 'inactive');
  const trailState = String(metadata.trail_stop || '').toLowerCase() === 'active' ? 'active' : 'waiting';
  const trigger = Number(metadata.trail_stop_trigger || t.trail_stop_trigger || 0);
  const stopLossEnabled = Boolean(rules.fixedStopLossEnabled);
  const stopLossDecimal = toDecimal(rules.stopLossPct ?? 0);
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
        ? `Exit: ${priceMoney(stopLossPrice)} (-${decimalPct(stopLossDecimal)})`
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
    _unrealized: Number(t.unrealized_pnl ?? t.unrealizedPnl ?? 0),
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

const watchlistColumns = [
  {
    key: 'coin',
    label: 'Coin',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.coin,
    render: (row) => `<span class="pi-pair">${escapeHtml(row.coin)}</span>`,
  },
  {
    key: 'pair',
    label: 'Perp pair',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.pair,
    render: (row) => escapeHtml(row.pair || `${row.coin}/USD-PERP`),
  },
  {
    key: 'price',
    label: 'HL price',
    sortType: 'number',
    defaultVisible: true,
    sortValue: (row) => Number(row.midPrice || 0),
    render: (row) => row.midPrice ? money(row.midPrice) : '—',
  },
  {
    key: 'status',
    label: 'Status',
    sortType: 'text',
    defaultVisible: true,
    sortValue: (row) => row.entryBlocked ? `blocked ${statusLabel(row.status)}` : statusLabel(row.status),
    render: (row) => {
      const blockLabel = entryBlockLabel(row);
      const blockTitle = row.entryBlockMessage ? ` title="${escapeHtml(row.entryBlockMessage)}"` : '';
      return [
        `<span class="pi-tag ${statusClass(row.status)}">${escapeHtml(statusLabel(row.status))}</span>`,
        blockLabel ? `<span class="pi-tag risk"${blockTitle}>${escapeHtml(blockLabel)}</span>` : '',
      ].filter(Boolean).join(' ');
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
    render: (row) => `<span class="${cls(row.unrealizedPnl)}">${row.hasOpenPosition ? money(row.unrealizedPnl) : '—'}</span>`,
  },
];

const tradeColumns = [
  { key: 'time', label: 'Time', sortType: 'datetime', defaultVisible: true, sortValue: (t) => t._timeValue, render: (t) => escapeHtml(formatTradeTime(t._time)) },
  { key: 'coin', label: 'Coin', sortType: 'text', defaultVisible: true, sortValue: (t) => t.coin || t.symbol || '', render: (t) => `<span class="pi-pair">${escapeHtml(t.coin || t.symbol || '-')}</span>` },
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
  { key: 'unrealized', label: 'Unrealized', sortType: 'number', defaultVisible: true, sortValue: (t) => t._unrealized, render: (t) => `<span class="${cls(t._unrealized)}">${money(t._unrealized)}</span>` },
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
  setText('hl-balance-source', source.replace(/_/g, ' '));
  setText('hero-equity', money(p.equity ?? summary.equity ?? summary.balance));
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
}

function renderEntryRulesHint(data) {
  const rules = ((data.hyperliquid || {}).config || {}).entryRules || {};
  const el = document.getElementById('entry-rules-hint');
  if (!el) return;
  const stopText = rules.fixedStopLossEnabled ? `SL ${rules.stopLossPct}%` : 'SL off';
  const feeFloor = rules.effectiveProfitFloorPct ?? ((rules.feeRatePerSide || 0.001) * 2 + (rules.profitProtectionFeeBuffer || 0.0015));
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
    `trail arm ~${decimalPct((rules.breakevenFloorPct ?? 0.0035) + (rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.0025))}`,
  ].join(' · ');
}

function watchlistRows(data) {
  let rows = ((data.hyperliquid || {}).watchlist || []).slice();
  if (state.watchlistActiveOnly) {
    rows = rows.filter((r) => {
      const s = String(r.status || '').toLowerCase();
      return s === 'open' || s === 'under_analysis' || s === 'active' || s === 'mirroring' || s === 'selected';
    });
  }
  return sortRows(rows, watchlistColumns, 'watchlist');
}

function renderHyperliquidWatchlist(data) {
  const body = document.getElementById('hl-watchlist-body');
  const countEl = document.getElementById('watchlist-count');
  const rows = watchlistRows(data);
  const visible = visibleColumns('watchlist', watchlistColumns);
  renderTableHead('hl-watchlist-head', 'watchlist', watchlistColumns);
  renderColumnMenu('watchlist-column-options', 'watchlist', watchlistColumns);

  if (countEl) {
    const total = ((data.hyperliquid || {}).watchlist || []).length;
    countEl.textContent = state.watchlistActiveOnly ? `${rows.length} of ${total} coins` : `${total} coins`;
  }
  if (body) {
    if (!visible.length) {
      renderNoColumns(body);
    } else if (!rows.length) {
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">No Hyperliquid pairs selected.</td></tr>`;
    } else {
      body.innerHTML = rows.map((row) => `
        <tr>${visible.map((col) => `<td data-label="${escapeHtml(col.label)}">${col.render(row)}</td>`).join('')}</tr>`).join('');
    }
  }
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
  const trades = tradesForFilter(data);
  const visible = visibleColumns('trades', tradeColumns);
  if (body) {
    if (!visible.length) {
      renderNoColumns(body);
    } else if (!trades.length) {
      const msg = state.tradeFilter === 'open'
        ? 'No open paper perpetual positions.'
        : state.tradeFilter === 'closed'
          ? 'No closed paper perpetual trades yet.'
          : 'No paper perpetual trades recorded.';
      body.innerHTML = `<tr><td colspan="${visible.length}" class="pi-empty">${msg}</td></tr>`;
    } else {
      body.innerHTML = trades.map((t) => `
        <tr>${visible.map((col) => `<td data-label="${escapeHtml(col.label)}">${col.render(t)}</td>`).join('')}</tr>`).join('');
    }
  }
  trades.forEach((t) => {
    const key = tradePriceKey(t);
    if (key && t._currentPrice > 0) state.lastPrices[key] = t._currentPrice;
  });
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
  safeRender('watchlist', renderHyperliquidWatchlist, data);
  safeRender('trades', renderPerpTrades, data);
  safeRender('system', renderSystemStrip, data);
}

document.addEventListener('DOMContentLoaded', () => {
  applyTheme();
  document.getElementById('theme-toggle')?.addEventListener('click', () => applyTheme(nextThemeMode()));

  document.addEventListener('click', (event) => {
    const sortBtn = event.target.closest('[data-sort-table][data-sort-key]');
    if (sortBtn) {
      setSort(sortBtn.dataset.sortTable, sortBtn.dataset.sortKey);
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
      if (state.payload) renderPerpTrades(state.payload);
    });
  });

  const activeOnly = document.getElementById('watchlist-active-only');
  if (activeOnly) {
    activeOnly.addEventListener('change', () => {
      state.watchlistActiveOnly = activeOnly.checked;
      if (state.payload) renderHyperliquidWatchlist(state.payload);
    });
  }

  loadDashboard().catch((err) => {
    console.error(err);
    const wl = document.getElementById('hl-watchlist-body');
    if (wl) wl.innerHTML = '<tr><td colspan="7" class="pi-empty">Failed to load watchlist.</td></tr>';
    const tb = document.getElementById('perp-paper-trades-body');
    if (tb) tb.innerHTML = '<tr><td colspan="19" class="pi-empty">Failed to load paper trades.</td></tr>';
  });
  setInterval(() => loadDashboard().catch(console.error), 30000);
});
