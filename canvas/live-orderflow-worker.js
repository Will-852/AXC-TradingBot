/**
 * live-orderflow-worker.js — Binance aggTrade WebSocket aggregator
 *
 * Web Worker: runs in separate thread, no DOM access.
 * Connects to Binance futures aggTrade stream, aggregates orderflow
 * in real-time (delta volume, heatmap, CVD, volume profile, large trades).
 *
 * Design decisions:
 * - ES5 for broad compatibility (var, function, no arrow/const/let)
 * - Flush cycle 1s for delta/heatmap/cvd, 5s for volume profile
 * - Exponential backoff reconnect (1s -> 30s cap)
 * - Candle boundary detection from trade timestamps, not wall clock
 * - Price bucketing mirrors fetch_agg_trades.py logic exactly
 */

// --- Constants ---

var INTERVAL_MS_MAP = {
  '1m': 60000, '3m': 180000, '5m': 300000, '15m': 900000,
  '30m': 1800000, '1h': 3600000, '2h': 7200000, '4h': 14400000,
  '6h': 21600000, '8h': 28800000, '12h': 43200000,
  '1d': 86400000, '1w': 604800000
};

var FOOTPRINT_IMBALANCE_RATIO = 3.0;
var VP_FLUSH_INTERVAL = 5;
var MAX_HISTORY = 500;

// --- State ---

var ws = null;
var symbol = '';
var interval = '1h';
var intervalMs = 3600000;
var bucketSize = 50;

var currentCandleTs = 0;
var deltaAccum = newDelta();
var heatmapAccum = {};
var cvdRunning = 0;

var deltaHistory = {};
var heatmapHistory = {};
var cvdHistory = {};
var vpAccum = {};

var largeTradeThreshold = 100000;
var reconnectDelay = 1000;
var reconnectTimer = null;
var flushTimer = null;
var vpCounter = 0;

// --- Helpers ---

function newDelta() {
  return { buy_vol: 0, sell_vol: 0, buy_usd: 0, sell_usd: 0 };
}

function priceBucket(price, size) {
  if (size < 1) {
    return Math.round(price / size) * size;
  }
  return Math.floor(price / size) * size;
}

function heatmapToArray(accum) {
  var keys = Object.keys(accum);
  keys.sort(function (a, b) { return +a - +b; });
  var arr = [];
  for (var i = 0; i < keys.length; i++) {
    arr.push([+keys[i], accum[keys[i]]]);
  }
  return arr;
}

function trimHistory(obj) {
  var keys = Object.keys(obj);
  if (keys.length <= MAX_HISTORY) return;
  keys.sort(function (a, b) { return +a - +b; });
  var excess = keys.length - MAX_HISTORY;
  for (var i = 0; i < excess; i++) {
    delete obj[keys[i]];
  }
}

function ensureBucket(accum, key) {
  if (!accum[key]) {
    accum[key] = { buy_vol: 0, sell_vol: 0 };
  }
  return accum[key];
}

// --- Candle boundary + finalize ---

function finalizePreviousCandle(prevTs) {
  deltaHistory[prevTs] = {
    buy_vol: deltaAccum.buy_vol,
    sell_vol: deltaAccum.sell_vol,
    buy_usd: deltaAccum.buy_usd,
    sell_usd: deltaAccum.sell_usd
  };
  heatmapHistory[prevTs] = heatmapToArray(heatmapAccum);
  var candleDelta = deltaAccum.buy_usd - deltaAccum.sell_usd;
  cvdRunning += candleDelta;
  cvdHistory[prevTs] = { delta: candleDelta, cvd: cvdRunning };
  self.postMessage({ type: 'candle_close', ts: prevTs });
  trimHistory(deltaHistory);
  trimHistory(heatmapHistory);
  trimHistory(cvdHistory);
}

// --- Trade processing ---

