/**
 * API Client Module
 * HTTP client com retry logic, timeout, deduplicação e cache 60s
 */

const APIClient = (function () {
  const CACHE = {};
  const PENDING = {};
  const CACHE_TTL = (typeof CONFIG !== 'undefined' && CONFIG.STORAGE && CONFIG.STORAGE.CACHE_TTL)
    ? CONFIG.STORAGE.CACHE_TTL
    : 60000;
  const API_TIMEOUT = (typeof CONFIG !== 'undefined' && CONFIG.TIMEOUTS && CONFIG.TIMEOUTS.API_CALL)
    ? CONFIG.TIMEOUTS.API_CALL
    : 10000;
  const MAX_RETRIES = 3;

  /**
   * Gera chave de cache a partir de url + opções
   */
  function _cacheKey(url, options) {
    return url + JSON.stringify(options || {});
  }

  /**
   * Verifica se cache ainda é válida
   */
  function _isCacheValid(entry) {
    return entry && (Date.now() - entry.ts) < CACHE_TTL;
  }

  /**
   * Fetch com timeout via AbortController
   */
  function _fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(function () {
      controller.abort();
    }, timeoutMs || API_TIMEOUT);

    const opts = Object.assign({}, options, { signal: controller.signal });

    return fetch(url, opts).finally(function () {
      clearTimeout(timer);
    });
  }

  /**
   * Fetch com retry exponencial
   * @param {string} url
   * @param {object} options - fetch options
   * @param {number} attempt - tentativa atual (interno)
   */
  function _fetchWithRetry(url, options, attempt) {
    attempt = attempt || 1;
    const start = Date.now();

    return _fetchWithTimeout(url, options).then(function (response) {
      const duration = Date.now() - start;

      if (typeof Logger !== 'undefined') {
        Logger.apiCall(url, (options && options.method) || 'GET', duration, response.status);
      }

      if (!response.ok) {
        // Não faz retry em 401/403/404 — são erros definitivos
        if ([401, 403, 404].indexOf(response.status) !== -1) {
          return Promise.reject(new Error('HTTP ' + response.status + ': ' + url));
        }
        throw new Error('HTTP ' + response.status);
      }

      return response.json();

    }).catch(function (err) {
      const isAbort = err.name === 'AbortError';
      const isDefinitive = err.message && (
        err.message.indexOf('HTTP 401') !== -1 ||
        err.message.indexOf('HTTP 403') !== -1 ||
        err.message.indexOf('HTTP 404') !== -1
      );

      if (isDefinitive) {
        return Promise.reject(err);
      }

      if (attempt < MAX_RETRIES && !isAbort) {
        const delay = Math.pow(2, attempt - 1) * 1000; // 1s, 2s, 4s
        if (typeof Logger !== 'undefined') {
          Logger.warn('APIClient', 'Retry ' + attempt + '/' + MAX_RETRIES + ' para: ' + url + ' (delay: ' + delay + 'ms)');
        }
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve(_fetchWithRetry(url, options, attempt + 1));
          }, delay);
        });
      }

      if (typeof Logger !== 'undefined') {
        Logger.error('APIClient', 'Falha definitiva após ' + attempt + ' tentativas: ' + url, err);
      }
      return Promise.reject(err);
    });
  }

  /**
   * GET com cache e deduplicação
   * @param {string} url
   * @param {object} options - fetch options adicionais
   * @param {boolean} bypassCache - forçar nova chamada
   */
  function get(url, options, bypassCache) {
    const key = _cacheKey(url, options);

    // Cache hit
    if (!bypassCache && _isCacheValid(CACHE[key])) {
      if (typeof Logger !== 'undefined') {
        Logger.info('APIClient', 'Cache hit: ' + url);
      }
      return Promise.resolve(CACHE[key].data);
    }

    // Deduplicação — se já há pedido igual em curso, reutiliza
    if (PENDING[key]) {
      if (typeof Logger !== 'undefined') {
        Logger.info('APIClient', 'Pedido duplicado deduplicated: ' + url);
      }
      return PENDING[key];
    }

    const fetchOptions = Object.assign({ method: 'GET' }, options || {});

    PENDING[key] = _fetchWithRetry(url, fetchOptions).then(function (data) {
      CACHE[key] = { data: data, ts: Date.now() };
      delete PENDING[key];
      return data;
    }).catch(function (err) {
      delete PENDING[key];
      return Promise.reject(err);
    });

    return PENDING[key];
  }

  /**
   * POST sem cache
   * @param {string} url
   * @param {object} body
   * @param {object} headers
   */
  function post(url, body, headers) {
    const options = {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
      body: JSON.stringify(body),
    };
    return _fetchWithRetry(url, options);
  }

  /**
   * PUT sem cache
   */
  function put(url, body, headers) {
    const options = {
      method: 'PUT',
      headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
      body: JSON.stringify(body),
    };
    return _fetchWithRetry(url, options);
  }

  /**
   * Invalida cache para um URL específico
   */
  function invalidateCache(url) {
    Object.keys(CACHE).forEach(function (k) {
      if (k.indexOf(url) === 0) delete CACHE[k];
    });
  }

  /**
   * Limpa toda a cache
   */
  function clearCache() {
    Object.keys(CACHE).forEach(function (k) { delete CACHE[k]; });
  }

  /**
   * Fetch paralelo com controlo de concorrência
   * @param {Array} requests - array de { url, options }
   * @param {number} concurrency - máximo em paralelo (default 4)
   */
  function batchGet(requests, concurrency) {
    concurrency = concurrency || 4;
    const results = [];
    let index = 0;

    function next() {
      if (index >= requests.length) return Promise.resolve();
      const req = requests[index++];
      return get(req.url, req.options).then(function (data) {
        results.push({ url: req.url, data: data, error: null });
        return next();
      }).catch(function (err) {
        results.push({ url: req.url, data: null, error: err.message });
        return next();
      });
    }

    const workers = [];
    for (let i = 0; i < Math.min(concurrency, requests.length); i++) {
      workers.push(next());
    }

    return Promise.all(workers).then(function () { return results; });
  }

  return {
    get,
    post,
    put,
    invalidateCache,
    clearCache,
    batchGet,
  };
})();
