/**
 * Shared profit-protection / trailing / stop-loss UI helpers for spot + portfolio dashboards.
 */
(function (global) {
  'use strict';

  const LOCKED_STATES = new Set(['profit_guaranteed', 'setup_breakeven']);
  const MILESTONE_STATES = new Set(['target_progress', 'setup_partial']);

  function toDecimal(value) {
    const n = Number(value || 0);
    return Math.abs(n) > 1 ? n / 100 : n;
  }

  function decimalPct(value) {
    return `${(Number(value || 0) * 100).toFixed(2)}%`;
  }

  function signedPct(value) {
    const n = Number(value || 0);
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(2)}%`;
  }

  function priceMoney(value) {
    const n = Number(value || 0);
    const digits = Math.abs(n) >= 1 ? 6 : 8;
    return n.toLocaleString(undefined, {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: digits,
      minimumFractionDigits: 6,
    });
  }

  function sideTargetPrice(entry, side, decimal, favorable = true) {
    if (!entry || !decimal) return 0;
    const s = String(side || 'long').toLowerCase();
    if (s === 'short') return entry * (1 + (favorable ? -decimal : decimal));
    return entry * (1 + (favorable ? decimal : -decimal));
  }

  function tradeMetadata(t) {
    const raw = t && (t.metadata ?? t.meta ?? {});
    if (!raw) return {};
    if (typeof raw === 'string') {
      try { return JSON.parse(raw); } catch (_) { return {}; }
    }
    return raw;
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function parseSetupFromTrade(t) {
    const reason = String(t.entry_reason || t.entryReason || '');
    const idx = reason.indexOf('[setup:');
    if (idx < 0) return {};
    const start = idx + '[setup:'.length;
    let depth = 0;
    let end = -1;
    for (let i = start; i < reason.length; i += 1) {
      const ch = reason[i];
      if (ch === '{') depth += 1;
      if (ch === '}') {
        depth -= 1;
        if (depth === 0) {
          end = i + 1;
          break;
        }
      }
    }
    if (end < 0) return {};
    try {
      return JSON.parse(reason.slice(start, end));
    } catch (_) {
      return {};
    }
  }

  function normalizeProtectionState(raw) {
    return String(raw || '').trim().toLowerCase();
  }

  function classifyProtectionState(raw) {
    const state = normalizeProtectionState(raw);
    if (LOCKED_STATES.has(state)) return 'armed';
    if (MILESTONE_STATES.has(state)) return 'milestone';
    if (!state || state === 'inactive') return 'waiting';
    return 'waiting';
  }

  function protectionStatusCell(label, detail, tone = 'inactive', title = '') {
    const toneClass = ['active', 'waiting', 'milestone', 'near-miss', 'inactive', 'disabled', 'enabled', 'estimate']
      .includes(tone)
      ? tone
      : 'inactive';
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    return `
    <div class="pi-protection-cell"${titleAttr}>
      <span class="pi-protection-state ${toneClass}">${escapeHtml(label)}</span>
      <span class="pi-protection-detail">${detail}</span>
    </div>`;
  }

  function progressBarHtml({ entry, current, ppArm, trailArm, target, peak }) {
    const levels = [entry, current, ppArm, trailArm, target, peak].filter((v) => Number(v) > 0);
    if (!entry || levels.length < 2) return '';
    const lo = Math.min(...levels);
    const hi = Math.max(...levels);
    const span = hi - lo || 1;
    const pctOf = (px) => Math.max(0, Math.min(100, ((Number(px) - lo) / span) * 100));
    const marks = [
      { cls: 'entry', left: pctOf(entry), label: 'E' },
      { cls: 'pp', left: pctOf(ppArm), label: 'PP' },
      { cls: 'trail', left: pctOf(trailArm), label: 'T' },
    ];
    if (target > 0) marks.push({ cls: 'target', left: pctOf(target), label: 'TP' });
    const curLeft = pctOf(current);
    const peakLeft = peak > 0 ? pctOf(peak) : null;
    return `
      <div class="pi-protection-progress" aria-hidden="true">
        <div class="pi-protection-progress-track">
          ${peakLeft != null ? `<span class="pi-protection-progress-peak" style="left:${peakLeft.toFixed(1)}%"></span>` : ''}
          <span class="pi-protection-progress-current" style="left:${curLeft.toFixed(1)}%"></span>
          ${marks.map((m) => (
            `<span class="pi-protection-progress-mark ${m.cls}" style="left:${m.left.toFixed(1)}%" title="${m.label}"></span>`
          )).join('')}
        </div>
      </div>`;
  }

  function resolveExitRules(t, data, { spotDefaultPath } = {}) {
    if (t.exitRules && typeof t.exitRules === 'object') return t.exitRules;
    const strategy = String(t.strategy || t.source_strategy || '').toLowerCase();
    const byStrategy = (
      (data.spot && data.spot.exitRulesByStrategy)
      || (data.hyperliquid && data.hyperliquid.exitRulesByStrategy)
      || {}
    );
    if (strategy && byStrategy[strategy]) return byStrategy[strategy];
    if (spotDefaultPath) {
      return ((data.spot || {}).exitRules) || {};
    }
    return ((data.hyperliquid || {}).config || {}).entryRules || {};
  }

  function buildProtectionDetails(t, entryPrice, side, isOpen, data, options = {}) {
    if (!isOpen || !entryPrice) {
      return {
        profitProtection: '—',
        trailingStop: '—',
        stopLoss: '—',
        liquidation: options.includeLiquidation ? '—' : undefined,
        sort: {},
        extras: '',
      };
    }
    const rules = resolveExitRules(t, data, options);
    const metadata = tradeMetadata(t);
    const setup = parseSetupFromTrade(t);
    const ppActivation = toDecimal(rules.profitProtectionActivationPct ?? options.defaultPpArm ?? 0.015);
    const trailActivation = toDecimal(rules.trailingActivationPct ?? options.defaultTrailArm ?? 0.018);
    const breakeven = toDecimal(rules.breakevenFloorPct ?? 0.014);
    const trailStep = toDecimal(rules.tightenedTrailingStepPct ?? rules.trailingStepPct ?? 0.003);
    const trailArm = rules.trailArmPct != null
      ? toDecimal(rules.trailArmPct)
      : Math.max(trailActivation, breakeven + trailStep);
    const ppArmPrice = sideTargetPrice(entryPrice, side, ppActivation, true);
    const trailArmPrice = sideTargetPrice(entryPrice, side, trailArm, true);
    const rawState = normalizeProtectionState(
      t.profit_protection || metadata.profit_protection || '',
    );
    const classification = classifyProtectionState(rawState);
    const trailState = String(t.trail_stop || metadata.trail_stop || '').toLowerCase() === 'active'
      ? 'active'
      : 'waiting';
    const trigger = Number(
      t.trail_stop_trigger || metadata.trail_stop_trigger || 0,
    );
    const hasLockedTrigger = trigger > 0 && (classification === 'armed' || classification === 'milestone');
    const highest = Number(t.highest_price || t.highestPrice || metadata.highest_price || 0);
    const current = Number(t.current_price || t.currentPrice || 0);
    const peakPct = entryPrice > 0 && highest > 0
      ? ((highest - entryPrice) / entryPrice) * 100
      : 0;
    const nowPct = entryPrice > 0 && current > 0
      ? (String(side).toLowerCase() === 'short'
        ? ((entryPrice - current) / entryPrice) * 100
        : ((current - entryPrice) / entryPrice) * 100)
      : 0;
    const armPctPoints = ppActivation * 100;
    const nearMiss = (
      classification !== 'armed'
      && peakPct > 0
      && peakPct >= (armPctPoints - 0.15)
      && peakPct < armPctPoints
      && nowPct <= (peakPct - 0.30)
    );
    const ppEnabled = rules.profitProtectionEnabled !== false;
    const trailEnabled = rules.trailingStopEnabled !== false;

    let ppLabel = 'waiting';
    let ppTone = 'waiting';
    let ppDetail = `Activates: ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)})`;
    let ppTitle = 'Profit protection arms when peak PnL reaches the activation threshold, then locks a floor exit price.';

    if (!ppEnabled) {
      ppLabel = 'disabled';
      ppTone = 'disabled';
      ppDetail = 'Profit protection off in config';
    } else if (classification === 'armed' && hasLockedTrigger) {
      ppLabel = 'armed';
      ppTone = 'active';
      ppDetail = `Exit: ${priceMoney(trigger)} · Peak ${signedPct(peakPct)} → now ${signedPct(nowPct)}`;
      ppTitle = `Locked floor (${rawState || 'armed'}). Peak ${signedPct(peakPct)}, now ${signedPct(nowPct)}.`;
    } else if (classification === 'milestone') {
      ppLabel = nearMiss ? 'near-miss' : 'milestone';
      ppTone = nearMiss ? 'near-miss' : 'milestone';
      const milestoneBits = [
        hasLockedTrigger ? `Floor: ${priceMoney(trigger)}` : 'Target progress (50% of setup)',
        `Peak ${signedPct(peakPct)} → now ${signedPct(nowPct)}`,
        `PP arms ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)})`,
      ];
      ppDetail = milestoneBits.join(' · ');
      ppTitle = 'Setup milestone reached (half of setup target). A small floor may be raised; full profit protection still arms at the PP threshold.';
    } else if (nearMiss) {
      ppLabel = 'near-miss';
      ppTone = 'near-miss';
      ppDetail = `Peak ${signedPct(peakPct)} → now ${signedPct(nowPct)} · arms ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)})`;
      ppTitle = 'Peak nearly reached the profit-protection arm threshold, then faded without locking.';
    } else {
      ppDetail = `Activates: ${priceMoney(ppArmPrice)} (+${decimalPct(ppActivation)}) · Peak ${signedPct(peakPct)} → now ${signedPct(nowPct)}`;
    }

    let stopLossDecimal = toDecimal(rules.stopLossPct ?? (options.defaultStopPct ?? 0.015));
    let stopLossPrice = 0;
    let stopSuffix = '';
    let stopLossEnabled = rules.fixedStopLossEnabled !== false && stopLossDecimal > 0;
    if (setup.stop_pct != null && Number(setup.stop_pct) > 0) {
      // setup.stop_pct is percent points (e.g. 1.29).
      const setupStopPctPoints = Number(setup.stop_pct);
      stopLossDecimal = setupStopPctPoints > 1 ? setupStopPctPoints / 100 : setupStopPctPoints;
      stopLossPrice = Number(setup.stop_hint) > 0
        ? Number(setup.stop_hint)
        : sideTargetPrice(entryPrice, side, stopLossDecimal, false);
      stopLossEnabled = true;
      stopSuffix = ' (setup)';
    } else if (rules.stopLossSource === 'atr') {
      stopSuffix = ' (ATR)';
      stopLossPrice = stopLossEnabled ? sideTargetPrice(entryPrice, side, stopLossDecimal, false) : 0;
    } else if (rules.stopLossSource === 'coin_override') {
      stopSuffix = ' (coin)';
      stopLossPrice = stopLossEnabled ? sideTargetPrice(entryPrice, side, stopLossDecimal, false) : 0;
    } else {
      stopLossPrice = stopLossEnabled ? sideTargetPrice(entryPrice, side, stopLossDecimal, false) : 0;
    }

    // setup.target_pct / stop_pct are percent points (e.g. 2.58).
    const targetPx = Number(setup.target_hint) > 0
      ? Number(setup.target_hint)
      : (Number(setup.target_pct) > 0
        ? sideTargetPrice(
          entryPrice,
          side,
          Number(setup.target_pct) > 1 ? Number(setup.target_pct) / 100 : Number(setup.target_pct),
          true,
        )
        : 0);

    const progress = progressBarHtml({
      entry: entryPrice,
      current,
      ppArm: ppArmPrice,
      trailArm: trailArmPrice,
      target: targetPx,
      peak: highest,
    });

    const result = {
      profitProtection: protectionStatusCell(ppLabel, ppDetail, ppTone, ppTitle),
      trailingStop: !trailEnabled
        ? protectionStatusCell('disabled', 'Trailing stop off in config', 'disabled', 'Trailing stop disabled for this strategy/profile.')
        : protectionStatusCell(
          trailState === 'active' ? 'active' : 'waiting',
          trailState === 'active' && trigger
            ? `Exit: ${priceMoney(trigger)}`
            : `Activates: ${priceMoney(trailArmPrice)} (+${decimalPct(trailArm)})`,
          trailState === 'active' ? 'active' : 'waiting',
          'Trailing stop ratchets an exit under the high-water mark after activation.',
        ),
      stopLoss: protectionStatusCell(
        stopLossEnabled ? 'enabled' : 'disabled',
        stopLossEnabled && stopLossPrice
          ? `Exit: ${priceMoney(stopLossPrice)} (-${decimalPct(stopLossDecimal)})${stopSuffix}`
          : 'Stop loss off',
        stopLossEnabled ? 'enabled' : 'disabled',
        'Hard stop loss from setup metadata when present, otherwise global/config stop.',
      ),
      sort: {
        profitProtection: classification === 'armed' ? 2 : (classification === 'milestone' || nearMiss ? 1 : 0),
        trailingStop: trailState === 'active' ? 1 : 0,
        stopLoss: stopLossEnabled ? stopLossPrice : -1,
      },
      extras: progress,
    };

    if (options.includeLiquidation) {
      const margin = Number(t.margin_used ?? t.marginUsed ?? 0);
      const notional = Number(t.notional_size ?? t.notionalSize ?? 0);
      const liquidationMove = notional > 0 ? Math.max(0, margin / notional) : 0;
      const estimatedLiquidation = liquidationMove > 0
        ? sideTargetPrice(entryPrice, side, liquidationMove, false)
        : 0;
      result.liquidation = protectionStatusCell(
        'estimate',
        estimatedLiquidation ? priceMoney(estimatedLiquidation) : '—',
        'estimate',
        'Estimated liquidation from margin/notional (approximate).',
      );
      result.sort.liquidation = estimatedLiquidation;
    }

    return result;
  }

  function protectionLegendHtml() {
    return `
      <div class="pi-protection-legend" title="Profit protection states">
        <span><i class="pi-protection-state waiting">waiting</i> not armed</span>
        <span><i class="pi-protection-state milestone">milestone</i> 50% setup / raised floor</span>
        <span><i class="pi-protection-state active">armed</i> locked exit</span>
        <span><i class="pi-protection-state near-miss">near-miss</i> peak almost armed</span>
        <span class="pi-protection-legend-marks">Bar: E entry · PP arm · T trail · TP target</span>
      </div>`;
  }

  global.ProtectionUI = {
    toDecimal,
    decimalPct,
    priceMoney,
    sideTargetPrice,
    tradeMetadata,
    escapeHtml,
    parseSetupFromTrade,
    classifyProtectionState,
    protectionStatusCell,
    buildProtectionDetails,
    protectionLegendHtml,
    resolveExitRules,
  };
})(typeof window !== 'undefined' ? window : globalThis);