function processTrade(msg) {
  var price = +msg.p;
  var qty = +msg.q;
  var usd = price * qty;
  var tradeTs = msg.T;
  // msg.m === true  -> buyer is maker -> SELL aggressor
  // msg.m === false -> seller is maker -> BUY aggressor
  var isSell = msg.m === true;
  var candleTs = Math.floor(tradeTs / intervalMs) * intervalMs;
  if (candleTs !== currentCandleTs) {
    if (currentCandleTs > 0) {
      finalizePreviousCandle(currentCandleTs);
    }
    currentCandleTs = candleTs;
    deltaAccum = newDelta();
    heatmapAccum = {};
  }

  if (isSell) {
    deltaAccum.sell_vol += qty;
    deltaAccum.sell_usd += usd;
  } else {
    deltaAccum.buy_vol += qty;
    deltaAccum.buy_usd += usd;
  }

  var bucket = priceBucket(price, bucketSize);
  var hb = ensureBucket(heatmapAccum, bucket);
  if (isSell) {
    hb.sell_vol += qty;
  } else {
    hb.buy_vol += qty;
  }

  var vpb = ensureBucket(vpAccum, bucket);
  if (isSell) {
    vpb.sell_vol += qty;
  } else {
    vpb.buy_vol += qty;
  }

  if (usd >= largeTradeThreshold) {
    self.postMessage({
      type: 'large_trade',
      data: {
        timestamp: tradeTs,
        price: price,
        qty: qty,
        usd_value: usd,
        side: isSell ? 'SELL' : 'BUY'
      }
    });
  }
}

// --- Flush cycle (1s interval) ---

function flush() {
  if (currentCandleTs === 0) return;

  var ts = currentCandleTs;
  self.postMessage({
    type: 'delta',
    ts: ts,
    data: {
      buy_vol: deltaAccum.buy_vol,
      sell_vol: deltaAccum.sell_vol,
      buy_usd: deltaAccum.buy_usd,
      sell_usd: deltaAccum.sell_usd
    }
  });
  self.postMessage({
    type: 'heatmap',
    ts: ts,
    data: heatmapToArray(heatmapAccum)
  });
  var currentDelta = deltaAccum.buy_usd - deltaAccum.sell_usd;
  self.postMessage({
    type: 'cvd',
    ts: ts,
    data: {
      delta: currentDelta,
      cvd: cvdRunning + currentDelta
    }
  });
  vpCounter++;
  if (vpCounter >= VP_FLUSH_INTERVAL) {
    vpCounter = 0;
    self.postMessage({
      type: 'vp',
      data: heatmapToArray(vpAccum)
    });
  }
}

// --- WebSocket management ---

function connect() {
  if (ws) {
    try { ws.close(); } catch (e) { /* ignore */ }
    ws = null;
  }

  var url = 'wss://fstream.binance.com/ws/' + symbol.toLowerCase() + '@aggTrade';
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
      if (msg.e === 'aggTrade') {
        processTrade(msg);
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
  interval = cfg.interval || '1h';
  intervalMs = INTERVAL_MS_MAP[interval] || 3600000;
  bucketSize = cfg.bucketSize || 50;

  currentCandleTs = 0;
  deltaAccum = newDelta();
  heatmapAccum = {};
  cvdRunning = 0;
  deltaHistory = {};
  heatmapHistory = {};
  cvdHistory = {};
  vpAccum = {};
  vpCounter = 0;
  reconnectDelay = 1000;

  connect();
  flushTimer = setInterval(flush, 1000);
}

function stop() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (flushTimer) {
    clearInterval(flushTimer);
    flushTimer = null;
  }
  if (ws) {
    try { ws.onclose = null; ws.close(); } catch (e) { /* ignore */ }
    ws = null;
  }

  symbol = '';
  currentCandleTs = 0;
  deltaAccum = newDelta();
  heatmapAccum = {};
  cvdRunning = 0;
  deltaHistory = {};
  heatmapHistory = {};
  cvdHistory = {};
  vpAccum = {};
  vpCounter = 0;

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
      if (typeof msg.largeTradeThreshold === 'number') {
        largeTradeThreshold = msg.largeTradeThreshold;
      }
      if (typeof msg.bucketSize === 'number') {
        bucketSize = msg.bucketSize;
      }
      break;
  }
};
