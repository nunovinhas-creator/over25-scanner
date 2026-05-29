/**
 * Validators Module
 * Input validation, sanitization e XSS prevention
 */

const Validators = (function () {

  // ─── Sanitização ────────────────────────────────────────────────

  /**
   * Escapa HTML para prevenir XSS
   */
  function sanitizeHtml(str) {
    if (typeof str !== 'string') return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .replace(/\//g, '&#x2F;');
  }

  /**
   * Remove espaços e caracteres invisíveis
   */
  function trim(str) {
    if (typeof str !== 'string') return '';
    return str.trim().replace(/\s+/g, ' ');
  }

  // ─── Validação de Credenciais ────────────────────────────────────

  /**
   * Valida token GitHub (ghp_... ou github_pat_...)
   */
  function isValidGithubToken(token) {
    if (!token || typeof token !== 'string') return false;
    const t = token.trim();
    return (
      (t.startsWith('ghp_') && t.length >= 36) ||
      (t.startsWith('github_pat_') && t.length >= 50) ||
      (t.startsWith('gho_') && t.length >= 36)
    );
  }

  /**
   * Valida token Telegram Bot (123456:ABC-DEF...)
   */
  function isValidTelegramToken(token) {
    if (!token || typeof token !== 'string') return false;
    return /^\d{8,12}:[A-Za-z0-9_-]{35,}$/.test(token.trim());
  }

  /**
   * Valida Chat ID Telegram (número positivo ou negativo)
   */
  function isValidTelegramChatId(chatId) {
    if (chatId === null || chatId === undefined) return false;
    const str = String(chatId).trim();
    return /^-?\d{5,15}$/.test(str);
  }

  /**
   * Valida API Key BSD (string não vazia com comprimento mínimo)
   */
  function isValidBsdApiKey(key) {
    if (!key || typeof key !== 'string') return false;
    const k = key.trim();
    return k.length >= 20 && k.length <= 200;
  }

  /**
   * Valida endereço de email
   */
  function isValidEmail(email) {
    if (!email || typeof email !== 'string') return false;
    return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email.trim());
  }

  // ─── Validação de Dados de Apostas ──────────────────────────────

  /**
   * Valida odds (número entre 1.01 e 100)
   */
  function isValidOdds(odds) {
    const n = parseFloat(odds);
    return !isNaN(n) && n >= 1.01 && n <= 100;
  }

  /**
   * Valida xG (número entre 0 e 15)
   */
  function isValidXG(xg) {
    const n = parseFloat(xg);
    return !isNaN(n) && n >= 0 && n <= 15;
  }

  /**
   * Valida resultado (ex: "2-1", "0-0", "3-2")
   */
  function isValidScore(score) {
    if (!score || typeof score !== 'string') return false;
    return /^\d{1,2}-\d{1,2}$/.test(score.trim());
  }

  /**
   * Valida nome de liga (string não vazia, máx 100 chars)
   */
  function isValidLeague(league) {
    if (!league || typeof league !== 'string') return false;
    const l = league.trim();
    return l.length >= 2 && l.length <= 100;
  }

  /**
   * Valida data ISO (YYYY-MM-DD ou ISO completo)
   */
  function isValidDate(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr);
    return !isNaN(d.getTime());
  }

  /**
   * Valida percentagem (0-100)
   */
  function isValidPercent(value) {
    const n = parseFloat(value);
    return !isNaN(n) && n >= 0 && n <= 100;
  }

  // ─── Validação de Pick Completo ──────────────────────────────────

  /**
   * Valida um pick completo antes de guardar
   * @param {object} pick
   * @returns {{ valid: boolean, errors: string[] }}
   */
  function validatePick(pick) {
    const errors = [];

    if (!pick || typeof pick !== 'object') {
      return { valid: false, errors: ['Pick inválido ou nulo'] };
    }

    if (!pick.home || typeof pick.home !== 'string' || pick.home.trim().length < 2) {
      errors.push('Nome da equipa casa inválido');
    }
    if (!pick.away || typeof pick.away !== 'string' || pick.away.trim().length < 2) {
      errors.push('Nome da equipa fora inválido');
    }
    if (!pick.league || !isValidLeague(pick.league)) {
      errors.push('Liga inválida');
    }
    if (!pick.date || !isValidDate(pick.date)) {
      errors.push('Data inválida');
    }
    if (pick.odds !== undefined && !isValidOdds(pick.odds)) {
      errors.push('Odds inválidas: ' + pick.odds);
    }
    if (pick.xg !== undefined && !isValidXG(pick.xg)) {
      errors.push('xG inválido: ' + pick.xg);
    }
    if (pick.result !== undefined && pick.result !== null && !isValidScore(pick.result)) {
      errors.push('Resultado inválido: ' + pick.result);
    }

    return {
      valid: errors.length === 0,
      errors: errors,
    };
  }

  /**
   * Valida configuração completa do utilizador
   * @param {object} cfg
   * @returns {{ valid: boolean, errors: string[] }}
   */
  function validateConfig(cfg) {
    const errors = [];

    if (!cfg || typeof cfg !== 'object') {
      return { valid: false, errors: ['Configuração inválida'] };
    }
    if (cfg.ghToken && !isValidGithubToken(cfg.ghToken)) {
      errors.push('Token GitHub inválido');
    }
    if (cfg.tgToken && !isValidTelegramToken(cfg.tgToken)) {
      errors.push('Token Telegram inválido');
    }
    if (cfg.tgChatId && !isValidTelegramChatId(cfg.tgChatId)) {
      errors.push('Chat ID Telegram inválido');
    }
    if (cfg.bsdKey && !isValidBsdApiKey(cfg.bsdKey)) {
      errors.push('API Key BSD inválida');
    }

    return {
      valid: errors.length === 0,
      errors: errors,
    };
  }

  // ─── Normalização ────────────────────────────────────────────────

  /**
   * Normaliza nome de equipa para comparação
   * Remove acentos, lowercase, trim
   */
  function normalizeTeamName(name) {
    if (!name || typeof name !== 'string') return '';
    return name
      .trim()
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s]/g, '')
      .replace(/\s+/g, ' ');
  }

  /**
   * Gera ID único para um pick (para deduplicação)
   * @param {object} pick
   */
  function normId(pick) {
    if (!pick) return null;
    const home = normalizeTeamName(pick.home || pick.homeTeam || '');
    const away = normalizeTeamName(pick.away || pick.awayTeam || '');
    const date = (pick.date || pick.commence_time || '').slice(0, 10);
    return home + '|' + away + '|' + date;
  }

  return {
    sanitizeHtml,
    trim,
    isValidGithubToken,
    isValidTelegramToken,
    isValidTelegramChatId,
    isValidBsdApiKey,
    isValidEmail,
    isValidOdds,
    isValidXG,
    isValidScore,
    isValidLeague,
    isValidDate,
    isValidPercent,
    validatePick,
    validateConfig,
    normalizeTeamName,
    normId,
  };
})();
