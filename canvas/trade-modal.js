/* ═══════════════════════════════════════════
   Trade Modal — OKX-style Order Panel Logic
   ═══════════════════════════════════════════ */

var _tmState = {
  symbol: '',
  price: 0,
  direction: 'LONG',  // LONG or SHORT
  planData: null,      // full action plan row data
  balance: {},         // { aster: 1000, binance: 500 }
  balanceLoaded: false,
  requestId: 0,        // guards against stale fetch callbacks
  symbolInfo: {},      // current active info { step_size, min_qty, ... }
  symbolInfoCache: {}, // keyed by "platform:SYMBOL"
  orderType: 'MARKET', // MARKET or LIMIT
};

function openTradeModal(planData) {
  _tmState.requestId++;
  _tmState.planData = planData;
  _tmState.symbol = planData.symbol || '';
  _tmState.price = planData.price || 0;
  _tmState.direction = 'LONG';

  var displayName = _tmState.symbol.replace('USDT', '');
  var chgPct = planData.change_pct;
  var chgHtml = chgPct != null
    ? '<span style="color:' + (chgPct >= 0 ? '#0d9488' : '#e11d48') + ';font-size:.78rem;margin-left:6px">' +
      (chgPct >= 0 ? '+' : '') + chgPct + '%</span>'
    : '';

  // Header
  document.getElementById('tm-header-info').innerHTML =
    '<span class="tm-symbol">' + (typeof symbolIcon === 'function' ? symbolIcon(_tmState.symbol) : '') +
    '<strong>' + displayName + '</strong></span>' +
    '<span class="tm-price">$' + fmtPrice(_tmState.price) + chgHtml + '</span>';

  // Populate exchanges from connected badges
  _populateExchanges();

  // Leverage default + clear inputs (before _setDirection, which triggers preview)
  document.getElementById('tm-leverage').value = '5';
  document.getElementById('tm-qty-input').value = '';

  // Reset order type to MARKET
  _setOrderType('MARKET');

  // Set direction to LONG → internally calls _updateSltpFromPlan → _updatePreview
  _setDirection('LONG');

  // Clear error + hide confirmation overlay + unfreeze form
  _tmHideError();
  document.getElementById('tm-confirm-overlay').classList.remove('show');
  _tmUnfreezeForm();
  _tmPendingPayload = null;

  // Reset submit button
  var btn = document.getElementById('tm-submit-btn');
  btn.disabled = false;
  btn.classList.remove('loading');
  btn.innerHTML = '<i class="fas fa-check mr-1"></i>確認下單';

  // Fetch balances + symbol info
  _fetchBalances();
  _fetchSymbolInfo();

  // Clear constraints display
  var rulesEl = document.getElementById('tm-rules');
  if (rulesEl) rulesEl.innerHTML = '<span style="color:#94a3b8;font-size:.68rem">載入交易規則…</span>';

  $('#trade-modal').modal('show');
}

function _populateExchanges() {
  var row = document.getElementById('tm-exchange-row');
  row.innerHTML = '';
  var connected = [];
  var badges = [
    { id: 'aster-conn-btn', name: 'aster', label: 'Aster' },
    { id: 'binance-conn-btn', name: 'binance', label: 'Binance' },
    { id: 'hl-conn-btn', name: 'hyperliquid', label: 'HyperLiquid' },
  ];
  badges.forEach(function(b) {
    var el = document.getElementById(b.id);
    if (el && el.classList.contains('exch-on')) {
      connected.push(b);
    }
  });
  if (!connected.length) {
    row.innerHTML = '<span style="color:#94a3b8;font-size:.78rem">無已連接交易所</span>';
    return;
  }
  connected.forEach(function(b, i) {
    var btn = document.createElement('button');
    btn.className = 'tm-exch-btn' + (i === 0 ? ' active' : '');
    btn.textContent = b.label;
    btn.setAttribute('data-platform', b.name);
    btn.onclick = function() {
      row.querySelectorAll('.tm-exch-btn').forEach(function(x) { x.classList.remove('active'); });
      btn.classList.add('active');
      // Clear old info so preview uses no stale rules while fetching
      _tmState.symbolInfo = {};
      _fetchSymbolInfo();
      _updatePreview();
    };
    row.appendChild(btn);
  });
}

function _getSelectedPlatform() {
  var active = document.querySelector('#tm-exchange-row .tm-exch-btn.active');
  return active ? active.getAttribute('data-platform') : '';
}

