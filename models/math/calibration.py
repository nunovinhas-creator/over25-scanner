"""
Probability calibration for ML Over/Under 2.5 predictions.

Calibration corrects systematic over- or under-confidence in raw model
probabilities. This is critical for betting: an over-confident model (e.g.
outputting 75% when the true rate is 50%) leads to Kelly over-staking.

Implemented calibrators:
  - PlattScaler      : logistic regression on log-odds (fast, few parameters)
  - IsotonicCalibrator: non-parametric isotonic regression (flexible)
  - TemperatureScaler: single-parameter temperature scaling for neural nets
  - BetaCalibrator   : beta distribution MLE (handles s-curve mis-calibration)
  - EnsembleCalibrator: weighted average of the above

Usage with picks from the scanner::

    from models.math.calibration import calibrate_from_picks, reliability_data

    calibrator = calibrate_from_picks(picks)
    calibrated_prob = calibrator.predict([0.72])[0]

References:
  Platt, J. (1999). "Probabilistic outputs for SVMs and comparisons to
  regularized likelihood methods." In Advances in Large Margin Classifiers.

  Niculescu-Mizil, A. & Caruana, R. (2005). "Predicting good probabilities
  with supervised learning." ICML-2005.

  Guo, C. et al. (2017). "On calibration of modern neural networks." ICML-2017.
"""

from __future__ import annotations

import math
import warnings
from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit as _logit_fn
from scipy.stats import beta as beta_dist
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.utils.validation import check_is_fitted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIN_PROB = 1e-6
_MAX_PROB = 1.0 - 1e-6
_MIN_SAMPLES_FOR_ISOTONIC = 30


def _safe_logit(p: np.ndarray) -> np.ndarray:
    """Numerically safe logit transform."""
    p = np.clip(np.asarray(p, dtype=np.float64), _MIN_PROB, _MAX_PROB)
    return np.log(p / (1.0 - p))


def _safe_sigmoid(x: np.ndarray) -> np.ndarray:
    return np.clip(expit(np.asarray(x, dtype=np.float64)), _MIN_PROB, _MAX_PROB)


def _validate_inputs(probs: np.ndarray, outcomes: Optional[np.ndarray] = None) -> None:
    if np.any(np.isnan(probs)) or np.any(np.isinf(probs)):
        raise ValueError("probs contains NaN or Inf values.")
    if np.any(probs < 0) or np.any(probs > 1):
        raise ValueError("probs must all be in [0, 1].")
    if outcomes is not None:
        unique = set(np.unique(outcomes).tolist())
        if not unique.issubset({0, 1, 0.0, 1.0}):
            raise ValueError(f"outcomes must be binary (0/1), got: {unique}")


# ---------------------------------------------------------------------------
# PlattScaler
# ---------------------------------------------------------------------------

class PlattScaler:
    """
    Platt scaling: fits a logistic regression on the log-odds of the raw
    probabilities to learn a linear re-mapping.

    f(p) = sigmoid(A * logit(p) + B)

    where A and B are fitted to maximise likelihood on the calibration set.

    This corrects linear over-/under-confidence but not non-linear distortions.
    """

    def __init__(self) -> None:
        self._model: Optional[LogisticRegression] = None
        self._is_fitted: bool = False

    def fit(self, probs: np.ndarray, outcomes: np.ndarray) -> "PlattScaler":
        """
        Fit the Platt scaler.

        Parameters
        ----------
        probs : array-like of float, shape (n,)
            Raw model probabilities in [0, 1].
        outcomes : array-like of int, shape (n,)
            Binary outcomes: 1 = Over 2.5 hit, 0 = miss.

        Returns
        -------
        self
        """
        probs = np.asarray(probs, dtype=np.float64)
        outcomes = np.asarray(outcomes, dtype=np.float64)
        _validate_inputs(probs, outcomes)

        n = len(probs)
        if n < 2:
            raise ValueError("Need at least 2 samples to fit PlattScaler.")

        X = _safe_logit(probs).reshape(-1, 1)
        y = outcomes.astype(int)

        self._model = LogisticRegression(
            solver="lbfgs",
            max_iter=1000,
            C=1e6,  # effectively no regularisation
            fit_intercept=True,
            random_state=0,
        )
        self._model.fit(X, y)
        self._is_fitted = True
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        """
        Apply Platt scaling to raw probabilities.

        Parameters
        ----------
        probs : array-like of float

        Returns
        -------
        np.ndarray of calibrated probabilities in (0, 1).
        """
        if not self._is_fitted or self._model is None:
            raise RuntimeError("PlattScaler must be fitted before calling predict().")
        probs = np.asarray(probs, dtype=np.float64)
        _validate_inputs(probs)
        X = _safe_logit(probs).reshape(-1, 1)
        cal = self._model.predict_proba(X)[:, 1]
        return np.clip(cal, _MIN_PROB, _MAX_PROB)

    @property
    def coef_(self) -> Optional[Tuple[float, float]]:
        """Returns (A, B) logistic regression coefficients, or None."""
        if self._model is None:
            return None
        A = float(self._model.coef_[0][0])
        B = float(self._model.intercept_[0])
        return A, B


