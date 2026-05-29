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
    return