function _setDirection(dir) {
  _tmState.direction = dir;
  var longBtn = document.getElementById('tm-dir-long');
  var shortBtn = document.getElementById('tm-dir-short');
  longBtn.className = 'tm-dir-btn' + (dir === 'LONG' ? ' long-active' : '');
  shortBtn.className = 'tm-dir-btn' + (dir === 'SHORT' ? ' short-active' : '');
  _updateSltpFromPlan();
  _updatePreview();
}


function _setOrderType(type) {
  _tmState.orderType = type;
  var mBtn = document.getElementById('tm-type-market');
  var lBtn = document.getElementById('tm-type-limit');
  mBtn.className = 'tm-dir-btn' + (type === 'MARKET' ? ' long-active' : '');
  lBtn.className = 'tm-dir-btn' + (type === 'LIMIT' ? ' long-active' : '');
  var limitGroup = document.getElementById('tm-limit-group');
  if (limitGroup) limitGroup.style.display = type === 'LIMIT' ? '' : 'none';
  // Auto-fill limit price with current price
  if (type === 'LIMIT') {
    var lpInput = document.getElementById('tm-limit-price');
    if (lpInput && !lpInput.value) lpInput.value = _tmState.price || '';
  }
  // Disable SL/TP for limit orders (not set until filled)
  var slInput = document.getElementById('tm-sl-price');
  var tpInput = document.getElementById('tm-tp-price');
  if (slInput) { slInput.disabled = type === 'LIMIT'; if (type === 'LIMIT') slInput.value = ''; }
  if (tpInput) { tpInput.disabled = type === 'LIMIT'; if (type === 'LIMIT') tpInput.value = ''; }
  // Restore SL/TP from plan when switching back to market
  if (type === 'MARKET') _updateSltpFromPlan();
  _updatePreview();
}

function _updateSltpFromPlan() {
  var p = _tmState.planData;
  if (!p) return;
  var slInput = document.getElementById('tm-sl-price');
  var tpInput = document.getElementById('tm-tp-price');
  if (_tmState.direction === 'LONG') {
    slInput.value = p.sl_long || '';
    tpInput.value = p.tp_long || '';
  } else {
    slInput.value = p.sl_short || '';
    tpInput.value = p.tp_short || '';
  }
  _updatePreview();
}

function _fetchBalances() {
  _tmState.balanceLoaded = false;
  fetch('/api/exchange/balance')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      _tmState.balance = {};
      for (var k in data) {
        if (data[k] && data[k].balance != null) {
          _tmState.balance[k] = data[k].balance;
        }
      }
      _tmState.balanceLoaded = true;
      _updatePreview();
    })
    .catch(function() {
      _tmState.balanceLoaded = false;
    });
}

function _fetchSymbolInfo() {
  var platform = _getSelectedPlatform();
  if (!platform || !_tmState.symbol) return;
  // Multi-key cache: reuse if already fetched for this platform+symbol
  var cacheKey = platform + ':' + _tmState.symbol;
  if (_tmState.symbolInfoCache[cacheKey]) {
    _tmState.symbolInfo = _tmState.symbolInfoCache[cacheKey];
    _renderRules();
    _updatePreview();
    return;
  }
  var reqId = _tmState.requestId;
  fetch('/api/exchange/symbol-info?symbol=' + encodeURIComponent(_tmState.symbol) + '&platform=' + encodeURIComponent(platform))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (reqId !== _tmState.requestId) return;
      if (data.error) {
        _renderRulesError(data.error);
        return;
      }
      _tmState.symbolInfoCache[cacheKey] = data;
      _tmState.symbolInfo = data;
      _renderRules();
      _updatePreview();
    })
    .catch(function() {
      if (reqId !== _tmState.requestId) return;
      _renderRulesError('無法載入交易規則');
    });
}