# ---------------------------------------------------------------------------
# IsotonicCalibrator
# ---------------------------------------------------------------------------

class IsotonicCalibrator:
    """
    Isotonic regression calibrator.

    Non-parametric, monotone calibration — it learns a step function mapping
    raw probabilities to calibrated ones.  More flexible than Platt but
    requires more data (≥ 30 samples recommended; falls back to Platt
    otherwise).

    Uses sklearn's IsotonicRegression with out-of-bounds='clip'.
    """

    def __init__(self) -> None:
        self._iso: Optional[IsotonicRegression] = None
        self._fallback: Optional[PlattScaler] = None
        self._is_fitted: bool = False
        self._used_fallback: bool = False

    def fit(self, probs: np.ndarray, outcomes: np.ndarray) -> "IsotonicCalibrator":
        """
        Fit isotonic regression calibrator.

        Parameters
        ----------
        probs : array-like of float
        outcomes : array-like of int (0/1)

        Returns
        -------
        self
        """
        probs = np.asarray(probs, dtype=np.float64)
        outcomes = np.asarray(outcomes, dtype=np.float64)
        _validate_inputs(probs, outcomes)

        n = len(probs)
        if n < _MIN_SAMPLES_FOR_ISOTONIC:
            warnings.warn(
                f"Only {n} samples — isotonic regression is unreliable with < "
                f"{_MIN_SAMPLES_FOR_ISOTONIC} samples. Falling back to PlattScaler.",
                UserWarning,
                stacklevel=2,
            )
            self._fallback = PlattScaler().fit(probs, outcomes)
            self._used_fallback = True
            self._is_fitted = True
            return self

        self._iso = IsotonicRegression(out_of_bounds="clip", increasing=True)
        self._iso.fit(probs, outcomes)
        self._used_fallback = False
        self._is_fitted = True
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        """
        Apply isotonic calibration.

        Parameters
        ----------
        probs : array-like of float

        Returns
        -------
        np.ndarray of calibrated probabilities.
        """
        if not self._is_fitted:
            raise RuntimeError("IsotonicCalibrator must be fitted before predict().")
        probs = np.asarray(probs, dtype=np.float64)
        _validate_inputs(probs)

        if self._used_fallback and self._fallback is not None:
            return self._fallback.predict(probs)

        cal = self._iso.predict(probs)
        return np.clip(cal, _MIN_PROB, _MAX_PROB)


# ---------------------------------------------------------------------------
# TemperatureScaler
# ---------------------------------------------------------------------------

