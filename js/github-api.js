/**
 * GitHub API Module
 * Operações de leitura/escrita no repositório com gestão de SHA
 */

const GitHubAPI = (function () {
  const BASE = 'https://api.github.com';
  const REPO = (typeof CONFIG !== 'undefined' && CONFIG.API && CONFIG.API.REPO)
    ? CONFIG.API.REPO
    : 'nunovinhas-creator/over25-scanner';
  const TIMEOUT = (typeof CONFIG !== 'undefined' && CONFIG.TIMEOUTS && CONFIG.TIMEOUTS.GITHUB_CALL)
    ? CONFIG.TIMEOUTS.GITHUB_CALL
    : 15000;

  // Cache local de SHAs para evitar leituras desnecessárias
  const _shaCache = {};

  /**
   * Obtém token GitHub do storage ou localStorage legado
   */
  function _getToken() {
    if (typeof SecureStorage !== 'undefined') {
      const t = SecureStorage.getToken('gh_token');
      if (t) return t;
    }
    // Fallback para chave legada do index.html
    try {
      const cfg = JSON.parse(localStorage.getItem('ov_cfg') || '{}');
      return cfg.ghToken || cfg.gh_token || cfg.token || '';
    } catch (e) {
      return '';
    }
  }

  /**
   * Headers padrão para GitHub API
   */
  function _headers() {
    const token = _getToken();
    const h = {
      'Accept': 'application/vnd.github.v3+json',
      'Content-Type': 'application/json',
    };
    if (token) h['Authorization'] = 'token ' + token;
    return h;
  }

  /**
   * Fetch com timeout
   */
  function _fetch(url, options) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, TIMEOUT);
    const opts = Object.assign({}, options, {
      signal: controller.signal,
      headers: Object.assign(_headers(), (options && options.headers) || {}),
    });
    return fetch(url, opts).finally(function () { clearTimeout(timer); });
  }

  /**
   * Lê um ficheiro do repositório
   * @param {string} path - ex: 'data/picks.json'
   * @param {string} branch - default 'main'
   * @returns {Promise<{ content: any, sha: string }>}
   */
  function readFile(path, branch) {
    branch = branch || 'main';
    const url = BASE + '/repos/' + REPO + '/contents/' + path + '?ref=' + branch;

    if (typeof Logger !== 'undefined') {
      Logger.info('GitHubAPI', 'Lendo ficheiro: ' + path);
    }

    return _fetch(url).then(function (r) {
      if (r.status === 404) {
        return { content: null, sha: null };
      }
      if (!r.ok) throw new Error('GitHub read error: HTTP ' + r.status);
      return r.json();
    }).then(function (data) {
      if (!data || !data.content) return { content: null, sha: null };

      // Guarda SHA em cache
      _shaCache[path] = data.sha;

      // Descodifica base64
      let parsed = null;
      try {
        const decoded = atob(data.content.replace(/\n/g, ''));
        parsed = JSON.parse(decoded);
      } catch (e) {
        if (typeof Logger !== 'undefined') {
          Logger.warn('GitHubAPI', 'Falha ao parsear JSON de: ' + path, e);
        }
        parsed = null;
      }

      return { content: parsed, sha: data.sha };
    }).catch(function (err) {
      if (typeof Logger !== 'undefined') {
        Logger.error('GitHubAPI', 'Erro ao ler: ' + path, err);
      }
      return { content: null, sha: null };
    });
  }

  /**
   * Escreve um ficheiro no repositório
   * @param {string} path - ex: 'data/picks.json'
   * @param {any} content - objeto/array a serializar em JSON
   * @param {string} message - commit message
   * @param {string} branch - default 'main'
   * @returns {Promise<boolean>}
   */
  function writeFile(path, content, message, branch) {
    branch = branch || 'main';
    const url = BASE + '/repos/' + REPO + '/contents/' + path;
    const commitMsg = message || 'auto-update: ' + path;

    if (typeof Logger !== 'undefined') {
      Logger.info('GitHubAPI', 'Escrevendo ficheiro: ' + path);
    }

    // Obtém SHA actual (necessário para update)
    function _doWrite(sha) {
      const body = {
        message: commitMsg,
        content: btoa(unescape(encodeURIComponent(JSON.stringify(content, null, 2)))),
        branch: branch,
      };
      if (sha) body.sha = sha;

      return _fetch(url, {
        method: 'PUT',
        body: JSON.stringify(body),
      }).then(function (r) {
        if (!r.ok) {
          return r.json().then(function (err) {
            throw new Error('GitHub write error: ' + (err.message || 'HTTP ' + r.status));
          });
        }
        return r.json();
      }).then(function (data) {
        // Actualiza SHA em cache
        if (data && data.content && data.content.sha) {
          _shaCache[path] = data.content.sha;
        }
        if (typeof Logger !== 'undefined') {
          Logger.info('GitHubAPI', 'Ficheiro guardado: ' + path);
        }
        return true;
      }).catch(function (err) {
        if (typeof Logger !== 'undefined') {
          Logger.error('GitHubAPI', 'Erro ao escrever: ' + path, err);
        }
        return false;
      });
    }

    // Usa SHA em cache se disponível, senão lê primeiro
    if (_shaCache[path]) {
      return _doWrite(_shaCache[path]);
    }

    return readFile(path, branch).then(function (result) {
      return _doWrite(result.sha);
    });
  }

  /**
   * Lê picks.json
   */
  function readPicks(branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.PICKS
      : 'data/picks.json';
    return readFile(path, branch).then(function (r) {
      return Array.isArray(r.content) ? r.content : [];
    });
  }

  /**
   * Escreve picks.json
   */
  function writePicks(picks, branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.PICKS
      : 'data/picks.json';
    const count = Array.isArray(picks) ? picks.length : 0;
    return writeFile(path, picks, 'auto-sync picks: ' + count + ' resultados', branch);
  }

  /**
   * Lê picks_1x2.json
   */
  function readPicks1x2(branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.PICKS_1X2
      : 'data/picks_1x2.json';
    return readFile(path, branch).then(function (r) {
      return Array.isArray(r.content) ? r.content : [];
    });
  }

  /**
   * Escreve picks_1x2.json
   */
  function writePicks1x2(picks, branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.PICKS_1X2
      : 'data/picks_1x2.json';
    const count = Array.isArray(picks) ? picks.length : 0;
    return writeFile(path, picks, 'auto-sync 1x2: ' + count + ' resultados', branch);
  }

  /**
   * Lê observations.json
   */
  function readObservations(branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.OBS
      : 'data/observations.json';
    return readFile(path, branch).then(function (r) {
      return Array.isArray(r.content) ? r.content : [];
    });
  }

  /**
   * Escreve observations.json
   */
  function writeObservations(obs, branch) {
    const path = (typeof CONFIG !== 'undefined' && CONFIG.FILES)
      ? CONFIG.FILES.OBS
      : 'data/observations.json';
    const count = Array.isArray(obs) ? obs.length : 0;
    return writeFile(path, obs, 'auto-sync obs: ' + count + ' resultados', branch);
  }

  /**
   * Invalida SHA cache para forçar releitura
   */
  function invalidateSha(path) {
    if (path) {
      delete _shaCache[path];
    } else {
      Object.keys(_shaCache).forEach(function (k) { delete _shaCache[k]; });
    }
  }

  return {
    readFile,
    writeFile,
    readPicks,
    writePicks,
    readPicks1x2,
    writePicks1x2,
    readObservations,
    writeObservations,
    invalidateSha,
  };
})();