function _renderRules() {
  var el = document.getElementById('tm-rules');
  if (!el) return;
  var info = _tmState.symbolInfo;
  if (!info || !info.min_qty) { el.innerHTML = ''; return; }

  var displayName = _tmState.symbol.replace('USDT', '');
  var safeDN = typeof esc === 'function' ? esc(displayName) : displayName.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  var minUsdt = info.min_qty * _tmState.price;
  var lines = [];
  lines.push('• 最低數量 <b>' + info.min_qty + ' ' + safeDN + '</b> (~$' + minUsdt.toFixed(2) + ')');
  if (info.min_notional) lines.push('• 最低名義值 <b>$' + info.min_notional + '</b>');
  lines.push('• 每次增減 <b>' + info.step_size + ' ' + safeDN + '</b>');
  lines.push('• 市價單 ✓ 限價單 ✓');

  el.innerHTML =
    '<div style="display:flex;align-items:flex-start;gap:6px">' +
      '<i class="fas fa-info-circle" style="margin-top:3px;flex-shrink:0"></i>' +
      '<div style="line-height:1.8">' + lines.join('<br>') + '</div>' +
    '</div>';
}

function _renderRulesError(msg) {
  var el = document.getElementById('tm-rules');
  if (el) el.innerHTML = '<span style="color:#f59e0b"><i class="fas fa-exclamation-triangle mr-1"></i>' + msg + '</span>';
}

function _tmQuickFill(pct) {
  var platform = _getSelectedPlatform();
  var bal = _tmState.balance[platform];
  if (!bal || bal <= 0) return;
  var usdt = bal * pct;
  document.getElementById('tm-qty-input').value = usdt.toFixed(2);
  _updatePreview();
}

function _getEntryPrice() {
  return _tmState.price;
}

function _updatePreview() {
  var price = _getEntryPrice();
  var leverage = parseInt(document.getElementById('tm-leverage').value) || 5;
  var usdtInput = parseFloat(document.getElementById('tm-qty-input').value) || 0;
  var slPrice = parseFloat(document.getElementById('tm-sl-price').value) || 0;
  var tpPrice = parseFloat(document.getElementById('tm-tp-price').value) || 0;

  // Use limit price for calculations when in limit mode
  var effectivePrice = _tmState.orderType === 'LIMIT'
    ? (parseFloat(document.getElementById('tm-limit-price').value) || price)
    : price;

  // Estimated qty with min_qty warning
  var qty = effectivePrice > 0 ? usdtInput * leverage / effectivePrice : 0;
  var estEl = document.getElementById('tm-est-pos');
  var displayName = _tmState.symbol.replace('USDT', '');
  var info = _tmState.symbolInfo;
  if (qty > 0) {
    var estText = '預計倉位: ' + qty.toFixed(6) + ' ' + displayName;
    // Warn if below minimum after rounding
    if (info && info.step_size) {
      var step = info.step_size;
      var roundedQty = step > 0 ? Math.round(Math.round(qty / step) * step * 1e8) / 1e8 : qty;
      if (roundedQty <= 0 || (info.min_qty && roundedQty < info.min_qty)) {
        estText += '  ⚠️ 低於最低';
        estEl.style.color = '#e11d48';
      } else {
        estEl.style.color = '';
      }
    }
    estEl.textContent = estText;
  } else {
    estEl.textContent = '';
    estEl.style.color = '';
  }

  // SL/TP pct hints
  var slPctEl = document.getElementById('tm-sl-pct');
  var tpPctEl = document.getElementById('tm-tp-pct');
  if (slPrice > 0 && price > 0) {
    var slPct = Math.abs(slPrice - price) / price * 100;
    slPctEl.textContent = '-' + slPct.toFixed(2) + '%';
  } else {
    slPctEl.textContent = '';
  }
  if (tpPrice > 0 && price > 0) {
    var tpPct = Math.abs(tpPrice - price) / price * 100;
    tpPctEl.textContent = '+' + tpPct.toFixed(2) + '%';
  } else {
    tpPctEl.textContent = '';
  }

  // Preview card
  var margin = leverage > 0 ? usdtInput : 0;
  var feeEst = usdtInput * leverage * 0.0004;  // 0.04% taker fee
  var rr = 0;
  if (slPrice > 0 && tpPrice > 0 && price > 0) {
    var risk = Math.abs(price - slPrice);
    var reward = Math.abs(tpPrice - price);
    rr = risk > 0 ? reward / risk : 0;
  }

  // Balance display
  var platform = _getSelectedPlatform();
  var bal = _tmState.balance[platform];
  var balHtml = _tmState.balanceLoaded && bal != null
    ? '<div class="tm-preview-row"><span>可用餘額</span><span class="tm-preview-val">$' + bal.toFixed(2) + '</span></div>'
    : '';

  document.getElementById('tm-preview').innerHTML =
    balHtml +
    '<div class="tm-preview-row"><span>保證金</span><span class="tm-preview-val">$' + margin.toFixed(2) + '</span></div>' +
    '<div class="tm-preview-row"><span>手續費(est)</span><span class="tm-preview-val">~$' + feeEst.toFixed(2) + '</span></div>' +
    (rr > 0 ? '<div class="tm-preview-row"><span>風險/收益比</span><span class="tm-preview-val">1:' + rr.toFixed(1) + '</span></div>' : '');
}