class TemperatureScaler:
    """
    Temperature scaling: divides logits by a single learned scalar T.

    calibrated_prob = sigmoid(logit / T)

    T > 1 softens probabilities (reduces overconfidence).
    T < 1 sharpens probabilities.
    T = 1 is the identity.

    Originally proposed for neural network calibration (Guo et al. 2017).
    Here it can be applied to any model producing log-odds (logits) or, if
    raw probabilities are provided, the internal logit is computed first.
    """

    def __init__(self) -> None:
        self.T: float = 1.0
        self._is_fitted: bool = False

    def fit(self, logits: np.ndarray, outcomes: np.ndarray) -> "TemperatureScaler":
        """
        Find the optimal temperature T by minimising NLL.

        If `logits` values are all in [0, 1] they are treated as probabilities
        and converted to logits internally.

        Parameters
        ----------
        logits : array-like of float
            Raw model logits (or probabilities — auto-detected).
        outcomes : array-like of int (0/1)

        Returns
        -------
        self
        """
        logits = np.asarray(logits, dtype=np.float64)
        outcomes = np.asarray(outcomes, dtype=np.float64)

        # Auto-detect if input is probabilities (all in [0,1]) or logits
        if np.all((logits >= 0.0) & (logits <= 1.0)):
            _validate_inputs(logits, outcomes)
            logits = _safe_logit(logits)
        else:
            if np.any(np.isnan(logits)) or np.any(np.isinf(logits)):
                raise ValueError("logits contains NaN or Inf.")

        n = len(logits)
        if n < 2:
            raise ValueError("Need at least 2 samples to fit TemperatureScaler.")

        def nll(log_T_arr: np.ndarray) -> float:
            T = math.exp(float(log_T_arr[0]))  # T > 0 always
            probs = _safe_sigmoid(logits / T)
            eps = 1e-9
            return -float(np.mean(
                outcomes * np.log(probs + eps) +
                (1.0 - outcomes) * np.log(1.0 - probs + eps)
            ))

        result = minimize(
            nll,
            x0=np.array([0.0]),   # log(T=1)
            method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-6},
        )
        self.T = float(math.exp(result.x[0]))
        self._is_fitted = True
        return self

    def predict(self, logits: np.ndarray) -> np.ndarray:
        """
        Apply temperature scaling.

        Parameters
        ----------
        logits : array-like of float
            Raw logits or probabilities (auto-detected).

        Returns
        -------
        np.ndarray of calibrated probabilities.
        """
        if not self._is_fitted:
            raise RuntimeError("TemperatureScaler must be fitted before predict().")
        logits = np.asarray(logits, dtype=np.float64)

        if np.all((logits >= 0.0) & (logits <= 1.0)):
            logits = _safe_logit(logits)

        cal = _safe_sigmoid(logits / self.T)
        return np.clip(cal, _MIN_PROB, _MAX_PROB)


# ---------------------------------------------------------------------------
# BetaCalibrator
# ---------------------------------------------------------------------------

