"""
data/schema/picks_1x2_schema.py
---------------------------------
Schema formal MÍNIMO para picks_1x2.json (Sharp 1X2) — sessão
data-quality-fixes, Ponto 3.

Não existia validação pandera formal para este ficheiro antes desta sessão
(ao contrário de Over 2.5 — ver picks_schema.py). Este schema cobre os
campos core gravados por pipeline/scan_sharp1x2.py e pipeline/settle_sharp1x2.py
mais os 3 novos campos de scoreline (score_no_alerta, minuto_no_alerta,
origem_alerta). `strict=False` — como no schema Over 2.5 — tolera campos
extra (legacy: nvp, drop_pct, label, score, phase, odds_pinnacle, ...) sem
os rejeitar; não é uma validação exaustiva de todos os campos históricos.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

logger = logging.getLogger(__name__)


Picks1x2Schema = DataFrameSchema(
    columns={
        "id": Column(str, nullable=False, description="Composite pick id, e.g. '209508_home_sh'."),
        "casa": Column(str, nullable=True, description="Home team name."),
        "fora": Column(str, nullable=True, description="Away team name."),
        "liga": Column(str, nullable=True, description="League name (never empty — see UNKNOWN_LEAGUE)."),
        "data": Column(str, nullable=True, description="Kick-off datetime, ISO-8601."),
        "outcome": Column(
            str,
            checks=[Check.isin(["HOME", "DRAW", "AWAY", ""], error="outcome must be HOME/DRAW/AWAY or empty")],
            nullable=True,
            description="1X2 outcome this pick backs.",
        ),
        "odds_entrada": Column(str, nullable=True, description="Bet365 odds at pick time (string float)."),
        "odds_pinnacle": Column(str, nullable=True, description="Pinnacle odds at pick time (string float)."),
        "div_b365_pin": Column(str, nullable=True, description="Bet365 vs Pinnacle divergence % (string float)."),
        "timing_h": Column(str, nullable=True, description="Hours to KO at pick time (string float)."),
        "gate_blocked_reason": Column(str, nullable=True, description="Non-empty if pick failed an alert gate."),
        "resultado_outcome": Column(
            str,
            checks=[Check.isin(["WIN", "LOSS", "VOID", ""], error="resultado_outcome must be WIN/LOSS/VOID or empty")],
            nullable=True,
            description="Settlement result — set by pipeline/settle_sharp1x2.py.",
        ),
        "resultado_jogo": Column(str, nullable=True, description="Final scoreline, e.g. '2-1'."),
        "settlement_error": Column(str, nullable=True, description="Explicit settlement failure reason (never silent)."),
        "settlement_error_at": Column(str, nullable=True, description="ISO-8601 timestamp of settlement_error."),
        "odds_fecho": Column(str, nullable=True, description="Pinnacle closing odds (string float) — see update_closing_odds.py."),
        "clv": Column(str, nullable=True, description="Closing Line Value % (string float)."),
        "fetch_error": Column(str, nullable=True, description="Explicit odds_fecho fetch failure reason (never silent)."),
        "fetch_error_at": Column(str, nullable=True, description="ISO-8601 timestamp of fetch_error."),
        "saved_at": Column(str, nullable=True, description="ISO-8601 timestamp when the pick was first persisted."),
        "fonte": Column(str, nullable=True, description="Pick origin, e.g. 'auto-scan'."),
        "data_quality_flag": Column(object, nullable=True, description="Non-empty/non-null → excluded from KPIs."),
        # ---- Scoreline no momento do alerta (Ponto 3) — retrocompatível: -----
        # ausente em picks antigos → NaN após validate_picks_1x2().
        "score_no_alerta": Column(str, nullable=True, description="Scoreline at alert time, e.g. '0-0'."),
        "minuto_no_alerta": Column(str, nullable=True, description="Match minute at alert time (string int), null pre-KO."),
        "origem_alerta": Column(
            str,
            checks=[Check.isin(["pre-ko", "live", ""], error="origem_alerta must be 'pre-ko', 'live' or empty")],
            nullable=True,
            description="Alert origin — always 'pre-ko' today (Sharp 1X2 gate requires timing_h in [0, 6]).",
        ),
    },
    coerce=True,
    strict=False,
    name="Picks1x2Schema",
)


def validate_picks_1x2(picks: list[dict]) -> pd.DataFrame:
    """
    Load picks_1x2.json raw dicts, validate against Picks1x2Schema, and
    return a clean DataFrame. Rows failing schema checks are dropped with a
    warning (lazy validation — mirrors data.schema.picks_schema.validate_picks).

    Missing columns (old picks without the newer fields) are filled with NaN
    before validation — dashboards/scripts never crash on legacy records.
    """
    if not picks:
        logger.warning("validate_picks_1x2: received empty picks list")
        return pd.DataFrame()

    df = pd.DataFrame(picks)
    for col in Picks1x2Schema.columns:
        if col not in df.columns:
            df[col] = np.nan

    str_cols = df.select_dtypes(include="object").columns
    for col in str_cols:
        df[col] = df[col].astype(str).where(df[col].notna(), np.nan)
    df.replace("", np.nan, inplace=True)

    try:
        df = Picks1x2Schema.validate(df, lazy=True)
        logger.info("validate_picks_1x2: all %d records passed schema validation", len(df))
    except pa.errors.SchemaErrors as exc:
        failure_cases = exc.failure_cases
        bad_idx = (
            set(failure_cases["index"].dropna().astype(int).tolist())
            if "index" in failure_cases.columns else set()
        )
        if bad_idx:
            logger.warning(
                "validate_picks_1x2: dropping %d records that failed schema checks", len(bad_idx)
            )
            df = df[~df.index.isin(bad_idx)].copy()
        else:
            logger.warning(
                "validate_picks_1x2: schema errors detected but could not isolate bad rows "
                "— proceeding with full DataFrame. Error: %s", str(exc)[:500]
            )

    return df
