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