class BetaCalibrator:
    """
    Beta calibration: fits a generalised beta distribution mapping.

    Kull et al. (2017) showed that beta calibration is superior to Platt
    when the calibration curve has an s-shape (common in tree-based models).

    The calibrated probability is:

        f(p) = sigmoid(a * log(p) - b * log(1-p) + c)

    Parameters a, b, c are fitted via maximum likelihood.

    Reference:
        Kull, M., Silva Filho, T., Flach, P. (2017). "Beta calibration: a
        well-founded and easily implemented improvement on logistic calibration
        for binary classifiers." AISTATS 2017.
    """

    def __init__(self) -> None:
        self.a_: float = 1.0
        self.b_: float = 1.0
        self.c_: float = 0.0
        self._is_fitted: bool = False

    def fit(self, probs: np.ndarray, outcomes: np.ndarray) -> "BetaCalibrator":
        """
        Fit beta calibration parameters via MLE.

        Parameters
        ----------
        probs : array-like of float, shape (n,)
        outcomes : array-like of int (0/1)

        Returns
        -------
        self
        """
        probs = np.asarray(probs, dtype=np.float64)
        outcomes = np.asarray(outcomes, dtype=np.float64)
        _validate_inputs(probs, outcomes)

        n = len(probs)
        if n < 4:
            raise ValueError("Need at least 4 samples to fit BetaCalibrator.")

        p_safe = np.clip(probs, _MIN_PROB, _MAX_PROB)
        log_p = np.log(p_safe)
        log_1mp = np.log(1.0 - p_safe)

        def neg_log_likelihood(params: np.ndarray) -> float:
            a, b, c = params
            logit_cal = a * log_p - b * log_1mp + c
            cal = _safe_sigmoid(logit_cal)
            eps = 1e-9
            return -float(np.mean(
                outcomes * np.log(cal + eps) +
                (1.0 - outcomes) * np.log(1.0 - cal + eps)
            ))

        result = minimize(
            neg_log_likelihood,
            x0=np.array([1.0, 1.0, 0.0]),
            method="Nelder-Mead",
            options={"maxiter": 3000, "xatol": 1e-7, "fatol": 1e-7},
        )
        self.a_, self.b_, self.c_ = float(result.x[0]), float(result.x[1]), float(result.x[2])
        self._is_fitted = True
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        """
        Apply beta calibration.

        Parameters
        ----------
        probs : array-like of float in [0, 1]

        Returns
        -------
        np.ndarray of calibrated probabilities.
        """
        if not self._is_fitted:
            raise RuntimeError("BetaCalibrator must be fitted before predict().")
        probs = np.asarray(probs, dtype=np.float64)
        _validate_inputs(probs)

        p_safe = np.clip(probs, _MIN_PROB, _MAX_PROB)
        logit_cal = self.a_ * np.log(p_safe) - self.b_ * np.log(1.0 - p_safe) + self.c_
        cal = _safe_sigmoid(logit_cal)
        return np.clip(cal, _MIN_PROB, _MAX_PROB)


# ---------------------------------------------------------------------------
# EnsembleCalibrator
# ---------------------------------------------------------------------------

