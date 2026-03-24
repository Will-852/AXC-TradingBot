// ══════════════════════════════════════════════════
//  Footprint IndexedDB Cache — persist flow/vol data across TF switches
//  DB: 'axc_fp_cache', Store: 'fpData'
//  Key: symbol:interval, Value: {footprintData snapshot + timestamp}
//  Auto-cleanup: entries > 24h on startup
// ══════════════════════════════════════════════════
// ⚠️ 2CHECK: IndexedDB 係 async，所有 API 都 return Promise。
//    caller 必須 handle race condition（例如 cache.get() 返嚟之前用戶又轉 TF）。

var FPCache = (function() {
  var DB_NAME = 'axc_fp_cache';
  var STORE_NAME = 'fpData';
  var DB_VERSION = 1;
  var MAX_AGE_MS = 24 * 60 * 60 * 1000;  // 24h TTL
  var _db = null;

  function _open() {
    if (_db) return Promise.resolve(_db);
    return new Promise(function(resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function(evt) {
        var db = evt.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'cacheKey' });
        }
      };
      req.onsuccess = function(evt) {
        _db = evt.target.result;
        resolve(_db);
      };
      req.onblocked = function() {
        console.warn('[FPCache] IndexedDB blocked by another tab');
        reject(new Error('blocked'));
      };
      req.onerror = function(evt) {
        console.warn('[FPCache] IndexedDB open failed:', evt.target.error);
        reject(evt.target.error);
      };
    });
  }

  // ⚠️ 2CHECK: cacheKey 格式必須同 get/put 一致：symbol + ':' + interval
  function _makeKey(symbol, interval) {
    return symbol + ':' + interval;
  }

  /**
   * Get cached footprint data for symbol:interval.
   * Returns null if not found or expired.
   */
  function get(symbol, interval) {
    return _open().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(STORE_NAME, 'readonly');
        var store = tx.objectStore(STORE_NAME);
        var req = store.get(_makeKey(symbol, interval));
        req.onsuccess = function() {
          var result = req.result;
          if (!result) { resolve(null); return; }
          // Check TTL
          if (Date.now() - result.savedAt > MAX_AGE_MS) {
            resolve(null);  // expired — caller will re-fetch
            return;
          }
          resolve({ data: result.data, ageMs: Date.now() - result.savedAt });
        };
        req.onerror = function() { resolve(null); };  // fail-open: treat as cache miss
      });
    }).catch(function() { return null; });  // IndexedDB unavailable → cache miss
  }

  /**
   * Save footprint data snapshot.
   * ⚠️ 2CHECK: footprintData 入面有 object reference — 必須 deep copy 避免後續 mutation 污染 cache。
   *    用 JSON parse/stringify 做 deep copy（structuredClone 唔 support 所有 browser）。
   */
  function put(symbol, interval, fpData) {
    // Deep copy to avoid reference mutation
    var snapshot;
    try {
      snapshot = JSON.parse(JSON.stringify({
        delta_volume: fpData.delta_volume || {},
        large_trades: fpData.large_trades || [],
        volume_profile: fpData.volume_profile || [],
        heatmap: fpData.heatmap || {},
        cvd: fpData.cvd || {}
      }));
    } catch(e) {
      console.warn('[FPCache] JSON snapshot failed:', e);
      return Promise.resolve();
    }

    return _open().then(function(db) {
      return new Promise(function(resolve) {
        var tx = db.transaction(STORE_NAME, 'readwrite');
        var store = tx.objectStore(STORE_NAME);
        store.put({
          cacheKey: _makeKey(symbol, interval),
          data: snapshot,
          savedAt: Date.now()
        });
        tx.oncomplete = function() {
          console.log('[FPCache] Saved', _makeKey(symbol, interval),
            '— delta:', Object.keys(snapshot.delta_volume).length,
            'vp:', snapshot.volume_profile.length);
          resolve();
        };
        tx.onerror = function() { resolve(); };  // fail silently
      });
    }).catch(function() {});  // IndexedDB unavailable → skip
  }

  /**
   * Cleanup entries older than MAX_AGE_MS. Call on startup.
   */
  function cleanup() {
    return _open().then(function(db) {
      return new Promise(function(resolve) {
        var tx = db.transaction(STORE_NAME, 'readwrite');
        var store = tx.objectStore(STORE_NAME);
        var req = store.openCursor();
        var deleted = 0;
        req.onsuccess = function(evt) {
          var cursor = evt.target.result;
          if (!cursor) {
            if (deleted > 0) console.log('[FPCache] Cleaned', deleted, 'expired entries');
            resolve();
            return;
          }
          if (Date.now() - cursor.value.savedAt > MAX_AGE_MS) {
            cursor.delete();
            deleted++;
          }
          cursor.continue();
        };
        req.onerror = function() { resolve(); };
      });
    }).catch(function() {});
  }

  return { get: get, put: put, cleanup: cleanup };
})();
