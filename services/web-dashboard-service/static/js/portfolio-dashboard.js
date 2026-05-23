const state = { payload: null };

const money = (value) => {
  const n = Number(value || 0);
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2 });
};

const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const num = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: digits });
const cls = (value) => Number(value || 0) < 0 ? 'negative' : Number(value || 0) > 0 ? 'positive' : '';

function setText(id, value, toneValue = null) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  el.classList.remove('positive', 'negative');
  if (toneValue !== null) el.classList.add(cls(toneValue));
}

function renderSummary(data) {
  const p = data.portfolio || {};
  setText('total-portfolio', money(p.totalPortfolio));
  setText('daily-pnl', money(p.dailyPnl), p.dailyPnl);
  setText('total-pnl', money(p.totalPnl), p.totalPnl);
  setText('invested-amount', money(p.investedAmount));
  setText('open-trades', String(p.openTrades || 0));
  setText('win-rate', pct((p.winRate || 0) * (p.winRate <= 1 ? 100 : 1)));
  setText('unrealized-pnl', money(p.unrealizedPnl), p.unrealizedPnl);
  const trading = data.tradingStatus || {};
  setText('trading-status', `Status: ${trading.status || trading.trading_status || 'unknown'}`);
  setText('updated-at', `Updated ${new Date(data.timestamp).toLocaleTimeString()}`);
}