class EnsembleCalibrator:
    """
    Ensemble calibrator: weighted average of Platt, Isotonic, and Beta.

    Weights are learned by optimising the combined NLL on the calibration set.
    If the dataset is small (< 30), only Platt scaling is used.

    Parameters
    ----------
    use_temperature : bool
        If True, also includes TemperatureScaler in the ensemble.
    """

    def __init__(self, use_temperature: bool = False) -> None:
        self.use_temperature = use_temperature
        self._platt = PlattScaler()
        self._isotonic = IsotonicCalibrator()
        self._beta = BetaCalibrator()
        self._temperature: Optional[TemperatureScaler] = (
            TemperatureScaler() if use_temperature else None
        )
        self._weights: np.ndarray = np.array([])
        self._is_fitted: bool = False
        self._small_sample: bool = False
        self._member_names: List[str] = []

    def fit(self, probs: np.ndarray, outcomes: np.ndarray) -> "EnsembleCalibrator":
        """
        Fit all sub-calibrators and learn their ensemble weights.

        For N < 30, only Platt scaling is used (isotonic is unreliable).

        Parameters
        ----------
        probs : array-like of float in [0, 1]
        outcomes : array-like of int (0/1)

        Returns
        -------
        self
        """
        probs = np.asarray(probs, dtype=np.float64)
        outcomes = np.asarray(outcomes, dtype=np.float64)
        _validate_inputs(probs, outcomes)

        n = len(probs)

        if n < _MIN_SAMPLES_FOR_ISOTONIC:
            # Small-sample fallback: Platt only
            warnings.warn(
                f"Only {n} calibration samples (< {_MIN_SAMPLES_FOR_ISOTONIC}). "
                "Using Platt scaling only.",
                UserWarning,
                stacklevel=2,
            )
            self._platt.fit(probs, outcomes)
            self._weights = np.array([1.0])
            self._member_names = ["platt"]
            self._small_sample = True
            self._is_fitted = True
            return self

        # Fit each calibrator
        self._platt.fit(probs, outcomes)
        self._isotonic.fit(probs, outcomes)

        beta_ok = True
        try:
            self._beta.fit(probs, outcomes)
        except Exception as e:
            warnings.warn(f"BetaCalibrator.fit failed: {e}. Excluding from ensemble.", UserWarning, stacklevel=2)
            beta_ok = False

        temp_ok = False
        if self.use_temperature and self._temperature is not None:
            try:
                self._temperature.fit(probs, outcomes)
                temp_ok = True
            except Exception as e:
                warnings.warn(f"TemperatureScaler.fit failed: {e}.", UserWarning, stacklevel=2)

        # Collect calibrated predictions from each member
        preds = [
            self._platt.predict(probs),
            self._isotonic.predict(probs),
        ]
        names = ["platt", "isotonic"]
        if beta_ok:
            preds.append(self._beta.predict(probs))
            names.append("beta")
        if temp_ok and self._temperature is not None:
            preds.append(self._temperature.predict(probs))
            names.append("temperature")

        preds = np.array(preds)  # (n_members, n_samples)
        self._member_names = names
        n_members = len(names)

        # Optimise weights to minimise NLL of the weighted ensemble
        def ensemble_nll(log_w: np.ndarray) -> float:
            w = np.exp(log_w)
            w /= w.sum()  # softmax → sum to 1
            blended = w @ preds  # (n_samples,)
            blended = np.clip(blended, _MIN_PROB, _MAX_PROB)
            eps = 1e-9
            return -float(np.mean(
                outcomes * np.log(blended + eps) +
                (1.0 - outcomes) * np.log(1.0 - blended + eps)
            ))

        result = minimize(
            ensemble_nll,
            x0=np.zeros(n_members),
            method="Nelder-Mead",
            options={"maxiter": 1000, "xatol": 1e-6},
        )
        raw_w = np.exp(result.x)
        self._weights = raw_w / raw_w.sum()
        self._small_sample = False
        self._is_fitted = True
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        """
        Apply ensemble calibration.

        Parameters
        ----------
        probs : array-like of float in [0, 1]

        Returns
        -------
        np.ndarray of calibrated probabilities.
        """
        if not self._is_fitted:
            raise RuntimeError("EnsembleCalibrator must be fitted before predict().")
        probs = np.asarray(probs, dtype=np.float64)
        _validate_inputs(probs)

        if self._small_sample:
            return self._platt.predict(probs)

        members: List[np.ndarray] = []
        for name in self._member_names:
            if name == "platt":
                members.append(self._platt.predict(probs))
            elif name == "isotonic":
                members.append(self._isotonic.predict(probs))
            elif name == "beta":
                members.append(self._beta.predict(probs))
            elif name == "temperature" and self._temperature is not None:
                members.append(self._temperature.predict(probs))

        preds = np.array(members)  # (n_members, n_samples)
        blended = self._weights @ preds
        return np.clip(blended, _MIN_PROB, _MAX_PROB)

    @property
    def weights(self) -> dict:
        """Return {member_name: weight} dict."""
        if not self._is_fitted:
            return {}
        return dict(zip(self._member_names, self._weights.tolist()))

    def __repr__(self) -> str:
        if not self._is_fitted:
            return "EnsembleCalibrator(not fitted)"
        w = self.weights
        w_str = ", ".join(f"{k}={v:.3f}" for k, v in w.items())
        return f"EnsembleCalibrator({w_str})"


# ---------------------------------------------------------------------------
# calibrate_from_picks
# ---------------------------------------------------------------------------