function _tmHideError() {
  var el = document.getElementById('tm-error');
  el.classList.remove('show');
  el.textContent = '';
  el.style.background = '';
  el.style.color = '';
}

function _tmShowError(msg) {
  var el = document.getElementById('tm-error');
  el.textContent = msg;
  el.classList.add('show');
}

// ── Staged payload for confirmation overlay ──
var _tmPendingPayload = null;

function submitTradeOrder() {
  _tmHideError();

  var platform = _getSelectedPlatform();
  if (!platform) { _tmShowError('請選擇交易所'); return; }

  var price = _getEntryPrice();
  var leverage = Math.min(Math.max(parseInt(document.getElementById('tm-leverage').value) || 5, 1), 125);
  var usdtInput = parseFloat(document.getElementById('tm-qty-input').value) || 0;
  var slPrice = parseFloat(document.getElementById('tm-sl-price').value) || 0;
  var tpPrice = parseFloat(document.getElementById('tm-tp-price').value) || 0;

  var isLimit = _tmState.orderType === 'LIMIT';
  var limitPrice = isLimit ? (parseFloat(document.getElementById('tm-limit-price').value) || 0) : 0;

  if (usdtInput <= 0) { _tmShowError('請輸入數量'); return; }
  if (isLimit && limitPrice <= 0) { _tmShowError('請輸入限價'); return; }
  if (!isLimit && price <= 0) { _tmShowError('無法取得價格'); return; }

  // For limit orders, use limit price for qty calculation
  var calcPrice = isLimit ? limitPrice : price;
  if (calcPrice <= 0) { _tmShowError('無法取得價格'); return; }

  // SL/TP direction sanity check (skip for limit orders — SL/TP not set until filled)
  if (!isLimit) {
    if (_tmState.direction === 'LONG') {
      if (slPrice > 0 && slPrice >= price) { _tmShowError('LONG 止損應低於入場價 (' + fmtPrice(price) + ')'); return; }
      if (tpPrice > 0 && tpPrice <= price) { _tmShowError('LONG 止盈應高於入場價 (' + fmtPrice(price) + ')'); return; }
    } else {
      if (slPrice > 0 && slPrice <= price) { _tmShowError('SHORT 止損應高於入場價 (' + fmtPrice(price) + ')'); return; }
      if (tpPrice > 0 && tpPrice >= price) { _tmShowError('SHORT 止盈應低於入場價 (' + fmtPrice(price) + ')'); return; }
    }
  }

  var qty = usdtInput * leverage / calcPrice;
  if (qty <= 0) { _tmShowError('計算數量錯誤'); return; }

  // Frontend pre-validation using exchange symbol info
  var info = _tmState.symbolInfo;
  if (!info || !info.step_size) {
    _tmShowError('交易規則載入中，請稍候再試');
    _fetchSymbolInfo();
    return;
  }
  var step = info.step_size;
  var roundedQty = step > 0 ? Math.round(Math.round(qty / step) * step * 1e8) / 1e8 : qty;
  if (roundedQty <= 0) {
    var minUsdt = (info.min_qty || step) * calcPrice / leverage;
    _tmShowError('數量太小：' + qty.toFixed(8) + ' 經精度調整後為 0。最低需 ~$' + minUsdt.toFixed(2) + ' USDT');
    return;
  }
  if (info.min_qty && roundedQty < info.min_qty) {
    var minUsdt2 = info.min_qty * calcPrice / leverage;
    _tmShowError('數量 ' + roundedQty + ' 低於最低 ' + info.min_qty + '。最低需 ~$' + minUsdt2.toFixed(2) + ' USDT');
    return;
  }
  if (info.min_notional && usdtInput * leverage < info.min_notional) {
    _tmShowError('名義值 $' + (usdtInput * leverage).toFixed(2) + ' 低於最低 $' + info.min_notional);
    return;
  }
  // Use the rounded qty for the payload
  qty = roundedQty;

  var side = _tmState.direction === 'LONG' ? 'BUY' : 'SELL';
  var displayName = _tmState.symbol.replace('USDT', '');
  // R4 fix: escape for innerHTML safety
  var safeName = typeof esc === 'function' ? esc(displayName) : displayName.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  var platNames = { aster: 'Aster', binance: 'Binance', hyperliquid: 'HyperLiquid' };

  // Stage payload (qty already rounded by pre-validation above)
  _tmPendingPayload = {
    symbol: _tmState.symbol,
    platform: platform,
    side: side,
    qty: parseFloat(qty.toFixed(8)),
    leverage: leverage,
    sl_price: slPrice > 0 ? slPrice : null,
    tp_price: tpPrice > 0 ? tpPrice : null,
    order_type: _tmState.orderType,
    limit_price: isLimit ? limitPrice : null,
  };

  // Build confirmation overlay content
  var row = function(label, val) {
    return '<div class="tm-confirm-row"><span class="tm-confirm-label">' + label + '</span><span class="tm-confirm-val">' + val + '</span></div>';
  };
  var isLong = _tmState.direction === 'LONG';
  var dirTag = isLong
    ? '<span style="background:#0d9488;color:#fff;padding:2px 10px;border-radius:4px;font-size:.78rem;font-weight:600">LONG</span>'
    : '<span style="background:#e11d48;color:#fff;padding:2px 10px;border-radius:4px;font-size:.78rem;font-weight:600">SHORT</span>';

  var html =
    row('交易所', platNames[platform] || platform) +
    row('幣種', '<span style="font-size:.9rem">' + safeName + '</span>') +
    row('方向', dirTag) +
    row('類型', isLimit ? '限價 $' + fmtPrice(limitPrice) : '市價') +
    row('數量', qty.toFixed(6) + ' ' + safeName + ' <span style="color:#64748b;font-size:.72rem">(~$' + (usdtInput * leverage).toFixed(2) + ')</span>') +
    row('保證金', '$' + usdtInput.toFixed(2)) +
    row('槓桿', leverage + 'x');
  if (!isLimit && slPrice > 0) html += row('止損', '$' + fmtPrice(slPrice));
  if (!isLimit && tpPrice > 0) html += row('止盈', '$' + fmtPrice(tpPrice));
  if (isLimit) {
    html += '<div class="tm-confirm-warn"><i class="fas fa-info-circle mr-1"></i>限價單掛單後未成交，SL/TP 需成交後手動設定</div>';
  } else {
    html += '<div class="tm-confirm-warn"><i class="fas fa-info-circle mr-1"></i>市價單，實際成交價可能因滑點而與現價有偏差</div>';
  }

  document.getElementById('tm-confirm-body').innerHTML = html;

  // Style confirm button by direction
  var goBtn = document.getElementById('tm-confirm-go');
  goBtn.className = 'tm-confirm-go ' + (isLong ? 'long-bg' : 'short-bg');
  goBtn.innerHTML = '<i class="fas fa-check mr-1"></i>確定 ' + _tmState.direction;
  goBtn.disabled = false;

  // R1 fix: freeze form beneath overlay
  var modalBody = document.querySelector('.trade-modal-body');
  var modalFooter = document.querySelector('.tm-footer');
  if (modalBody) modalBody.classList.add('frozen');
  if (modalFooter) modalFooter.classList.add('frozen');

  // Show overlay
  document.getElementById('tm-confirm-overlay').classList.add('show');
}

