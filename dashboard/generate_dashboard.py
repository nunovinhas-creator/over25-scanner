"""
generate_dashboard.py
---------------------
Generates a standalone HTML analytics dashboard for the Over 2.5 goals scanner.

Usage:
    python dashboard/generate_dashboard.py
    python dashboard/generate_dashboard.py --output dashboard/analytics.html
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Colour palette – matches scanner CSS
# ---------------------------------------------------------------------------
BG_DARK   = "#0A0A0C"
BG_CARD   = "#12121A"
BG_PANEL  = "#1A1A2E"
GOLD      = "#D4AF37"
GREEN     = "#39FF14"
RED       = "#FF3B3B"
TEXT_MAIN = "#E0E0E0"
TEXT_DIM  = "#888"
GRID_COL  = "#2A2A3A"

PLOTLY_DARK = dict(
    paper_bgcolor=BG_DARK,
    plot_bgcolor=BG_CARD,
    font=dict(color=TEXT_MAIN, family="'Segoe UI', Arial, sans-serif"),
    xaxis=dict(gridcolor=GRID_COL, zerolinecolor=GRID_COL),
    yaxis=dict(gridcolor=GRID_COL, zerolinecolor=GRID_COL),
    margin=dict(l=50, r=30, t=50, b=50),
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_picks_df(path: Optional[str] = None) -> pd.DataFrame:
    """
    Load data/picks.json, coerce numeric types, and filter to resolved picks
    (result_over25 in WIN / LOSS).
    """
    if path is None:
        # Walk up from this file to the repo root
        root = Path(__file__).resolve().parent.parent
        path = root / "data" / "picks.json"

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    df = pd.DataFrame(raw)

    # -- type coercions -------------------------------------------------------
    df["data"] = pd.to_datetime(df["data"], utc=True, errors="coerce")
    df["score_sistema"]  = pd.to_numeric(df["score_sistema"],  errors="coerce")
    df["prob_over25"]    = pd.to_numeric(df["prob_over25"],    errors="coerce")
    df["odds_over"]      = pd.to_numeric(df["odds_over"],      errors="coerce")
    df["odds_over_close"]= pd.to_numeric(df["odds_over_close"],errors="coerce")
    df["clv"]            = pd.to_numeric(df["clv"],            errors="coerce")
    df["xg_total"]       = pd.to_numeric(df["xg_total"],       errors="coerce")
    df["btts_prob"]      = pd.to_numeric(df["btts_prob"],       errors="coerce")

    # Normalise string columns
    df["result_over25"] = df["result_over25"].str.strip().str.upper()
    df["movimento"]     = df["movimento"].str.strip().str.upper()
    df["sharp_label"]   = df["sharp_label"].str.strip().str.upper()

    # -- filter to resolved picks only ----------------------------------------
    df = df[df["result_over25"].isin(["WIN", "LOSS"])].copy()

    # Derived columns
    df["win"] = (df["result_over25"] == "WIN").astype(int)
    df["profit_flat"] = np.where(
        df["win"] == 1,
        df["odds_over"] - 1,   # net profit on 1-unit stake
        -1.0,
    )
    df.sort_values("data", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df["cumprofit"] = df["profit_flat"].cumsum()

    # EV = prob * odds - 1
    df["ev"] = (df["prob_over25"] / 100.0) * df["odds_over"] - 1.0

    return df


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def plot_roi_curve(picks_df: pd.DataFrame) -> go.Figure:
    """
    Cumulative profit curve over time (flat 1-unit stake).
    SHORTENING picks are highlighted with gold markers.
    """
    df = picks_df.copy()
    df["label"] = df["casa"] + " v " + df["fora"]

    short_mask = df["movimento"] == "SHORTENING"

    fig = go.Figure()

    # Main ROI line
    fig.add_trace(go.Scatter(
        x=df["data"],
        y=df["cumprofit"],
        mode="lines",
        name="ROI Cumulativo",
        line=dict(color=GOLD, width=2),
        hovertemplate=(
            "<b>%{customdata}</b><br>"
            "Data: %{x|%d/%m %H:%M}<br>"
            "Lucro acumulado: %{y:+.2f}u<extra></extra>"
        ),
        customdata=df["label"],
    ))

    # SHORTENING highlights
    df_short = df[short_mask]
    if not df_short.empty:
        fig.add_trace(go.Scatter(
            x=df_short["data"],
            y=df_short["cumprofit"],
            mode="markers",
            name="SHORTENING",
            marker=dict(
                color=np.where(df_short["win"] == 1, GREEN, RED),
                size=9,
                symbol="diamond",
                line=dict(color=GOLD, width=1),
            ),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Resultado: %{customdata[1]}<br>"
                "Lucro acumulado: %{y:+.2f}u<extra></extra>"
            ),
            customdata=list(zip(df_short["label"], df_short["result_over25"])),
        ))

    # Breakeven line
    fig.add_hline(y=0, line_dash="dash", line_color=TEXT_DIM, line_width=1)

    fig.update_layout(
        **PLOTLY_DARK,
        title=dict(text="Curva ROI Cumulativa (stake plana 1u)", font=dict(color=GOLD, size=16)),
        xaxis_title="Data",
        yaxis_title="Lucro (unidades)",
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MAIN)),
        hovermode="x unified",
    )
    return fig


def plot_calibration(picks_df: pd.DataFrame) -> go.Figure:
    """
    Reliability diagram: predicted probability bands (x) vs actual win rate (y).
    Bubble size = number of picks in that band.
    Includes diagonal perfect-calibration reference line.
    """
    df = picks_df.copy()

    # Probability bands in 10-point bins
    bins   = list(range(0, 101, 10))
    labels = [f"{lo}-{lo+10}%" for lo in bins[:-1]]
    df["prob_band"] = pd.cut(df["prob_over25"], bins=bins, labels=labels, right=False)

    grouped = df.groupby("prob_band", observed=True).agg(
        n=("win", "count"),
        wr=("win", "mean"),
        mid_prob=("prob_over25", "mean"),
    ).reset_index()
    grouped = grouped[grouped["n"] > 0]

    # Bubble colour: green if actual WR > predicted, red if under
    colours = np.where(grouped["wr"] >= grouped["mid_prob"] / 100, GREEN, RED)

    fig = go.Figure()

    # Perfect calibration diagonal
    fig.add_trace(go.Scatter(
        x=[0, 100],
        y=[0, 1],
        mode="lines",
        name="Calibração perfeita",
        line=dict(color=TEXT_DIM, dash="dash", width=1),
        hoverinfo="skip",
    ))

    # Calibration bubbles
    fig.add_trace(go.Scatter(
        x=grouped["mid_prob"],
        y=grouped["wr"],
        mode="markers+text",
        name="Win rate real",
        marker=dict(
            size=np.sqrt(grouped["n"]) * 10,
            color=colours,
            opacity=0.85,
            line=dict(color=GOLD, width=1),
        ),
        text=grouped["n"].apply(lambda n: f"n={n}"),
        textposition="top center",
        textfont=dict(color=TEXT_MAIN, size=11),
        hovertemplate=(
            "Banda: %{x:.0f}%<br>"
            "Win rate real: %{y:.1%}<br>"
            "n picks: %{customdata}<extra></extra>"
        ),
        customdata=grouped["n"],
    ))

    fig.update_layout(
        **PLOTLY_DARK,
        title=dict(text="Diagrama de Calibração (Predicted vs Actual Win Rate)", font=dict(color=GOLD, size=16)),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MAIN)),
    )
    fig.update_xaxes(title="Probabilidade Prevista (%)", range=[40, 100], gridcolor=GRID_COL)
    fig.update_yaxes(title="Win Rate Real", tickformat=".0%", range=[0, 1.05], gridcolor=GRID_COL)
    return fig


def plot_strategy_comparison(comparison_df: pd.DataFrame) -> go.Figure:
    """
    Horizontal bar chart of ROI by strategy.
    Bars coloured green (ROI >= 0) or red (ROI < 0).
    comparison_df must have columns: strategy, roi, n_picks.
    """
    df = comparison_df.sort_values("roi", ascending=True)
    colours = [GREEN if v >= 0 else RED for v in df["roi"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["roi"],
        y=df["strategy"],
        orientation="h",
        marker_color=colours,
        marker_line=dict(color=BG_DARK, width=0.5),
        text=df.apply(lambda r: f"{r['roi']:+.1f}% (n={r['n_picks']})", axis=1),
        textposition="outside",
        textfont=dict(color=TEXT_MAIN, size=11),
        hovertemplate="<b>%{y}</b><br>ROI: %{x:+.1f}%<extra></extra>",
    ))

    fig.add_vline(x=0, line_color=TEXT_DIM, line_width=1)

    fig.update_layout(
        **PLOTLY_DARK,
        title=dict(text="Comparação de Estratégias (ROI %)", font=dict(color=GOLD, size=16)),
        xaxis_title="ROI (%)",
        yaxis_title="",
        bargap=0.3,
    )
    return fig


def plot_score_distribution(picks_df: pd.DataFrame) -> go.Figure:
    """
    Histogram of score_sistema, coloured by result (WIN green, LOSS red).
    """
    df = picks_df.copy()
    wins  = df[df["win"] == 1]["score_sistema"]
    losses = df[df["win"] == 0]["score_sistema"]

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=wins,
        name="WIN",
        marker_color=GREEN,
        opacity=0.75,
        xbins=dict(start=0, end=100, size=5),
        hovertemplate="Score: %{x}<br>Count: %{y}<extra>WIN</extra>",
    ))
    fig.add_trace(go.Histogram(
        x=losses,
        name="LOSS",
        marker_color=RED,
        opacity=0.75,
        xbins=dict(start=0, end=100, size=5),
        hovertemplate="Score: %{x}<br>Count: %{y}<extra>LOSS</extra>",
    ))

    fig.update_layout(
        **PLOTLY_DARK,
        barmode="overlay",
        title=dict(text="Distribuição Score Sistema por Resultado", font=dict(color=GOLD, size=16)),
        xaxis_title="Score Sistema (0–100)",
        yaxis_title="Nº de Picks",
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MAIN)),
    )
    return fig


def plot_clv_distribution(picks_df: pd.DataFrame) -> go.Figure:
    """
    CLV distribution histogram with positive/negative split and KDE overlay.
    """
    df = picks_df.dropna(subset=["clv"]).copy()
    pos = df[df["clv"] >= 0]["clv"]
    neg = df[df["clv"] <  0]["clv"]

    fig = go.Figure()

    # Negative CLV bars
    fig.add_trace(go.Histogram(
        x=neg,
        name="CLV < 0",
        marker_color=RED,
        opacity=0.7,
        xbins=dict(size=2),
        hovertemplate="CLV: %{x:.1f}%<br>Count: %{y}<extra>CLV negativo</extra>",
    ))

    # Positive CLV bars
    fig.add_trace(go.Histogram(
        x=pos,
        name="CLV ≥ 0",
        marker_color=GREEN,
        opacity=0.7,
        xbins=dict(size=2),
        hovertemplate="CLV: %{x:.1f}%<br>Count: %{y}<extra>CLV positivo</extra>",
    ))

    # KDE overlay using scipy if available
    try:
        from scipy.stats import gaussian_kde
        clv_vals = df["clv"].values
        if len(clv_vals) > 4:
            kde = gaussian_kde(clv_vals, bw_method=0.4)
            x_range = np.linspace(clv_vals.min() - 2, clv_vals.max() + 2, 200)
            kde_y    = kde(x_range) * len(clv_vals) * 2  # scale to histogram counts
            fig.add_trace(go.Scatter(
                x=x_range,
                y=kde_y,
                mode="lines",
                name="KDE",
                line=dict(color=GOLD, width=2),
                hoverinfo="skip",
            ))
    except ImportError:
        pass

    fig.add_vline(x=0, line_dash="dash", line_color=TEXT_DIM, line_width=1)

    mean_clv = df["clv"].mean()
    fig.add_vline(
        x=mean_clv,
        line_dash="dot",
        line_color=GOLD,
        annotation_text=f"Média {mean_clv:+.2f}%",
        annotation_font_color=GOLD,
        annotation_position="top right",
    )

    fig.update_layout(
        **PLOTLY_DARK,
        barmode="overlay",
        title=dict(text="Distribuição CLV (Closing Line Value)", font=dict(color=GOLD, size=16)),
        xaxis_title="CLV (%)",
        yaxis_title="Nº de Picks",
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MAIN)),
    )
    return fig


def plot_movement_matrix(picks_df: pd.DataFrame) -> go.Figure:
    """
    Heatmap: movimento (rows) × sharp_label (cols), cell value = win rate.
    """
    df = picks_df.copy()

    movements   = ["SHORTENING", "DRIFTING"]
    sharp_labels = ["STEAM", "SHARP", "WATCH"]

    matrix = []
    annotations = []

    for mov in movements:
        row = []
        for sl in sharp_labels:
            subset = df[(df["movimento"] == mov) & (df["sharp_label"] == sl)]
            wr = subset["win"].mean() if len(subset) > 0 else np.nan
            n  = len(subset)
            row.append(wr if not np.isnan(wr) else None)
            annotations.append(dict(
                x=sl,
                y=mov,
                text=f"{wr:.0%}<br>n={n}" if not np.isnan(wr) else "n/a",
                showarrow=False,
                font=dict(color=TEXT_MAIN, size=13),
            ))
        matrix.append(row)

    fig = go.Figure(data=go.Heatmap(
        z=matrix,
        x=sharp_labels,
        y=movements,
        colorscale=[[0, RED], [0.5, "#444"], [1, GREEN]],
        zmin=0,
        zmax=1,
        colorbar=dict(
            title=dict(text="Win Rate", font=dict(color=GOLD)),
            tickformat=".0%",
            tickfont=dict(color=TEXT_MAIN),
        ),
        hovertemplate="<b>%{y} × %{x}</b><br>Win Rate: %{z:.1%}<extra></extra>",
    ))

    fig.update_layout(
        **PLOTLY_DARK,
        title=dict(text="Matriz Win Rate: Movimento × Sharp Label", font=dict(color=GOLD, size=16)),
        xaxis_title="Sharp Label",
        yaxis_title="Movimento",
        annotations=annotations,
    )
    return fig


# ---------------------------------------------------------------------------
# KPI calculation helpers
# ---------------------------------------------------------------------------

def _build_strategy_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Build comparison_df for plot_strategy_comparison from picks_df."""
    strategies = {
        "Todos os Picks":      df,
        "SHORTENING":          df[df["movimento"] == "SHORTENING"],
        "DRIFTING":            df[df["movimento"] == "DRIFTING"],
        "STEAM/SHARP":         df[df["sharp_label"].isin(["STEAM", "SHARP"])],
        "WATCH":               df[df["sharp_label"] == "WATCH"],
        "Score >= 60":         df[df["score_sistema"] >= 60],
        "Score >= 70":         df[df["score_sistema"] >= 70],
        "EV Positivo":         df[df["ev"] > 0],
        "SHORT + STEAM/SHARP": df[(df["movimento"] == "SHORTENING") & df["sharp_label"].isin(["STEAM", "SHARP"])],
        "xG >= 3.0":           df[df["xg_total"] >= 3.0],
    }

    rows = []
    for name, subset in strategies.items():
        if len(subset) == 0:
            continue
        n    = len(subset)
        wr   = subset["win"].mean() * 100
        profit = subset["profit_flat"].sum()
        roi  = (profit / n) * 100  # ROI per pick as %
        rows.append({"strategy": name, "roi": roi, "n_picks": n, "win_rate": wr})

    return pd.DataFrame(rows)


