/**
 * depth-worker.js — Binance L2 order book depth WebSocket processor
 *
 * Web Worker: runs in separate thread, no DOM access.
 * Connects to Binance futures partial depth stream (top 20 levels, 100ms),
 * computes OBI at L1/L5/L10, total depth, and detects potential spoofing.
 *
 * ES5 only | 250ms flush | spoof detect < 3s | reconnect 1s->30s cap
 */

// --- Constants ---
var DEFAULT_THRESHOLDS = {
  BTCUSDT: 5,
  ETHUSDT: 50,
  SOLUSDT: 500,
  XRPUSDT: 50000,
  BNBUSDT: 100
};

var SPOOF_LIFETIME_MS = 3000;
var STALE_ORDER_MS = 30000;
var FLUSH_INTERVAL_MS = 250;

// --- State ---
var ws = null;
var symbol = '';
var largeOrderThreshold = 5;
var reconnectDelay = 1000;
var reconnectTimer = null;
var flushTimer = null;
var currentBids = [];   // [[price, qty], ...] parsed to numbers
var currentAsks = [];
var lastUpdateTs = 0;
var largeOrders = {};   // spoof tracking: key = side+'|'+price

// --- Helpers ---
function sumQty(levels, n) {
  var total = 0;
  var limit = Math.min(n, levels.length);
  for (var i = 0; i < limit; i++) {
    total += levels[i][1];
  }
  return total;
}

function calcOBI(bidSum, askSum) {
  var denom = bidSum + askSum;
  if (denom === 0) return 0;
  return (bidSum - askSum) / denom;
}

function parseLevels(raw) {
  var out = [];
  for (var i = 0; i < raw.length; i++) {
    out.push([parseFloat(raw[i][0]), parseFloat(raw[i][1])]);
  }
  return out;
}

// --- Spoofing Detection ---
function updateSpoofTracking(side, levels, now) {
  var currentKeys = {};
  for (var i = 0; i < levels.length; i++) {
    var price = levels[i][0];
    var qty = levels[i][1];
    if (qty >= largeOrderThreshold) {
      var key = side + '|' + price;
      currentKeys[key] = true;
      if (!largeOrders[key]) {
        largeOrders[key] = { qty: qty, firstSeen: now, lastSeen: now };
      } else {
        largeOrders[key].qty = qty;
        largeOrders[key].lastSeen = now;
      }
    }
  }

  var keys = Object.keys(largeOrders);
  for (var j = 0; j < keys.length; j++) {
    var k = keys[j];
    if (k.indexOf(side + '|') !== 0) continue;
    if (currentKeys[k]) continue;

    var order = largeOrders[k];
    var lifetime = order.lastSeen - order.firstSeen;

    if (lifetime < SPOOF_LIFETIME_MS && lifetime > 0) {
      var parts = k.split('|');
      self.postMessage({
        type: 'spoof',
        side: parts[0],
        price: parseFloat(parts[1]),
        qty: order.qty,
        lifetime_ms: lifetime,
        ts: now
      });
    }
    delete largeOrders[k];
  }
}

function cleanupStaleOrders(now) {
  var keys = Object.keys(largeOrders);
  for (var i = 0; i < keys.length; i++) {
    if (now - largeOrders[keys[i]].lastSeen > STALE_ORDER_MS) {
      delete largeOrders[keys[i]];
    }
  }
}

// --- Depth message processing ---
function processDepth(msg) {
  var now = Date.now();
  currentBids = parseLevels(msg.b || []);
  currentAsks = parseLevels(msg.a || []);
  lastUpdateTs = msg.E || now;

  updateSpoofTracking('BID', currentBids, now);
  updateSpoofTracking('ASK', currentAsks, now);
  cleanupStaleOrders(now);
}

// --- Flush cycle (250ms) ---
function flush() {
  if (currentBids.length === 0 && currentAsks.length === 0) return;

  var bestBid = currentBids.length > 0 ? currentBids[0][0] : 0;
  var bestAsk = currentAsks.length > 0 ? currentAsks[0][0] : 0;
  var spread = bestAsk > 0 && bestBid > 0 ? bestAsk - bestBid : 0;

  var bidL1 = sumQty(currentBids, 1);
  var askL1 = sumQty(currentAsks, 1);
  var bidL5 = sumQty(currentBids, 5);
  var askL5 = sumQty(currentAsks, 5);
  var bidL10 = sumQty(currentBids, 10);
  var askL10 = sumQty(currentAsks, 10);

  var bidDepth = sumQty(currentBids, currentBids.length);
  var askDepth = sumQty(currentAsks, currentAsks.length);

  self.postMessage({
    type: 'book',
    bestBid: bestBid,
    bestAsk: bestAsk,
    spread: Math.round(spread * 100) / 100,
    obi_l1: Math.round(calcOBI(bidL1, askL1) * 10000) / 10000,
    obi_l5: Math.round(calcOBI(bidL5, askL5) * 10000) / 10000,
    obi_l10: Math.round(calcOBI(bidL10, askL10) * 10000) / 10000,
    bidDepth: Math.round(bidDepth * 1000) / 1000,
    askDepth: Math.round(askDepth * 1000) / 1000,
    ts: lastUpdateTs
  });
}

// --- WebSocket management ---
function connect() {
  if (ws) {
    try { ws.close(); } catch (e) { /* ignore */ }
    ws = null;
  }

  var url = 'wss://fstream.binance.com/ws/' + symbol.toLowerCase() + '@depth20@100ms';
  self.postMessage({ type: 'status', status: 'reconnecting' });

  try {
    ws = new WebSocket(url);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = function () {
    reconnectDelay = 1000;
    self.postMessage({ type: 'status', status: 'connected' });
  };

  ws.onmessage = function (evt) {
    try {
      var msg = JSON.parse(evt.data);
      if (msg.b && msg.a) {
        processDepth(msg);
      }
    } catch (e) {
      // Parse error — skip malformed message
    }
  };

  ws.onerror = function () {};

  ws.onclose = function () {
    ws = null;
    self.postMessage({ type: 'status', status: 'disconnected' });
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(function () { reconnectTimer = null; connect(); }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

// --- Start / Stop / Config ---
function start(cfg) {
  stop();
  symbol = cfg.symbol || 'BTCUSDT';
  largeOrderThreshold = DEFAULT_THRESHOLDS[symbol] || 5;
  reconnectDelay = 1000;
  currentBids = [];
  currentAsks = [];
  lastUpdateTs = 0;
  largeOrders = {};
  connect();
  flushTimer = setInterval(flush, FLUSH_INTERVAL_MS);
}

function stop() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (flushTimer) { clearInterval(flushTimer); flushTimer = null; }
  if (ws) {
    try { ws.onclose = null; ws.close(); } catch (e) { /* ignore */ }
    ws = null;
  }
  symbol = '';
  currentBids = [];
  currentAsks = [];
  lastUpdateTs = 0;
  largeOrders = {};
  self.postMessage({ type: 'status', status: 'disconnected' });
}

// --- Message handler (main thread -> worker) ---
self.onmessage = function (evt) {
  var msg = evt.data;
  if (!msg || !msg.type) return;

  switch (msg.type) {
    case 'start':
      start(msg);
      break;
    case 'stop':
      stop();
      break;
    case 'config':
      if (typeof msg.largeOrderThreshold === 'number') {
        largeOrderThreshold = msg.largeOrderThreshold;
      }
      break;
  }
};