function _tmUnfreezeForm() {
  var modalBody = document.querySelector('.trade-modal-body');
  var modalFooter = document.querySelector('.tm-footer');
  if (modalBody) modalBody.classList.remove('frozen');
  if (modalFooter) modalFooter.classList.remove('frozen');
}

function _tmConfirmCancel() {
  document.getElementById('tm-confirm-overlay').classList.remove('show');
  _tmUnfreezeForm();
  _tmPendingPayload = null;
}

function _tmConfirmExecute() {
  if (!_tmPendingPayload) return;
  var payload = _tmPendingPayload;
  // R3 fix: null payload immediately to prevent double-click
  _tmPendingPayload = null;

  // Loading state on confirm button
  var goBtn = document.getElementById('tm-confirm-go');
  goBtn.disabled = true;
  goBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>下單中…';

  var thisReqId = _tmState.requestId;

  fetch('/api/place-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(function(r) { return r.json().then(function(d) { return { status: r.status, data: d }; }); })
  .then(function(res) {
    if (thisReqId !== _tmState.requestId) return;
    if (res.data.ok) {
      // Hide overlay immediately
      document.getElementById('tm-confirm-overlay').classList.remove('show');
      _tmUnfreezeForm();
      // Show success + execution timing
      var submitBtn = document.getElementById('tm-submit-btn');
      submitBtn.disabled = true;
      var timing = res.data.timing;
      var isPending = res.data.pending;
      var entry = res.data.entry;
      var dn = payload.symbol.replace('USDT', '');
      var timeStr = timing ? ' (' + timing.fill_ms + 'ms 成交 / ' + timing.total_ms + 'ms 總計)' : '';

      if (isPending) {
        // Limit order — pending
        submitBtn.innerHTML = '<i class="fas fa-check mr-1"></i>掛單成功！' + timeStr;
        submitBtn.style.background = '#635bff';
        submitBtn.innerHTML += '<br><span style="font-size:.7rem;font-weight:400">限價 $' + fmtPrice(payload.limit_price) + ' × ' + payload.qty.toFixed(6) + '</span>';
      } else {
        // Market order — filled
        submitBtn.innerHTML = '<i class="fas fa-check mr-1"></i>下單成功！' + timeStr;
        submitBtn.style.background = '#0d9488';
        if (entry && entry.avgPrice > 0) {
          submitBtn.innerHTML += '<br><span style="font-size:.7rem;font-weight:400">成交價 $' + parseFloat(entry.avgPrice).toFixed(4) + ' × ' + parseFloat(entry.executedQty).toFixed(6) + '</span>';
        }
      }
      var warnings = res.data.warnings;
      if (warnings && warnings.length) {
        _tmShowError(warnings.join('; '));
        document.getElementById('tm-error').style.background = '#fffbeb';
        document.getElementById('tm-error').style.color = '#92400e';
      }
      // Push notification with execution timing
      if (typeof pushNotif === 'function') {
        if (isPending) {
          pushNotif('trade',
            '掛單 ' + dn + ' ' + payload.side + ' @ $' + fmtPrice(payload.limit_price) + (timing ? ' (' + timing.total_ms + 'ms)' : ''),
            payload.qty.toFixed(6) + ' ' + dn + ' | 保證金 $' + (payload.limit_price * payload.qty / payload.leverage).toFixed(2)
          );
        } else {
          pushNotif('trade',
            '成交 ' + dn + ' ' + payload.side + (timing ? ' (' + timing.fill_ms + 'ms)' : ''),
            (entry && entry.avgPrice > 0 ? '$' + parseFloat(entry.avgPrice).toFixed(2) + ' × ' + parseFloat(entry.executedQty).toFixed(6) : '') +
            ' | 保證金 $' + (payload.qty * (entry && entry.avgPrice > 0 ? parseFloat(entry.avgPrice) : 1) / payload.leverage).toFixed(2)
          );
        }
      }
      // Refresh dashboard immediately (backend cache already invalidated)
      if (typeof fetchData === 'function') fetchData();
      setTimeout(function() {
        $('#trade-modal').modal('hide');
        submitBtn.style.background = '';
        if (typeof fetchData === 'function') fetchData();
      }, 2000);
    } else {
      goBtn.disabled = false;
      goBtn.innerHTML = '<i class="fas fa-check mr-1"></i>確定下單';
      // Restore payload so user can retry
      _tmPendingPayload = payload;
      // Show error in main modal, hide overlay so user sees it
      document.getElementById('tm-confirm-overlay').classList.remove('show');
      _tmUnfreezeForm();
      _tmShowError(res.data.error || '下單失敗');
    }
  })
  .catch(function(err) {
    if (thisReqId !== _tmState.requestId) return;
    goBtn.disabled = false;
    goBtn.innerHTML = '<i class="fas fa-check mr-1"></i>確定下單';
    _tmPendingPayload = payload;
    document.getElementById('tm-confirm-overlay').classList.remove('show');
    _tmUnfreezeForm();
    _tmShowError('網絡錯誤: ' + err.message);
  });
}

// Bind events after DOM ready
$(function() {
  // Qty input → update preview
  $('#tm-qty-input, #tm-sl-price, #tm-tp-price, #tm-leverage, #tm-limit-price').on('input change', function() {
    _updatePreview();
  });
});
