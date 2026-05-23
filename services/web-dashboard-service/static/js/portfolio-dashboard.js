const state = { payload: null, tradeFilter: 'open', watchlistActiveOnly: false };

const money = (value) => {
  const n = Number(value || 0);
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2 });
};

const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const num = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: digits });
const cls = (value) => Number(value || 0) < 0 ? 'negative' : Number(value || 0) > 0 ? 'positive' : '';

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
  const mirrorEl = document.getElementById('hl-mirror-pill');
  if (mirrorEl) {
    const mirrors = (cfg.mirrorSourceExchanges || []).join(' · ') || 'none';
    mirrorEl.textContent = `Mirror: ${mirrors}`;
    mirrorEl.title = 'Spot exchanges scanned for signals';
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
  el.textContent = [
    `Min conf long ${pct((rules.minConfidenceLong || 0) * (rules.minConfidenceLong <= 1 ? 100 : 1))}`,
    `short ${pct((rules.minConfidenceShort || 0) * (rules.minConfidenceShort <= 1 ? 100 : 1))}`,
    `max ${rules.maxOpenPositions || 0} open`,
    `margin ≤ ${money(rules.maxMarginPerTrade)}`,
    `SL ${rules.stopLossPct}% / TP ${rules.takeProfitPct}%`,
  ].join(' · ');
}

function renderHyperliquidWatchlist(data) {
  const body = document.getElementById('hl-watchlist-body');
  const countEl = document.getElementById('watchlist-count');
  let rows = ((data.hyperliquid || {}).watchlist || []).slice();
  if (state.watchlistActiveOnly) {
    rows = rows.filter((r) => {
      const s = String(r.status || '').toLowerCase();
      return s === 'open' || s === 'under_analysis' || s === 'active' || s === 'mirroring' || s === 'selected';
    });
  }
  if (countEl) {
    const total = ((data.hyperliquid || {}).watchlist || []).length;
    countEl.textContent = state.watchlistActiveOnly
      ? `${rows.length} of ${total} coins`
      : `${total} coins`;
  }
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="pi-empty">No Hyperliquid pairs selected.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => {
    const side = row.openSide ? String(row.openSide).toLowerCase() : '—';
    const sideClass = side === 'long' ? 'pi-side-long' : side === 'short' ? 'pi-side-short' : '';
    return `
    <tr>
      <td><span class="pi-pair">${escapeHtml(row.coin)}</span></td>
      <td>${escapeHtml(row.pair || `${row.coin}/USD-PERP`)}</td>
      <td>${row.midPrice ? money(row.midPrice) : '—'}</td>
      <td><span class="pi-tag ${statusClass(row.status)}">${escapeHtml(statusLabel(row.status))}</span></td>
      <td>${row.hasOpenPosition ? 'Yes' : '—'}</td>
      <td>${side !== '—' ? `<span class="pi-tag ${sideClass}">${side}</span>` : '—'}</td>
      <td class="${cls(row.unrealizedPnl)}">${row.hasOpenPosition ? money(row.unrealizedPnl) : '—'}</td>
    </tr>`;
  }).join('');
}

function formatTradeTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function tradesForFilter(data) {
  const pt = data.perpTrades || {};
  const open = pt.open || [];
  const closed = pt.closed || [];
  if (state.tradeFilter === 'open') return open;
  if (state.tradeFilter === 'closed') return closed;
  return [...open, ...closed].sort((a, b) => {
    const ta = new Date(a.exit_time || a.entry_time || 0).getTime();
    const tb = new Date(b.exit_time || b.entry_time || 0).getTime();
    return tb - ta;
  });
}

