// backtest-compare.js — Enhanced A/B Compare
// Globals: compareRunA, currentResult, currentTrades, equityChart, api(), showToast(), parseTradeTime()

var compareState = { runA: null, runB: null, listCache: null };

var COMPARE_METRICS = [
  ['Return %',      'return_pct',       false],
  ['Win Rate %',    'win_rate',         false],
  ['Profit Factor', 'profit_factor',    false],
  ['Max DD %',      'max_drawdown_pct', true],
  ['Sharpe',        'sharpe_ratio',     false],
  ['Sortino',       'sortino_ratio',    false],
  ['Calmar',        'calmar_ratio',     false],
  ['Expectancy',    'expectancy',       false],
  ['Trades',        'total_trades',     null],   // neutral
  ['Payoff Ratio',  'payoff_ratio',     false],
  ['Win Streak',    'max_win_streak',   false],
  ['Loss Streak',   'max_loss_streak',  true],
  ['Kelly %',       'kelly_pct',        false],
  ['CAGR %',        'cagr_pct',        false]
];

// ── 1. Open the compare drawer ──────────────────
function openCompareDrawer() {
  var panel = document.getElementById('compare-panel');
  var body  = document.getElementById('compare-body');

  var buildUI = function(list) {
    compareState.listCache = list;
    var optionsHtml = '';
    list.forEach(function(r, i) {
      var ret = (r.stats && r.stats.return_pct !== undefined)
        ? (r.stats.return_pct >= 0 ? '+' : '') + r.stats.return_pct.toFixed(1) + '%'
        : '?';
      var label = r.symbol + ' ' + r.days + 'd  ' + ret + '  (' + r.trade_count + ')';
      optionsHtml += '<option value="' + i + '">' + label + '</option>';
    });

    var currentOpt = currentResult
      ? '<option value="current">(Current Run)</option>'
      : '';

    body.innerHTML =
      '<div style="display:flex;gap:6px;margin-bottom:8px">' +
        '<select id="compare-sel-a" onchange="selectCompareRun(\'A\', this)" ' +
          'style="flex:1;background:var(--bg-3);color:var(--text-1);border:1px solid var(--border);' +
          'border-radius:4px;padding:3px 6px;font-size:.68rem">' +
          '<option value="">— Run A —</option>' + optionsHtml + currentOpt +
        '</select>' +
        '<select id="compare-sel-b" onchange="selectCompareRun(\'B\', this)" ' +
          'style="flex:1;background:var(--bg-3);color:var(--text-1);border:1px solid var(--border);' +
          'border-radius:4px;padding:3px 6px;font-size:.68rem">' +
          '<option value="">— Run B —</option>' + optionsHtml + currentOpt +
        '</select>' +
      '</div>' +
      '<div id="compare-metrics"></div>' +
      '<div id="compare-equity-msg" style="display:none;font-size:.65rem;color:var(--text-3);text-align:center;padding:4px"></div>' +
      '<div style="text-align:right;margin-top:6px">' +
        '<button onclick="resetCompare()" style="font-size:.65rem;background:none;border:1px solid var(--border);' +
        'color:var(--text-3);border-radius:3px;padding:2px 8px;cursor:pointer">Reset</button>' +
      '</div>';

    // Auto-select current run as Run B
    if (currentResult) {
      var selB = document.getElementById('compare-sel-b');
      if (selB) { selB.value = 'current'; selectCompareRun('B', selB); }
    }

    panel.style.display = 'block';
  };

  // Fetch list or use cache
  if (compareState.listCache) {
    buildUI(compareState.listCache);
    return;
  }
  api('/api/backtest/list').then(function(list) {
    if (!list || !list.length) {
      showToast('No saved results to compare', 'info');
      return;
    }
    buildUI(list);
  }).catch(function(e) {
    showToast('Failed to load results: ' + e.message, 'error');
  });
}

// ── 2. Select a run for slot A or B ─────────────
function selectCompareRun(slot, selectElement) {
  var val = selectElement.value;
  if (!val) { compareState['run' + slot] = null; return; }

  var setRun = function(label, stats, equity) {
    compareState['run' + slot] = { label: label, stats: stats, equity: equity };
    if (compareState.runA && compareState.runB) {
      renderCompareMetrics(compareState.runA.stats, compareState.runB.stats);
      renderCompareEquity(
        compareState.runA.equity, compareState.runB.equity,
        compareState.runA.label,  compareState.runB.label
      );
    }
  };

  if (val === 'current') {
    var stats = currentResult || {};
    var eq = (currentResult && currentResult.equity_curve && currentResult.equity_curve.length)
      ? currentResult.equity_curve
      : computeEquityFromTrades(currentTrades, 10000);
    setRun('Current Run', stats, eq);
    return;
  }

  var idx  = parseInt(val);
  var item = compareState.listCache[idx];
  if (!item) return;

  var itemStats = item.stats || {};
  var label = item.symbol + ' ' + item.days + 'd';

  // Fetch full result for equity curve
  api('/api/backtest/results?file=' + encodeURIComponent(item.file)).then(function(data) {
    var eq = (data.equity_curve && data.equity_curve.length)
      ? data.equity_curve
      : computeEquityFromTrades(data.trades || [], item.balance || 10000);
    setRun(label, itemStats, eq);
  }).catch(function(e) {
    // Still show metrics even without equity
    setRun(label, itemStats, []);
  });
}