function renderExchangeStatus(data) {
  const map = {};
  (data.exchangeStatus || []).forEach(s => { map[String(s.exchange || '').toLowerCase()] = s.status || 'unknown'; });
  [['binance', 'exchange-pill-binance'], ['bybit', 'exchange-pill-bybit'], ['cryptocom', 'exchange-pill-cryptocom']].forEach(([name, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const status = map[name] || 'healthy';
    el.classList.remove('ok', 'warn', 'bad');
    el.classList.add(status.includes('healthy') ? 'ok' : status.includes('unreachable') ? 'bad' : 'warn');
  });
  const strip = document.getElementById('system-strip');
  if (strip) {
    strip.innerHTML = (data.exchangeStatus || []).map(s => `<span class="pi-health-pill">${s.exchange}: ${s.status}</span>`).join('');
  }
}

function renderExternalStatus(data) {
  const usage = ((data.externalData || {}).coinstats || {});
  const el = document.getElementById('coinstats-credit-pill');
  if (!el) return;
  el.classList.remove('watch', 'paused');
  if (!usage.enabled) {
    el.textContent = 'CoinStats: disabled';
    return;
  }
  if (!usage.configured) {
    el.textContent = 'CoinStats: API key missing';
    el.classList.add('watch');
    return;
  }
  const used = Number(usage.usedCredits || 0);
  const limit = Number(usage.configuredMonthlyLimit || usage.totalCredits || 20000);
  el.textContent = `CoinStats: ${num(used, 0)} / ${num(limit, 0)} credits`;
  if (usage.budgetStatus === 'paused') el.classList.add('paused');
  if (usage.budgetStatus === 'watch') el.classList.add('watch');
}

function renderPositions(data) {
  const body = document.getElementById('positions-body');
  const rows = data.positions || [];
  setText('position-count', `${rows.length} pairs`);
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="9" class="pi-empty">No tracked pairs available.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr>
      <td><span class="pi-token-dot"></span><span class="pi-pair">${row.pair || '-'}</span></td>
      <td>${row.exchange || '-'}</td>
      <td>${row.price ? money(row.price) : '-'}</td>
      <td class="${cls(row.priceChange24h)}">${pct(row.priceChange24h)}</td>
      <td>${row.positionSize ? num(row.positionSize, 6) : '-'}</td>
      <td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td>
      <td class="${cls(row.unrealizedPnl)}">${money(row.unrealizedPnl)}</td>
      <td>${row.strategy || '-'}</td>
      <td><span class="pi-signal">${row.signal || row.status || 'watch'}</span></td>
    </tr>
  `).join('');
}

function renderNews(data) {
  const list = document.getElementById('news-list');
  const news = data.news || [];
  if (!list) return;
  if (!news.length) {
    list.innerHTML = '<div class="pi-empty">No recent token news found for tracked pairs.</div>';
    return;
  }
  list.innerHTML = news.map(item => {
    const tagClass = item.sentiment === 'risk' ? 'risk' : item.affectsOpenPosition ? 'hot' : '';
    const href = item.url || '#';
    return `
      <article class="pi-news-item">
        <a href="${href}" target="_blank" rel="noopener">${item.headline}</a>
        <div class="pi-news-meta">
          <span>${item.source || item.provider || 'News'}</span>
          <span>${item.publishedAt || ''}</span>
          <span class="pi-tag ${tagClass}">${item.affectsOpenPosition ? 'Affects open position' : item.relevance || 'Relevant'}</span>
          <span>${(item.tokens || []).join(', ')}</span>
        </div>
      </article>`;
  }).join('');
}

function sparkBars(seed) {
  const base = String(seed || 'BTC').split('').reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
  return Array.from({ length: 12 }, (_, i) => `<span style="height:${18 + ((base + i * 11) % 42)}%"></span>`).join('');
}

function renderTokens(data) {
  const wrap = document.getElementById('token-cards');
  const tokens = data.tokenResearch || [];
  if (!wrap) return;
  if (!tokens.length) {
    wrap.innerHTML = '<div class="pi-empty">Token research is waiting for tracked pair data.</div>';
    return;
  }
  wrap.innerHTML = tokens.slice(0, 8).map(t => `
    <article class="pi-token-card">
      <div class="pi-token-top">
        <div>
          <div class="pi-token-symbol">${t.token}</div>
          <div class="pi-muted">${t.name || t.token}${t.rank ? ` · Rank ${t.rank}` : ''}</div>
        </div>
        <span class="pi-tag ${t.isOpen ? 'hot' : ''}">${t.signal || 'watch'}</span>
      </div>
      <div class="pi-token-price">${t.price ? money(t.price) : money((t.realizedPnl || 0) + (t.unrealizedPnl || 0))}</div>
      <div class="${cls(t.priceChange1d)}">${pct(t.priceChange1d)} 24h</div>
      <div class="pi-spark">${sparkBars(t.token)}</div>
      <p class="pi-token-note">${t.whyItMatters || 'Market context is cached for this token.'}</p>
    </article>
  `).join('');
}

function renderDailyPnl(data) {
  const wrap = document.getElementById('daily-pnl-bars');
  let rows = data.dailyPnl || [];
  if (!Array.isArray(rows)) rows = [];
  rows = rows.slice(-14);
  if (!wrap) return;
  if (!rows.length) {
    wrap.innerHTML = '<div class="pi-empty">Daily PnL history unavailable.</div>';
    return;
  }
  const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r.realized_pnl || r.pnl || r.value || 0))), 1);
  wrap.innerHTML = rows.map(r => {
    const value = Number(r.realized_pnl || r.pnl || r.value || 0);
    const height = Math.max(4, Math.round((Math.abs(value) / maxAbs) * 160));
    const label = String(r.date || '').slice(5) || '';
    return `
      <div class="pi-bar-day" title="${label}: ${money(value)}">
        <div class="pi-bar-track"><div class="pi-bar ${value < 0 ? 'negative' : ''}" style="height:${height}px"></div></div>
        <span class="pi-bar-label">${label}</span>
      </div>`;
  }).join('');
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
  renderSummary(data);
  renderExchangeStatus(data);
  renderExternalStatus(data);
  renderPositions(data);
  renderNews(data);
  renderTokens(data);
  renderDailyPnl(data);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-control]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try { await controlBot(btn.dataset.control); }
      catch (err) { console.error(err); alert(err.message); }
      finally { btn.disabled = false; }
    });
  });
  loadDashboard().catch(err => {
    console.error(err);
    const body = document.getElementById('positions-body');
    if (body) body.innerHTML = '<tr><td colspan="9" class="pi-empty">Failed to load dashboard data.</td></tr>';
  });
  setInterval(() => loadDashboard().catch(console.error), 30000);
});
