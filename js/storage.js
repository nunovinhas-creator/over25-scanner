/**
 * Storage Module
 * Secure localStorage abstraction with basic encryption and TTL support
 */

const SecureStorage = (function () {
  const PREFIX = 'ov_';

  // XOR cipher simples para ofuscar dados sensíveis
  function _xorEncrypt(text, key) {
    if (!key) return text;
    let result = '';
    for (let i = 0; i < text.length; i++) {
      result += String.fromCharCode(
        text.charCodeAt(i) ^ key.charCodeAt(i % key.length)
      );
    }
    return btoa(result);
  }

  function _xorDecrypt(encoded, key) {
    if (!key) return encoded;
    try {
      const text = atob(encoded);
      let result = '';
      for (let i = 0; i < text.length; i++) {
        result += String.fromCharCode(
          text.charCodeAt(i) ^ key.charCodeAt(i % key.length)
        );
      }
      return result;
    } catch (e) {
      return null;
    }
  }

  function _getKey() {
    // Usa o user-agent como seed da chave — não é criptografia forte,
    // apenas ofuscação para evitar leitura casual
    return (navigator.userAgent || 'ov_default').slice(0, 16);
  }

  /**
   * Guarda um valor com TTL opcional
   * @param {string} key
   * @param {*} value
   * @param {object} options - { ttl: ms, encrypt: bool }
   */
  function set(key, value, options) {
    const opts = options || {};
    const fullKey = PREFIX + key;
    const payload = {
      value: value,
      ts: Date.now(),
      ttl: opts.ttl || null,
    };

    try {
      let serialized = JSON.stringify(payload);
      if (opts.encrypt) {
        serialized = _xorEncrypt(serialized, _getKey());
        localStorage.setItem(fullKey, '__enc__' + serialized);
      } else {
        localStorage.setItem(fullKey, serialized);
      }
      return true;
    } catch (e) {
      if (typeof Logger !== 'undefined') {
        Logger.error('SecureStorage', 'Erro ao guardar: ' + key, e);
      }
      return false;
    }
  }

  /**
   * Lê um valor, respeitando TTL
   * @param {string} key
   * @returns {*} valor ou null se expirado/inexistente
   */
  function get(key) {
    const fullKey = PREFIX + key;
    try {
      let raw = localStorage.getItem(fullKey);
      if (!raw) return null;

      let payload;
      if (raw.startsWith('__enc__')) {
        const decrypted = _xorDecrypt(raw.slice(7), _getKey());
        if (!decrypted) return null;
        payload = JSON.parse(decrypted);
      } else {
        payload = JSON.parse(raw);
      }

      // Verifica TTL
      if (payload.ttl && (Date.now() - payload.ts) > payload.ttl) {
        remove(key);
        return null;
      }

      return payload.value;
    } catch (e) {
      if (typeof Logger !== 'undefined') {
        Logger.error('SecureStorage', 'Erro ao ler: ' + key, e);
      }
      return null;
    }
  }

  /**
   * Remove uma chave
   */
  function remove(key) {
    localStorage.removeItem(PREFIX + key);
  }

  /**
   * Guarda token de API com encriptação automática
   */
  function setToken(name, token) {
    return set(name, token, { encrypt: true });
  }

  /**
   * Lê token de API
   */
  function getToken(name) {
    return get(name);
  }

  /**
   * Lista todas as chaves do módulo
   */
  function listKeys() {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) {
        keys.push(k.slice(PREFIX.length));
      }
    }
    return keys;
  }

  /**
   * Limpa todos os dados do módulo
   */
  function clearAll() {
    const keys = listKeys();
    keys.forEach(function (k) { remove(k); });
  }

  return {
    set,
    get,
    remove,
    setToken,
    getToken,
    listKeys,
    clearAll,
  };
})();