def _compute_kpis(df: pd.DataFrame, comparison_df: pd.DataFrame) -> dict:
    """Compute top-level KPI values for the summary section."""
    n_total  = len(df)
    win_rate = df["win"].mean() * 100
    profit   = df["profit_flat"].sum()
    roi      = (profit / n_total) * 100 if n_total > 0 else 0.0
    avg_clv  = df["clv"].mean()

    best_row = comparison_df.loc[comparison_df["roi"].idxmax()] if len(comparison_df) > 0 else None
    best_str = f"{best_row['strategy']} ({best_row['roi']:+.1f}%)" if best_row is not None else "N/A"

    return {
        "n_total":   n_total,
        "win_rate":  win_rate,
        "roi":       roi,
        "avg_clv":   avg_clv,
        "best_strategy": best_str,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(output_path: str = "dashboard/analytics.html") -> None:
    """
    Generate standalone analytics.html with all 6 Plotly charts in a dark-themed
    2-column grid layout.  Plotly is loaded from CDN (plotly.io include_plotlyjs='cdn').
    """
    picks_df      = load_picks_df()
    comparison_df = _build_strategy_comparison(picks_df)
    kpis          = _compute_kpis(picks_df, comparison_df)

    # Build chart HTML fragments
    def _chart_html(fig: go.Figure) -> str:
        return pio.to_html(
            fig,
            include_plotlyjs="cdn",
            full_html=False,
            config={"displayModeBar": False, "responsive": True},
        )

    roi_html    = _chart_html(plot_roi_curve(picks_df))
    cal_html    = _chart_html(plot_calibration(picks_df))
    strat_html  = _chart_html(plot_strategy_comparison(comparison_df))
    score_html  = _chart_html(plot_score_distribution(picks_df))
    clv_html    = _chart_html(plot_clv_distribution(picks_df))
    matrix_html = _chart_html(plot_movement_matrix(picks_df))

    # Only include plotly CDN once (it gets duplicated by to_html per chart)
    # We include it in the first chart; subsequent charts reference the same global
    charts_no_cdn = []
    for i, html in enumerate([roi_html, cal_html, strat_html, score_html, clv_html, matrix_html]):
        if i == 0:
            charts_no_cdn.append(html)
        else:
            # Strip the <script src="cdn"> tag from subsequent charts
            import re
            html_clean = re.sub(
                r'<script src="https://cdn\.plot\.ly/[^"]+"></script>',
                "",
                html,
            )
            charts_no_cdn.append(html_clean)

    roi_h, cal_h, strat_h, score_h, clv_h, matrix_h = charts_no_cdn

    kpi_clv_colour = GREEN if kpis["avg_clv"] >= 0 else RED
    kpi_roi_colour = GREEN if kpis["roi"] >= 0 else RED

    html_doc = f"""<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Over 2.5 SCOUT — Analytics Dashboard</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: {BG_DARK};
      color: {TEXT_MAIN};
      font-family: 'Segoe UI', Arial, sans-serif;
      min-height: 100vh;
      padding: 24px 16px;
    }}

    h1 {{
      text-align: center;
      color: {GOLD};
      font-size: 1.8rem;
      letter-spacing: 2px;
      margin-bottom: 8px;
      text-transform: uppercase;
    }}

    .subtitle {{
      text-align: center;
      color: {TEXT_DIM};
      font-size: 0.85rem;
      margin-bottom: 32px;
    }}

    /* KPI cards */
    .kpi-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      justify-content: center;
      margin-bottom: 36px;
    }}

    .kpi-card {{
      background: {BG_PANEL};
      border: 1px solid #2A2A3A;
      border-radius: 10px;
      padding: 18px 28px;
      min-width: 160px;
      text-align: center;
    }}

    .kpi-label {{
      font-size: 0.72rem;
      color: {TEXT_DIM};
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 6px;
    }}

    .kpi-value {{
      font-size: 1.65rem;
      font-weight: 700;
      color: {GOLD};
    }}

    .kpi-value.green {{ color: {GREEN}; }}
    .kpi-value.red   {{ color: {RED};   }}

    .kpi-card.wide {{
      min-width: 260px;
    }}

    /* Chart grid */
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(560px, 1fr));
      gap: 24px;
      max-width: 1400px;
      margin: 0 auto;
    }}

    .chart-card {{
      background: {BG_CARD};
      border: 1px solid #2A2A3A;
      border-radius: 12px;
      padding: 8px;
      overflow: hidden;
    }}

    /* Make embedded plotly divs fill the card */
    .chart-card .plotly-graph-div {{
      width: 100% !important;
    }}

    @media (max-width: 640px) {{
      .chart-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <h1>Over 2.5 Scout &mdash; Analytics Dashboard</h1>
  <p class="subtitle">Gerado automaticamente &bull; {picks_df["data"].max().strftime("%d/%m/%Y") if not picks_df.empty else "N/A"}</p>

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">Total Picks</div>
      <div class="kpi-value">{kpis['n_total']}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value {'green' if kpis['win_rate'] >= 52.4 else 'red'}">{kpis['win_rate']:.1f}%</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">ROI</div>
      <div class="kpi-value {'green' if kpis['roi'] >= 0 else 'red'}">{kpis['roi']:+.1f}%</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">CLV Médio</div>
      <div class="kpi-value {'green' if kpis['avg_clv'] >= 0 else 'red'}">{kpis['avg_clv']:+.2f}%</div>
    </div>
    <div class="kpi-card wide">
      <div class="kpi-label">Melhor Estratégia</div>
      <div class="kpi-value" style="font-size:1.1rem;">{kpis['best_strategy']}</div>
    </div>
  </div>

  <!-- Charts -->
  <div class="chart-grid">
    <div class="chart-card">{roi_h}</div>
    <div class="chart-card">{cal_h}</div>
    <div class="chart-card">{strat_h}</div>
    <div class="chart-card">{score_h}</div>
    <div class="chart-card">{clv_h}</div>
    <div class="chart-card">{matrix_h}</div>
  </div>
</body>
</html>
"""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"Dashboard gerado: {out.resolve()}  ({out.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gerar dashboard de analytics Over 2.5")
    parser.add_argument(
        "--output",
        default="dashboard/analytics.html",
        help="Caminho de saída para o HTML gerado (default: dashboard/analytics.html)",
    )
    args = parser.parse_args()
    generate_html(output_path=args.output)
