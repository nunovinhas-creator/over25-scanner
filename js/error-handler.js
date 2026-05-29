/**
 * Error Handler & User Notifications
 * Unified error handling, logging, and user-facing alerts
 */

const ErrorHandler = (function() {
  const ERROR_TYPES = {
    NETWORK: 'NETWORK_ERROR',
    VALIDATION: 'VALIDATION_ERROR',
    AUTH: 'AUTH_ERROR',
    GITHUB: 'GITHUB_ERROR',
    TELEGRAM: 'TELEGRAM_ERROR',
    STORAGE: 'STORAGE_ERROR',
    TIMEOUT: 'TIMEOUT_ERROR',
    UNKNOWN: 'UNKNOWN_ERROR',
  };

  const MESSAGES = {
    pt: {
      NETWORK_ERROR: 'Erro de rede. Verifica a tua ligação.',
      VALIDATION_ERROR: 'Dados inválidos. Verifica os campos.',
      AUTH_ERROR: 'Autenticação falhou. Verifica as credenciais.',
      GITHUB_ERROR: 'Erro GitHub: {detail}',
      TELEGRAM_ERROR: 'Erro ao enviar Telegram.',
      STORAGE_ERROR: 'Erro ao guardar dados localmente.',
      TIMEOUT_ERROR: 'Pedido expirou. Tenta de novo.',
      UNKNOWN_ERROR: 'Erro desconhecido: {detail}',
    },
  };

  const errorLog = [];

  function log(error, context = {}) {
    const entry = {
      timestamp: new Date().toISOString(),
      type: error.type || ERROR_TYPES.UNKNOWN,
      message: error.message,
      context,
      stack: error.stack,
    };
    errorLog.push(entry);
    if (errorLog.length > 100) errorLog.shift();
    if (FEATURE_FLAGS.DEBUG_MODE) console.error('[ErrorHandler]', entry);
  }

  function showToast(message, type = 'error', duration = 4000) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
      position: fixed; bottom: 20px; right: 20px; padding: 12px 16px;
      border-radius: 6px; font-size: 14px; z-index: 9999;
      animation: slideIn 0.3s ease;
      ${type === 'error' ? 'background: #e03050; color: white;' : ''}
      ${type === 'success' ? 'background: #0aaa53; color: white;' : ''}
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.style.animation = 'slideOut 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  function handle(error, userMessage = null, context = {}) {
    log(error, context);
    const msg = userMessage || MESSAGES.pt[error.type] || MESSAGES.pt.UNKNOWN_ERROR;
    if (typeof document !== 'undefined') showToast(msg, 'error');
    return { success: false, error: msg };
  }

  return {
    ERROR_TYPES,
    log,
    handle,
    showToast,
    getErrorLog: () => [...errorLog],
  };
})();