def calibrate_from_picks(picks: List[dict]) -> EnsembleCalibrator:
    """
    Build and fit an EnsembleCalibrator from a list of pick dicts.

    Accepts the scanner's native pick format (from picks.json / GAS Sheet),
    which contains fields like:
      - 'prob_over25' : float, 0–100 (percentage)  OR 0–1
      - 'result_over25' : 'WIN' | 'LOSS' | '' | None

    Only picks with a resolved result ('WIN' or 'LOSS') are used.

    Parameters
    ----------
    picks : list of dict
        Each dict should have 'prob_over25' and 'result_over25'.
        Field 'score_sistema' (0–100) is used as a fallback if
        'prob_over25' is missing.

    Returns
    -------
    EnsembleCalibrator
        Fitted calibrator.  If < 5 resolved picks exist, raises ValueError.

    Examples
    --------
    >>> picks = [
    ...     {'prob_over25': 72, 'result_over25': 'LOSS'},
    ...     {'prob_over25': 65, 'result_over25': 'WIN'},
    ... ]
    >>> cal = calibrate_from_picks(picks)
    >>> cal.predict(np.array([0.72]))
    array([0.54...])
    """
    resolved_probs: List[float] = []
    resolved_outcomes: List[int] = []

    for pick in picks:
        result = pick.get("result_over25", "") or ""
        if result.upper() not in ("WIN", "LOSS"):
            continue

        outcome = 1 if result.upper() == "WIN" else 0

        # Try prob_over25 first, fall back to score_sistema
        raw_prob = pick.get("prob_over25") or pick.get("score_sistema")
        if raw_prob is None:
            continue

        try:
            p = float(raw_prob)
        except (TypeError, ValueError):
            continue

        # Normalise from 0–100 range to 0–1 if needed
        if p > 1.0:
            p = p / 100.0

        if not (0.0 < p < 1.0):
            continue

        resolved_probs.append(p)
        resolved_outcomes.append(outcome)

    n = len(resolved_probs)
    if n < 5:
        raise ValueError(
            f"Only {n} picks with resolved results found. "
            "Need at least 5 to fit a calibrator."
        )

    probs_arr = np.array(resolved_probs, dtype=np.float64)
    outcomes_arr = np.array(resolved_outcomes, dtype=np.float64)

    calibrator = EnsembleCalibrator()
    calibrator.fit(probs_arr, outcomes_arr)
    return calibrator


# ---------------------------------------------------------------------------
# Reliability diagram data
# ---------------------------------------------------------------------------

def reliability_data(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute data for a reliability (calibration) diagram.

    Divides predicted probabilities into `n_bins` equal-width bins and
    computes the empirical win rate within each bin.

    Parameters
    ----------
    probs : array-like of float in [0, 1]
        Model's predicted probabilities.
    outcomes : array-like of int (0/1)
        Actual binary outcomes.
    n_bins : int
        Number of probability bins (default 10 → each bin is 10 percentage
        points wide).

    Returns
    -------
    bin_centers : np.ndarray, shape (n_bins,)
        Mid-point of each probability bin.
    bin_accs : np.ndarray, shape (n_bins,)
        Empirical accuracy (observed win rate) in each bin.
        Bins with 0 samples are returned as np.nan.
    bin_counts : np.ndarray, shape (n_bins,)
        Number of samples falling in each bin.

    Notes
    -----
    A perfectly calibrated model has bin_accs ≈ bin_centers for all bins.
    The Expected Calibration Error (ECE) is:

        ECE = sum_b (count_b / N) * |acc_b - center_b|

    Examples
    --------
    >>> probs = np.array([0.7, 0.75, 0.68, 0.72])
    >>> outcomes = np.array([0, 0, 1, 0])
    >>> centers, accs, counts = reliability_data(probs, outcomes, n_bins=5)
    """
    probs = np.asarray(probs, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)
    _validate_inputs(probs, outcomes)

    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length.")

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    bin_accs = np.full(n_bins, np.nan)
    bin_counts = np.zeros(n_bins, dtype=int)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)
        count = int(np.sum(mask))
        bin_counts[i] = count
        if count > 0:
            bin_accs[i] = float(np.mean(outcomes[mask]))

    return bin_centers, bin_accs, bin_counts


def expected_calibration_error(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Compute the Expected Calibration Error (ECE).

    ECE = sum_b (n_b / N) * |acc_b - conf_b|

    Parameters
    ----------
    probs : array-like of float in [0, 1]
    outcomes : array-like of int (0/1)
    n_bins : int

    Returns
    -------
    float in [0, 1]
    """
    probs = np.asarray(probs, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)

    centers, accs, counts = reliability_data(probs, outcomes, n_bins=n_bins)
    n = len(probs)
    ece = 0.0
    for i in range(n_bins):
        if counts[i] > 0 and not np.isnan(accs[i]):
            ece += (counts[i] / n) * abs(accs[i] - centers[i])
    return float(ece)
