"""
data/schema/picks_btts_schema.py
-----------------------------------
Schema formal MÍNIMO para picks_btts_over25.json (BTTS+Over 2.5) — sessão
data-quality-fixes, Ponto 3.

Não existia validação pandera formal para este ficheiro antes desta sessão.
`pipeline/scan_over25.py::_compute_btts_over25()` mais o merge com `ev`/`prob`/
`lineup_info` produzem muitos campos (ver CLAUDE.md, secção "BTTS+Over 2.5 —
Componentes"); este schema cobre os campos core de identificação/resultado
mais os 3 novos campos de scoreline. `strict=False` tolera o resto sem os
rejeitar — não é uma validação exaustiva de todos os campos.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

logger = logging.getLogger(__name__)


PicksBttsSchema = DataFrameSchema(
    columns={
        "id": Column(str, nullable=False, description="Composite pick id, e.g. '209508_btts'."),
        "casa": Column(str, nullable=True, description="Home team name."),
        "fora": Column(str, nullable=True, description="Away team name."),
        "liga": Column(str, nullable=True, description="League name (never empty — see UNKNOWN_LEAGUE)."),
        "data": Column(str, nullable=True, description="Kick-off datetime, ISO-8601."),
        "odds_btts": Column(str, nullable=True, description="BTTS yes odds used for the pick (string float)."),
        "clv_btts_over25": Column(str, nullable=True, description="Real CLV vs market (p_dc_conjunta/p_naive_market - 1)."),
        "resultado_btts_over25": Column(
            str,
            checks=[Check.isin(["WIN", "LOSS", "VOID", ""], error="resultado_btts_over25 must be WIN/LOSS/VOID or empty")],
            nullable=True,
            description="Settlement result (not yet auto-populated — see backlog).",
        ),
        "scanned_at": Column(str, nullable=True, description="ISO-8601 timestamp of the scan that created this pick."),
        "fonte": Column(str, nullable=True, description="Pick origin, e.g. 'auto-scan-btts'."),
        "data_quality_flag": Column(object, nullable=True, description="Non-empty/non-null → excluded from KPIs."),
        # ---- Scoreline no momento do alerta (Ponto 3) — retrocompatível: -----
        # ausente em picks antigos → NaN após validate_picks_btts().
        "score_no_alerta": Column(str, nullable=True, description="Scoreline at alert time, e.g. '0-0'."),
        "minuto_no_alerta": Column(str, nullable=True, description="Match minute at alert time (string int), null pre-KO."),
        "origem_alerta": Column(
            str,
            checks=[Check.isin(["pre-ko", "live", ""], error="origem_alerta must be 'pre-ko', 'live' or empty")],
            nullable=True,
            description="Alert origin — always 'pre-ko' today (BTTS gate runs inside the Over 2.5 pre-KO scan).",
        ),
    },
    coerce=True,
    strict=False,
    name="PicksBttsSchema",
)


def validate_picks_btts(picks: list[dict]) -> pd.DataFrame:
    """
    Load picks_btts_over25.json raw dicts, validate against PicksBttsSchema,
    and return a clean DataFrame. Rows failing schema checks are dropped with
    a warning (lazy validation — mirrors data.schema.picks_schema.validate_picks).

    Missing columns (old picks without the newer fields) are filled with NaN
    before validation — dashboards/scripts never crash on legacy records.
    """
    if not picks:
        logger.warning("validate_picks_btts: received empty picks list")
        return pd.DataFrame()

    df = pd.DataFrame(picks)
    for col in PicksBttsSchema.columns:
        if col not in df.columns:
            df[col] = np.nan

    str_cols = df.select_dtypes(include="object").columns
    for col in str_cols:
        df[col] = df[col].astype(str).where(df[col].notna(), np.nan)
    df.replace("", np.nan, inplace=True)

    try:
        df = PicksBttsSchema.validate(df, lazy=True)
        logger.info("validate_picks_btts: all %d records passed schema validation", len(df))
    except pa.errors.SchemaErrors as exc:
        failure_cases = exc.failure_cases
        bad_idx = (
            set(failure_cases["index"].dropna().astype(int).tolist())
            if "index" in failure_cases.columns else set()
        )
        if bad_idx:
            logger.warning(
                "validate_picks_btts: dropping %d records that failed schema checks", len(bad_idx)
            )
            df = df[~df.index.isin(bad_idx)].copy()
        else:
            logger.warning(
                "validate_picks_btts: schema errors detected but could not isolate bad rows "
                "— proceeding with full DataFrame. Error: %s", str(exc)[:500]
            )

    return df
