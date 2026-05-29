/**
 * State Manager Module
 * Global state com pub/sub subscriptions e persistência automática
 */

const StateManager = (function () {

  // ─── Estado inicial ──────────────────────────────────────────────

  const _state = {
    // Dados principais
    data: {
      picks: [],
      picks1x2: [],
      observations: [],
      games: [],
      liveData: [],
    },

    // Configuração do utilizador
    config: {
      ghToken: '',
      ghRepo: '',
      tgToken: '',
      tgChatId: '',
      bsdKey: '',
      autoLog: false,
      debug: false,
    },

    // Estado da UI
    ui: {
      activeTab: 'scanner',
      scanFilter: 'all',
      lgFilter: 'all',
      loading: false,
      lastSync: null,
      liveActive: false,
      liveTimer: null,
    },

    // Métricas da sessão
    session: {
      apiCallCount: 0,
      lastError: null,
      startTime: Date.now(),
    },
  };

  // ─── Subscriptions ───────────────────────────────────────────────

  const _subscribers = {};

  /**
   * Subscreve alterações a um caminho de estado
   * @param {string} path - ex: 'data.picks', 'ui.loading', 'config'
   * @param {function} callback - chamado com (newValue, oldValue, path)
   * @returns {function} unsubscribe function
   */
  function subscribe(path, callback) {
    if (!_subscribers[path]) _subscribers[path] = [];
    _subscribers[path].push(callback);

    // Retorna função para cancelar subscrição
    return function () {
      _subscribers[path] = _subscribers[path].filter(function (cb) {
        return cb !== callback;
      });
    };
  }

  /**
   * Notifica subscribers de um caminho e todos os caminhos pai
   */
  function _notify(path, newVal, oldVal) {
    // Notifica subscribers exactos
    if (_subscribers[path]) {
      _subscribers[path].forEach(function (cb) {
        try { cb(newVal, oldVal, path); } catch (e) {
          if (typeof Logger !== 'undefined') {
            Logger.error('StateManager', 'Erro em subscriber: ' + path, e);
          }
        }
      });
    }

    // Notifica subscribers do caminho pai (ex: 'data' quando 'data.picks' muda)
    const parts = path.split('.');
    if (parts.length > 1) {
      const parent = parts.slice(0, -1).join('.');
      if (_subscribers[parent]) {
        _subscribers[parent].forEach(function (cb) {
          try { cb(get(parent), null, path); } catch (e) {
            if (typeof Logger !== 'undefined') {
              Logger.error('StateManager', 'Erro em subscriber pai: ' + parent, e);
            }
          }
        });
      }
    }

    // Notifica subscribers globais '*'
    if (_subscribers['*']) {
      _subscribers['*'].forEach(function (cb) {
        try { cb(newVal, oldVal, path); } catch (e) {}
      });
    }
  }

  // ─── Navegação no estado por caminho ────────────────────────────

  /**
   * Resolve caminho 'data.picks' → objecto e chave final
   */
  function _resolve(path) {
    const parts = path.split('.');
    let obj = _state;
    for (let i = 0; i < parts.length - 1; i++) {
      if (obj[parts[i]] === undefined) return null;
      obj = obj[parts[i]];
    }
    return { obj: obj, key: parts[parts.length - 1] };
  }

  /**
   * Lê valor por caminho
   * @param {string} path - ex: 'data.picks'
   * @returns {*}
   */
  function get(path) {
    if (!path) return Object.assign({}, _state);
    const resolved = _resolve(path);
    if (!resolved) return undefined;
    const val = resolved.obj[resolved.key];
    // Retorna cópia para evitar mutações externas em arrays/objects
    if (Array.isArray(val)) return val.slice();
    if (val && typeof val === 'object') return Object.assign({}, val);
    return val;
  }

  /**
   * Define valor por caminho e notifica subscribers
   * @param {string} path - ex: 'data.picks'
   * @param {*} value
   * @param {boolean} silent - se true, não notifica subscribers
   */
  function set(path, value, silent) {
    const resolved = _resolve(path);
    if (!resolved) {
      if (typeof Logger !== 'undefined') {
        Logger.warn('StateManager', 'Caminho inválido: ' + path);
      }
      return false;
    }

    const oldVal = resolved.obj[resolved.key];
    resolved.obj[resolved.key] = value;

    if (typeof Logger !== 'undefined') {
      Logger.info('StateManager', 'Estado alterado: ' + path);
    }

    if (!silent) _notify(path, value, oldVal);
    return true;
  }

  /**
   * Faz merge de um objecto no estado (para actualizações parciais)
   * @param {string} path
   * @param {object} partial
   */
  function merge(path, partial) {
    const current = get(path);
    if (current && typeof current === 'object' && !Array.isArray(current)) {
      set(path, Object.assign({}, current, partial));
    } else {
      set(path, partial);
    }
  }

  // ─── Persistência ────────────────────────────────────────────────

  /**
   * Persiste configuração em localStorage
   */
  function saveConfig() {
    try {
      if (typeof SecureStorage !== 'undefined') {
        SecureStorage.set('config', _state.config, { encrypt: false });
      } else {
        localStorage.setItem('ov_cfg', JSON.stringify(_state.config));
      }
      if (typeof Logger !== 'undefined') {
        Logger.info('StateManager', 'Config persistida');
      }
      return true;
    } catch (e) {
      if (typeof Logger !== 'undefined') {
        Logger.error('StateManager', 'Erro ao persistir config', e);
      }
      return false;
    }
  }

  /**
   * Carrega configuração de localStorage
   */
  function loadConfig() {
    try {
      let cfg = null;

      if (typeof SecureStorage !== 'undefined') {
        cfg = SecureStorage.get('config');
      }

      // Fallback para chave legada
      if (!cfg) {
        const raw = localStorage.getItem('ov_cfg');
        if (raw) cfg = JSON.parse(raw);
      }

      if (cfg && typeof cfg === 'object') {
        _state.config = Object.assign(_state.config, cfg);
        if (typeof Logger !== 'undefined') {
          Logger.info('StateManager', 'Config carregada');
        }
      }

      return _state.config;
    } catch (e) {
      if (typeof Logger !== 'undefined') {
        Logger.error('StateManager', 'Erro ao carregar config', e);
      }
      return _state.config;
    }
  }

  // ─── Helpers de domínio ──────────────────────────────────────────

  /**
   * Adiciona um pick evitando duplicados (usa normId se disponível)
   * @param {string} type - 'picks' ou 'picks1x2'
   * @param {object} pick
   */
  function addPick(type, pick) {
    const path = 'data.' + (type || 'picks');
    const current = get(path) || [];

    // Deduplicação por normId
    let isDuplicate = false;
    if (typeof Validators !== 'undefined') {
      const newId = Validators.normId(pick);
      isDuplicate = current.some(function (p) {
        return Validators.normId(p) === newId;
      });
    } else {
      // Fallback simples
      isDuplicate = current.some(function (p) {
        return p.home === pick.home &&
               p.away === pick.away &&
               (p.date || '').slice(0, 10) === (pick.date || '').slice(0, 10);
      });
    }

    if (isDuplicate) {
      if (typeof Logger !== 'undefined') {
        Logger.warn('StateManager', 'Pick duplicado ignorado: ' + (pick.home || '') + ' vs ' + (pick.away || ''));
      }
      return false;
    }

    current.push(pick);
    set(path, current);
    return true;
  }

  /**
   * Incrementa contador de chamadas API
   */
  function incrementApiCalls() {
    _state.session.apiCallCount++;
  }

  /**
   * Regista último erro
   */
  function setLastError(err) {
    _state.session.lastError = {
      message: err ? (err.message || String(err)) : null,
      ts: Date.now(),
    };
  }

  /**
   * Retorna resumo da sessão actual
   */
  function getSessionSummary() {
    const uptime = Math.round((Date.now() - _state.session.startTime) / 1000);
    return {
      apiCalls: _state.session.apiCallCount,
      lastError: _state.session.lastError,
      uptimeSeconds: uptime,
      picksLoaded: (_state.data.picks || []).length,
      picks1x2Loaded: (_state.data.picks1x2 || []).length,
    };
  }

  /**
   * Reset completo do estado (mantém config)
   */
  function reset() {
    const savedConfig = Object.assign({}, _state.config);
    _state.data.picks = [];
    _state.data.picks1x2 = [];
    _state.data.observations = [];
    _state.data.games = [];
    _state.data.liveData = [];
    _state.ui.loading = false;
    _state.ui.liveActive = false;
    _state.session.apiCallCount = 0;
    _state.session.lastError = null;
    _state.config = savedConfig;
    _notify('*', _state, null);
    if (typeof Logger !== 'undefined') {
      Logger.info('StateManager', 'Estado resetado');
    }
  }

  // Inicialização — carrega config ao arrancar
  loadConfig();

  return {
    get,
    set,
    merge,
    subscribe,
    saveConfig,
    loadConfig,
    addPick,
    incrementApiCalls,
    setLastError,
    getSessionSummary,
    reset,
  };
})();
