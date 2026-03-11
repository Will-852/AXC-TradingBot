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

  // Set direction to LONG → internally calls _updateSltpFromPlan → _updatePreview
  _setDirection('LONG');

  // Clear error
  _tmHideError();

  // Reset submit button
  var btn = document.getElementById('tm-submit-btn');
  btn.disabled = false;
  btn.classList.remove('loading');
  btn.innerHTML = '<i class="fas fa-check mr-1"></i>確認下單';

  // Fetch balances
  _fetchBalances();

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

  // Estimated qty
  var qty = price > 0 ? usdtInput * leverage / price : 0;
  var estEl = document.getElementById('tm-est-pos');
  var displayName = _tmState.symbol.replace('USDT', '');
  estEl.textContent = qty > 0 ? '預計倉位: ' + qty.toFixed(6) + ' ' + displayName : '';

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

function submitTradeOrder() {
  _tmHideError();

  var platform = _getSelectedPlatform();
  if (!platform) { _tmShowError('請選擇交易所'); return; }

  var price = _getEntryPrice();
  var leverage = parseInt(document.getElementById('tm-leverage').value) || 5;
  var usdtInput = parseFloat(document.getElementById('tm-qty-input').value) || 0;
  var slPrice = parseFloat(document.getElementById('tm-sl-price').value) || 0;
  var tpPrice = parseFloat(document.getElementById('tm-tp-price').value) || 0;

  if (usdtInput <= 0) { _tmShowError('請輸入數量'); return; }
  if (price <= 0) { _tmShowError('無法取得價格'); return; }

  // SL/TP direction sanity check
  if (_tmState.direction === 'LONG') {
    if (slPrice > 0 && slPrice >= price) { _tmShowError('LONG 止損應低於入場價 (' + fmtPrice(price) + ')'); return; }
    if (tpPrice > 0 && tpPrice <= price) { _tmShowError('LONG 止盈應高於入場價 (' + fmtPrice(price) + ')'); return; }
  } else {
    if (slPrice > 0 && slPrice <= price) { _tmShowError('SHORT 止損應高於入場價 (' + fmtPrice(price) + ')'); return; }
    if (tpPrice > 0 && tpPrice >= price) { _tmShowError('SHORT 止盈應低於入場價 (' + fmtPrice(price) + ')'); return; }
  }

  var qty = usdtInput * leverage / price;
  if (qty <= 0) { _tmShowError('計算數量錯誤'); return; }

  var side = _tmState.direction === 'LONG' ? 'BUY' : 'SELL';
  var displayName = _tmState.symbol.replace('USDT', '');

  // Build confirmation summary
  var confirmLines = [
    '確認下單？\n',
    '交易所: ' + platform.toUpperCase(),
    '幣種: ' + displayName,
    '方向: ' + _tmState.direction + ' (' + side + ')',
    '類型: 市價',
    '數量: ' + qty.toFixed(6) + ' ' + displayName + ' (~$' + (usdtInput * leverage).toFixed(2) + ')',
    '保證金: $' + usdtInput.toFixed(2),
    '槓桿: ' + leverage + 'x',
  ];
  if (slPrice > 0) confirmLines.push('止損: $' + slPrice);
  if (tpPrice > 0) confirmLines.push('止盈: $' + tpPrice);

  if (!confirm(confirmLines.join('\n'))) return;

  var payload = {
    symbol: _tmState.symbol,
    platform: platform,
    side: side,
    qty: parseFloat(qty.toFixed(6)),
    leverage: leverage,
    sl_price: slPrice > 0 ? slPrice : null,
    tp_price: tpPrice > 0 ? tpPrice : null,
  };

  // Loading state
  var btn = document.getElementById('tm-submit-btn');
  btn.disabled = true;
  btn.classList.add('loading');
  btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>下單中…';

  var thisReqId = _tmState.requestId;

  fetch('/api/place-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(function(r) { return r.json().then(function(d) { return { status: r.status, data: d }; }); })
  .then(function(res) {
    if (thisReqId !== _tmState.requestId) return;  // stale callback
    btn.classList.remove('loading');
    if (res.data.ok) {
      btn.innerHTML = '<i class="fas fa-check mr-1"></i>下單成功！';
      btn.style.background = '#0d9488';
      var warnings = res.data.warnings;
      if (warnings && warnings.length) {
        _tmShowError(warnings.join('; '));
        document.getElementById('tm-error').style.background = '#fffbeb';
        document.getElementById('tm-error').style.color = '#92400e';
      }
      setTimeout(function() {
        $('#trade-modal').modal('hide');
        btn.style.background = '';
        if (typeof fetchData === 'function') fetchData();
      }, 1500);
    } else {
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-check mr-1"></i>確認下單';
      _tmShowError(res.data.error || '下單失敗');
    }
  })
  .catch(function(err) {
    if (thisReqId !== _tmState.requestId) return;  // stale callback
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.innerHTML = '<i class="fas fa-check mr-1"></i>確認下單';
    _tmShowError('網絡錯誤: ' + err.message);
  });
}

// Bind events after DOM ready
$(function() {
  // Qty input → update preview
  $('#tm-qty-input, #tm-sl-price, #tm-tp-price, #tm-leverage').on('input change', function() {
    _updatePreview();
  });
});
