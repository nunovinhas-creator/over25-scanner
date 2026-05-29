/**
 * Logger Module
 * Request/response logging & performance tracking
 */

const Logger = (function () {
  const MAX_LOGS = 500;
  const STORAGE_KEY = 'ov_logs';

  let logs = [];

  function _timestamp() {
    return new Date().toISOString();
  }

  function _store() {
    try {
      const trimmed = logs.slice(-MAX_LOGS);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
    } catch (e) {
      // localStorage cheio — ignora
    }
  }

  function _load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      logs = raw ? JSON.parse(raw) : [];
    } catch (e) {
      logs = [];
    }
  }

  function info(module, message, data) {
    const entry = {
      level: 'INFO',
      ts: _timestamp(),
      module,
      message,
      data: data || null,
    };
    logs.push(entry);
    _store();
    if (typeof FEATURE_FLAGS !== 'undefined' && FEATURE_FLAGS.DEBUG_MODE) {
      console.info(`[${entry.ts}] [${module}] ${message}`, data || '');
    }
  }

  function warn(module, message, data) {
    const entry = {
      level: 'WARN',
      ts: _timestamp(),
      module,
      message,
      data: data || null,
    };
    logs.push(entry);
    _store();
    console.warn(`[${entry.ts}] [${module}] WARN: ${message}`, data || '');
  }

  function error(module, message, err) {
    const entry = {
      level: 'ERROR',
      ts: _timestamp(),
      module,
      message,
      error: err ? (err.message || String(err)) : null,
    };
    logs.push(entry);
    _store();
    console.error(`[${entry.ts}] [${module}] ERROR: ${message}`, err || '');
  }

  function apiCall(endpoint, method, durationMs, status) {
    const entry = {
      level: 'API',
      ts: _timestamp(),
      endpoint,
      method: method || 'GET',
      durationMs,
      status,
    };
    logs.push(entry);
    _store();
    if (typeof FEATURE_FLAGS !== 'undefined' && FEATURE_FLAGS.LOG_API_CALLS) {
      console.info(`[API] ${method} ${endpoint} → ${status} (${durationMs}ms)`);
    }
  }

  function getLogs(level) {
    _load();
    if (level) return logs.filter(function (l) { return l.level === level; });
    return logs.slice();
  }

  function clearLogs() {
    logs = [];
    localStorage.removeItem(STORAGE_KEY);
  }

  function exportLogs() {
    _load();
    const blob = new Blob([JSON.stringify(logs, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ov_logs_' + new Date().toISOString().slice(0, 10) + '.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  // Inicializa carregando logs existentes
  _load();

  return {
    info,
    warn,
    error,
    apiCall,
    getLogs,
    clearLogs,
    exportLogs,
  };
})();
