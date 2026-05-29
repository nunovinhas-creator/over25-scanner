/**
 * Global Configuration & Constants
 * Centralized configuration for API endpoints, thresholds, and constants
 */

const CONFIG = {
  // API Endpoints
  API: {
    BSD_BASE: 'https://sports.bzzoiro.com',
    GH_API: 'https://api.github.com',
    TG_API: 'https://api.telegram.org',
    REPO: 'nunovinhas-creator/over25-scanner',
  },

  // File Paths
  FILES: {
    PICKS: 'data/picks.json',
    PICKS_1X2: 'data/picks_1x2.json',
    OBS: 'data/observations.json',
  },

  // Sharp Detection Thresholds
  SHARP: {
    TH_MOVE: 1.0,
    DIV_MIN: 2.0,
    DIV_STEAM: 8.0,
  },

  // Scoring Weights
  SCORE: {
    ML_PROBABILITY: 40,
    XG: 20,
    BTTS: 15,
    SHARP_MONEY: 15,
  },

  // Timeouts & Limits
  TIMEOUTS: {
    API_CALL: 10000,
    GITHUB_CALL: 15000,
  },

  // Pagination
  PAGINATION: {
    ODDS_LIMIT: 200,
    MAX_PAGES: 20,
  },

  // Storage
  STORAGE: {
    CONFIG_KEY: 'ov_cfg',
    CACHE_TTL: 60000,
  },
};

const FEATURE_FLAGS = {
  DEBUG_MODE: localStorage.getItem('debug') === 'true',
  LOG_API_CALLS: localStorage.getItem('log_api') === 'true',
};