function renderPerpTrades(data) {
  const pt = data.perpTrades || {};
  const summary = (data.paperPerps || {}).summary || {};
  setText('perp-trades-meta', `${pt.totalOpen ?? 0} open · ${pt.totalClosed ?? summary.closed_trades ?? 0} closed`);

  document.querySelectorAll('[data-trade-filter]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tradeFilter === state.tradeFilter);
  });

  const body = document.getElementById('perp-paper-trades-body');
  if (!body) return;
  const trades = tradesForFilter(data);
  if (!trades.length) {
    const msg = state.tradeFilter === 'open'
      ? 'No open paper perpetual positions.'
      : state.tradeFilter === 'closed'
        ? 'No closed paper perpetual trades yet.'
        : 'No paper perpetual trades recorded.';
    body.innerHTML = `<tr><td colspan="12" class="pi-empty">${msg}</td></tr>`;
    return;
  }
  body.innerHTML = trades.map((t) => {
    const status = String(t.status || '').toUpperCase();
    const isOpen = status === 'OPEN';
    const realized = Number(t.realized_pnl ?? t.realizedPnl ?? 0);
    const unrealized = Number(t.unrealized_pnl ?? t.unrealizedPnl ?? 0);
    const time = isOpen ? (t.entry_time ?? t.entryTime) : (t.exit_time ?? t.exitTime ?? t.entry_time ?? t.entryTime);
    const side = String(t.position_side ?? t.positionSide ?? '-').toLowerCase();
    const sideClass = side === 'long' ? 'pi-side-long' : side === 'short' ? 'pi-side-short' : '';
    return `
    <tr>
      <td>${escapeHtml(formatTradeTime(time))}</td>
      <td><span class="pi-pair">${escapeHtml(t.coin || t.symbol || '-')}</span></td>
      <td><span class="pi-tag ${sideClass}">${escapeHtml(side)}</span></td>
      <td><span class="pi-signal">${escapeHtml(status || '-')}</span></td>
      <td>${money(t.entry_price ?? t.entryPrice)}</td>
      <td>${isOpen ? '—' : money(t.exit_price ?? t.exitPrice)}</td>
      <td>${money(t.margin_used ?? t.marginUsed)}</td>
      <td>${money(t.notional_size ?? t.notionalSize)}</td>
      <td class="${cls(realized)}">${money(realized)}</td>
      <td class="${cls(unrealized)}">${money(unrealized)}</td>
      <td>${escapeHtml(t.source_strategy ?? t.sourceStrategy ?? '-')}</td>
      <td class="pi-muted-cell">${escapeHtml(t.exit_reason ?? t.exitReason ?? (isOpen ? '' : ''))}</td>
    </tr>`;
  }).join('');
}

function renderSystemStrip(data) {
  const strip = document.getElementById('system-strip');
  if (!strip) return;
  strip.innerHTML = (data.exchangeStatus || [])
    .map((s) => `<span class="pi-health-pill">${escapeHtml(s.exchange)}: ${escapeHtml(s.status)}</span>`)
    .join('');
}

async function controlBot(action) {
  const endpoint = action === 'emergency' ? '/api/control/emergency-stop' : `/api/control/${action}`;
  const response = await fetch(endpoint, { method: 'POST' });
  if (!response.ok) throw new Error(`Control request failed: ${action}`);
  await loadDashboard();
}

async function loadDashboard() {
  const response = await fetch('/api/v1/dashboard/portfolio-intelligence', { cache: 'no-store' });
  if (!response.ok) throw new Error('Dashboard payload failed');
  const data = await response.json();
  state.payload = data;
  safeRender('topbar', renderHyperliquidTopbar, data);
  safeRender('hero', renderHero, data);
  safeRender('entryRules', renderEntryRulesHint, data);
  safeRender('watchlist', renderHyperliquidWatchlist, data);
  safeRender('trades', renderPerpTrades, data);
  safeRender('system', renderSystemStrip, data);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-control]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try { await controlBot(btn.dataset.control); }
      catch (err) { console.error(err); alert(err.message); }
      finally { btn.disabled = false; }
    });
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
    if (tb) tb.innerHTML = '<tr><td colspan="12" class="pi-empty">Failed to load paper trades.</td></tr>';
  });
  setInterval(() => loadDashboard().catch(console.error), 30000);
});