// ── 3. Compute equity from trades ───────────────
function computeEquityFromTrades(trades, initialBalance) {
  var bal = initialBalance || 10000;
  var curve = [];
  if (!trades || !trades.length) return curve;
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var ts = typeof parseTradeTime === 'function'
      ? parseTradeTime(t.exit_time)
      : new Date(t.exit_time).getTime();
    bal += (t.pnl || 0);
    curve.push({ time: ts || Date.now(), equity: bal });
  }
  return curve;
}

// ── 4. Render metrics table ─────────────────────
function renderCompareMetrics(statsA, statsB) {
  var el = document.getElementById('compare-metrics');
  if (!el) return;

  var html =
    '<div style="display:grid;grid-template-columns:1fr auto auto auto;gap:1px 8px;' +
    'font-size:.63rem;padding:4px 0">' +
    '<div style="color:var(--text-3);font-weight:600">Metric</div>' +
    '<div style="color:var(--text-3);font-weight:600;text-align:right">A</div>' +
    '<div style="color:var(--text-3);font-weight:600;text-align:right">B</div>' +
    '<div style="color:var(--text-3);font-weight:600;text-align:right">\u0394</div>';

  COMPARE_METRICS.forEach(function(m) {
    var label = m[0], key = m[1], lowerBetter = m[2];
    var a = (statsA && typeof statsA[key] === 'number') ? statsA[key] : null;
    var b = (statsB && typeof statsB[key] === 'number') ? statsB[key] : null;

    var aStr = a !== null ? fmtVal(a, key) : '--';
    var bStr = b !== null ? fmtVal(b, key) : '--';
    var dStr = '--';
    var dColor = 'var(--text-3)';

    if (a !== null && b !== null) {
      var delta = b - a;
      dStr = (delta >= 0 ? '+' : '') + fmtVal(delta, key);
      if (lowerBetter === null) {
        dColor = 'var(--text-3)';
      } else if (delta !== 0) {
        var better = lowerBetter ? delta < 0 : delta > 0;
        dColor = better ? 'var(--pos)' : 'var(--neg)';
      }
    }

    html +=
      '<div class="compare-metric" style="color:var(--text-2)">' + label + '</div>' +
      '<div class="compare-val" style="text-align:right;color:var(--text-1)">' + aStr + '</div>' +
      '<div class="compare-val" style="text-align:right;color:var(--text-1)">' + bStr + '</div>' +
      '<div class="compare-delta" style="text-align:right;color:' + dColor + ';font-weight:600">' + dStr + '</div>';
  });

  html += '</div>';
  el.innerHTML = html;
}

function fmtVal(v, key) {
  if (key === 'total_trades' || key === 'max_win_streak' || key === 'max_loss_streak') {
    return Math.round(v).toString();
  }
  return v.toFixed(2);
}

// ── 5. Render equity comparison ─────────────────
function renderCompareEquity(curveA, curveB, labelA, labelB) {
  var msgEl = document.getElementById('compare-equity-msg');
  if (!msgEl) return;

  var hasA = curveA && curveA.length > 0;
  var hasB = curveB && curveB.length > 0;

  if (!hasA && !hasB) {
    msgEl.textContent = 'No equity data available';
    msgEl.style.display = 'block';
    return;
  }

  // Show the Run B equity in the main equityChart (simpler approach)
  var curve = hasB ? curveB : curveA;
  var lbl   = hasB ? labelB : labelA;

  if (equityChart && curve.length) {
    var data = curve.map(function(e) {
      var eq = typeof e.equity === 'number' ? e.equity : (e.close || 0);
      var ts = e.time || e.timestamp || 0;
      return { timestamp: ts, open: eq, high: eq, low: eq, close: eq, volume: 0 };
    });
    equityChart.applyNewData(data);
    msgEl.textContent = 'Equity: ' + lbl + (hasA && hasB ? ' (B shown, metrics compare both)' : '');
    msgEl.style.display = 'block';
  } else {
    msgEl.textContent = 'Equity chart not initialised';
    msgEl.style.display = 'block';
  }
}

// ── 6. Reset compare ────────────────────────────
function resetCompare() {
  compareState.runA = null;
  compareState.runB = null;
  compareState.listCache = null;
  compareRunA = null;  // backward compat

  var panel = document.getElementById('compare-panel');
  if (panel) panel.style.display = 'none';

  var body = document.getElementById('compare-body');
  if (body) body.innerHTML = '';

  var msgEl = document.getElementById('compare-equity-msg');
  if (msgEl) { msgEl.style.display = 'none'; msgEl.textContent = ''; }
}
