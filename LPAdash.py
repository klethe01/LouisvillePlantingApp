"""
Louisville Planting Guide App — MVP Dash Dashboard
====================================================
Interactive analytics dashboard for seed germination
recommendations in Louisville, KY (Zone 7a).

Connects to Supabase PostgreSQL via LPAmain.py ETL pipeline.
Runs the full ETL on startup to refresh 14-day temperature
data and risk assessments, then serves the dashboard.

Usage:
    python LPAdash.py

Runs at:
    http://localhost:8050

Dependencies:
    pip install dash dash-bootstrap-components plotly pandas
                psycopg2-binary python-dotenv requests

Developed with Claude 4.6 Sonnet (Anthropic, 2026)
"""

# ============================================================
# IMPORTS
# ============================================================

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import dash
from dash import dcc, html, dash_table, Input, Output
import dash_bootstrap_components as dbc

from LPAmain import run_etl, log


# ============================================================
# DATA LOADING
# ------------------------------------------------------------
# Run the full ETL pipeline on startup:
#   - Calls Open Meteo API for the latest 14-day temps
#   - Computes risk assessments for all 47 plants
#   - Returns two DataFrames ready for the dashboard
# ============================================================

log.info("Starting Louisville Planting Guide dashboard...")

try:
    risk_df, temps_df = run_etl()
    log.info("Dashboard data loaded — %d plants | %d temp readings.",
             len(risk_df), len(temps_df))
except Exception as exc:
    log.error("ETL pipeline failed on startup: %s", exc)
    sys.exit(1)


# ============================================================
# CONSTANTS
# ============================================================

TZ_EASTERN = ZoneInfo("America/New_York")

RISK_COLORS = {
    "low":    "#198754",   # green
    "medium": "#e6a817",   # amber
    "high":   "#dc3545",   # red
}

RISK_LABELS = {
    "low":    "Recommended",
    "medium": "May Advise Waiting",
    "high":   "Not Recommended",
}

RISK_BG = {
    "low":    "#d1e7dd",
    "medium": "#fff3cd",
    "high":   "#f8d7da",
}

RISK_TEXT = {
    "low":    "#0a3622",
    "medium": "#664d03",
    "high":   "#58151c",
}


# ============================================================
# KPI CALCULATIONS
# ------------------------------------------------------------
# Computed once on startup from the ETL output DataFrames.
# ============================================================

def compute_kpis(risk_df: pd.DataFrame, temps_df: pd.DataFrame) -> dict:
    """
    Compute summary metrics for the KPI card row.
    Returns a dict of values used directly in the layout.
    """
    n_low  = int((risk_df["risk_level"] == "low").sum())
    n_med  = int((risk_df["risk_level"] == "medium").sum())
    n_high = int((risk_df["risk_level"] == "high").sum())

    min_soil = (
        int(temps_df["soil_6cm_temp"].min())
        if not temps_df.empty and temps_df["soil_6cm_temp"].notna().any()
        else "—"
    )
    min_air = (
        int(temps_df["air_temp"].min())
        if not temps_df.empty and temps_df["air_temp"].notna().any()
        else "—"
    )

    ts = pd.to_datetime(temps_df["timestamp"], utc=True)
    window_start = ts.min().astimezone(TZ_EASTERN).strftime("%b %d")
    window_end   = ts.max().astimezone(TZ_EASTERN).strftime("%b %d")

    return {
        "n_low":    n_low,
        "n_med":    n_med,
        "n_high":   n_high,
        "min_soil": min_soil,
        "min_air":  min_air,
        "window":   f"{window_start} – {window_end}",
        "updated":  datetime.now(TZ_EASTERN).strftime("%b %d, %Y at %I:%M %p"),
    }


kpis = compute_kpis(risk_df, temps_df)


# ============================================================
# CHART BUILDERS
# ============================================================

def build_temp_chart(temps_df: pd.DataFrame) -> go.Figure:
    """
    14-day temperature time series chart.

    Shows air temperature and soil temperature at 6cm
    as separate traces. Solid lines = actual past readings,
    dashed lines = forecast. Vertical rule marks 'now'.
    Reference line at 40°F marks the minimum safe air temp.
    """
    fig = go.Figure()

    # Convert UTC timestamps to Eastern time for display
    df = temps_df.copy()
    df["ts"] = (
        pd.to_datetime(df["timestamp"], utc=True)
        .dt.tz_convert(TZ_EASTERN)
    )

    actual   = df[~df["is_forecast"]]
    forecast = df[ df["is_forecast"]]

    common = dict(mode="lines", hovertemplate="%{x|%b %d %H:%M}<br>%{y}°F<extra></extra>")

    # Air temp — actual (solid blue)
    fig.add_trace(go.Scatter(
        x=actual["ts"],   y=actual["air_temp"],
        name="Air temp (actual)",
        line=dict(color="#0d6efd", width=2),
        **common
    ))
    # Air temp — forecast (dashed blue)
    fig.add_trace(go.Scatter(
        x=forecast["ts"], y=forecast["air_temp"],
        name="Air temp (forecast)",
        line=dict(color="#0d6efd", width=2, dash="dash"),
        **common
    ))
    # Soil 6cm — actual (solid green)
    fig.add_trace(go.Scatter(
        x=actual["ts"],   y=actual["soil_6cm_temp"],
        name="Soil at 6cm (actual)",
        line=dict(color="#198754", width=2),
        **common
    ))
    # Soil 6cm — forecast (dashed green)
    fig.add_trace(go.Scatter(
        x=forecast["ts"], y=forecast["soil_6cm_temp"],
        name="Soil at 6cm (forecast)",
        line=dict(color="#198754", width=2, dash="dash"),
        **common
    ))

    # Reference line — 40°F minimum safe air temp
    fig.add_hline(
        y=40, line_dash="dot", line_color="#dc3545", line_width=1.2,
        annotation_text="40°F minimum air temp",
        annotation_font_size=11,
        annotation_position="bottom right",
    )

    # Vertical line — now (separates actual from forecast)
    now_et = datetime.now(TZ_EASTERN).isoformat()
    fig.add_vline(
        x=now_et,
        line_dash="dash", line_color="#6c757d", line_width=1,
        annotation_text="Now",
        annotation_font_size=11,
        annotation_position="top",
    )

    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Temperature (°F)",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right",  x=1,
            font=dict(size=11),
        ),
        hovermode="x unified",
        margin=dict(l=50, r=20, t=50, b=30),
        plot_bgcolor="white",
        paper_bgcolor="white",
        height=320,
        xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
        font=dict(family="system-ui, sans-serif", size=12),
    )
    return fig


def build_risk_chart(filtered_df: pd.DataFrame) -> go.Figure:
    """
    Soil temperature gap chart — the core planting insight.

    For each plant, calculates the gap between the current
    14-day minimum soil temperature and the plant's minimum
    requirement:
        gap = current_min_soil - plant_min_requirement

    Positive gap → soil is warm enough for that plant.
    Negative gap → soil is too cold, plant is high risk.

    Plants are sorted by gap so the chart immediately shows
    which plants are easiest and hardest to grow right now.
    Color coded by risk level (green / amber / red).
    """
    if filtered_df.empty:
        fig = go.Figure()
        fig.update_layout(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            annotations=[dict(
                text="No plants match the current filters.",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=14, color="#6c757d")
            )],
            height=300, plot_bgcolor="white", paper_bgcolor="white",
        )
        return fig

    # Current minimum soil temp is the same for all rows in a run
    current_soil = int(filtered_df["min_14day_soil6cm"].iloc[0])

    df = filtered_df.copy()
    df["gap"] = df["min_14day_soil6cm"] - df["min_soil_temp_6cm"]
    df = df.sort_values("gap", ascending=True)

    fig = go.Figure()

    # Draw one trace per risk level so the legend is clean
    for level in ["high", "medium", "low"]:
        subset = df[df["risk_level"] == level]
        if subset.empty:
            continue

        fig.add_trace(go.Bar(
            y=subset["common_name"],
            x=subset["gap"],
            name=RISK_LABELS[level],
            orientation="h",
            marker_color=RISK_COLORS[level],
            marker_line_width=0,
            hovertemplate=(
                "<b>%{y}</b><br>"
                f"Current soil min: {current_soil}°F<br>"
                "Plant minimum: %{customdata[0]}°F<br>"
                "Gap: %{x:+.0f}°F<br>"
                "Risk: " + RISK_LABELS[level] +
                "<extra></extra>"
            ),
            customdata=subset[["min_soil_temp_6cm"]].values,
        ))

    # Reference line at 0 — exactly at the minimum threshold
    fig.add_vline(
        x=0, line_dash="solid", line_color="#212529", line_width=1.5,
        annotation_text="At minimum",
        annotation_font_size=10,
        annotation_position="top",
    )

    chart_height = max(350, len(df) * 22 + 80)

    fig.update_layout(
        xaxis_title=f"Temperature gap (current {current_soil}°F min vs plant requirement, °F)",
        yaxis_title=None,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="right",  x=1,
            font=dict(size=11),
        ),
        margin=dict(l=10, r=20, t=50, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(showgrid=True, gridcolor="#f0f0f0", zeroline=False),
        yaxis=dict(showgrid=False),
        barmode="overlay",
        height=chart_height,
        font=dict(family="system-ui, sans-serif", size=11),
    )
    return fig


# ============================================================
# COMPONENT HELPERS
# ============================================================

def kpi_card(label: str, value, unit: str = "", color: str = "primary") -> dbc.Card:
    """
    Render a single KPI summary card.
    Used in the top metric row.
    """
    return dbc.Card([
        dbc.CardBody([
            html.P(label,
                   className="text-muted mb-1",
                   style={"fontSize": "0.78rem", "fontWeight": "500"}),
            html.Div([
                html.Span(str(value),
                          className=f"fw-bold fs-2 text-{color}"),
                html.Span(f" {unit}" if unit else "",
                          className="text-muted ms-1",
                          style={"fontSize": "0.8rem"}),
            ]),
        ], className="py-3")
    ], className="shadow-sm border-0 h-100")


# ============================================================
# APP INITIALISATION
# ============================================================

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    title="Louisville Planting Guide",
    meta_tags=[{"name": "viewport",
                "content": "width=device-width, initial-scale=1"}],
)


# ============================================================
# LAYOUT
# ------------------------------------------------------------
# Four sections:
#   1. Header        — title, window dates, last updated
#   2. KPI row       — recommended / waiting / avoid / min soil temp
#   3. Temp chart    — 14-day air + soil time series
#   4. Filters       — category + risk level dropdowns
#   5. Insight panel — soil temp gap chart (left) + table (right)
#   6. Footer        — data sources
# ============================================================

app.layout = dbc.Container([

    # ── Section 1: Header ─────────────────────────────────
    dbc.Row([
        dbc.Col([
            html.H2(
                [html.Span("🌱 ", style={"fontSize": "1.6rem"}),
                 "Louisville Planting Guide"],
                className="fw-bold mb-1",
                style={"color": "#1a5c39"}
            ),
            html.P(
                f"Seed germination recommendations for Zone 7a  ·  "
                f"14-day window: {kpis['window']}  ·  "
                f"Data refreshed {kpis['updated']}",
                className="text-muted mb-0",
                style={"fontSize": "0.82rem"}
            ),
        ])
    ], className="pt-3 pb-2 mb-3",
       style={"borderBottom": "2px solid #1a5c39"}),

    # ── Section 2: KPI Cards ──────────────────────────────
    dbc.Row([
        dbc.Col(
            kpi_card("Recommended to Plant",
                     kpis["n_low"], "plants", "success"),
            xs=6, md=3
        ),
        dbc.Col(
            kpi_card("May Advise Waiting",
                     kpis["n_med"], "plants", "warning"),
            xs=6, md=3
        ),
        dbc.Col(
            kpi_card("Not Recommended",
                     kpis["n_high"], "plants", "danger"),
            xs=6, md=3
        ),
        dbc.Col(
            kpi_card("14-Day Min Soil Temp",
                     kpis["min_soil"], "°F at 6cm", "info"),
            xs=6, md=3
        ),
    ], className="mb-4 g-3"),

    # ── Section 3: Temperature Chart ─────────────────────
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.Strong("14-Day Temperature Dashboard"),
                    html.Span(
                        " — Air temp (blue) and Soil at 6cm (green) · "
                        "Solid = actual, Dashed = forecast",
                        className="text-muted ms-2",
                        style={"fontSize": "0.78rem"}
                    ),
                ], className="bg-white"),
                dbc.CardBody(
                    dcc.Graph(
                        id="temp-chart",
                        figure=build_temp_chart(temps_df),
                        config={"displayModeBar": False},
                    ),
                    className="p-2"
                )
            ], className="shadow-sm border-0")
        ])
    ], className="mb-4"),

    # ── Section 4: Filters ────────────────────────────────
    dbc.Row([
        dbc.Col([
            html.Label("Plant category",
                       className="small fw-medium mb-1 d-block"),
            dcc.Dropdown(
                id="category-filter",
                options=[
                    {"label": "All categories",    "value": "all"},
                    {"label": "Vegetable / Fruit", "value": "vegetable/fruit"},
                    {"label": "Herb",              "value": "herb"},
                    {"label": "Flower",            "value": "flower"},
                ],
                value="all",
                clearable=False,
            ),
        ], xs=12, md=4),
        dbc.Col([
            html.Label("Risk level",
                       className="small fw-medium mb-1 d-block"),
            dcc.Dropdown(
                id="risk-filter",
                options=[
                    {"label": "All risk levels",                  "value": "all"},
                    {"label": "✅  Recommended (low risk)",        "value": "low"},
                    {"label": "⚠️  May advise waiting (medium)",   "value": "medium"},
                    {"label": "🚫  Not recommended (high risk)",   "value": "high"},
                ],
                value="all",
                clearable=False,
            ),
        ], xs=12, md=4),
        dbc.Col([
            html.Label("\u00a0",
                       className="small fw-medium mb-1 d-block"),
            html.P(id="filter-summary",
                   className="text-muted small pt-2 mb-0"),
        ], xs=12, md=4),
    ], className="mb-3"),

    # ── Section 5: Risk Chart + Table ────────────────────
    dbc.Row([

        # Soil temp gap chart
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.Strong("Soil Temperature Gap by Plant"),
                    html.Span(
                        " — how far current min soil temp is above each "
                        "plant's minimum requirement",
                        className="text-muted ms-1",
                        style={"fontSize": "0.75rem"}
                    ),
                ], className="bg-white"),
                dbc.CardBody(
                    dcc.Graph(
                        id="risk-chart",
                        config={"displayModeBar": False},
                    ),
                    className="p-2"
                )
            ], className="shadow-sm border-0 h-100")
        ], xs=12, md=5),

        # Plant recommendations table
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(
                    html.Strong("Plant Recommendations"),
                    className="bg-white"
                ),
                dbc.CardBody([
                    dash_table.DataTable(
                        id="risk-table",
                        columns=[
                            {"name": "Plant",           "id": "common_name",
                             "type": "text"},
                            {"name": "Category",        "id": "category_name",
                             "type": "text"},
                            {"name": "Risk",            "id": "risk_level",
                             "type": "text"},
                            {"name": "Min Soil (°F)",   "id": "min_soil_temp_6cm",
                             "type": "numeric"},
                            {"name": "Assessment",      "id": "risk_desc",
                             "type": "text"},
                        ],
                        sort_action="native",
                        sort_by=[{"column_id": "min_soil_temp_6cm",
                                  "direction": "asc"}],
                        filter_action="native",
                        page_size=20,
                        fixed_rows={"headers": True},
                        style_table={
                            "overflowX": "auto",
                            "overflowY": "auto",
                            "maxHeight": "560px",
                        },
                        style_cell={
                            "fontFamily": "system-ui, sans-serif",
                            "fontSize":   "12px",
                            "padding":    "6px 10px",
                            "whiteSpace": "normal",
                            "height":     "auto",
                            "border":     "none",
                        },
                        style_header={
                            "backgroundColor": "#f8f9fa",
                            "fontWeight":       "600",
                            "fontSize":         "12px",
                            "borderBottom":     "2px solid #dee2e6",
                            "fontFamily":       "system-ui, sans-serif",
                        },
                        style_data_conditional=[
                            {
                                "if": {"filter_query": '{risk_level} = "low"'},
                                "backgroundColor": RISK_BG["low"],
                                "color":           RISK_TEXT["low"],
                            },
                            {
                                "if": {"filter_query": '{risk_level} = "medium"'},
                                "backgroundColor": RISK_BG["medium"],
                                "color":           RISK_TEXT["medium"],
                            },
                            {
                                "if": {"filter_query": '{risk_level} = "high"'},
                                "backgroundColor": RISK_BG["high"],
                                "color":           RISK_TEXT["high"],
                            },
                            {
                                "if": {"state": "selected"},
                                "backgroundColor": "#cfe2ff",
                                "border":          "1px solid #0d6efd",
                            },
                        ],
                    )
                ], className="p-2")
            ], className="shadow-sm border-0 h-100")
        ], xs=12, md=7),

    ], className="mb-4"),

    # ── Section 6: Footer ────────────────────────────────
    dbc.Row([
        dbc.Col([
            html.Hr(className="mt-0"),
            html.P(
                "Data sources: Open Meteo API (weather) · "
                "Old Farmer's Almanac & Mississippi Foundation "
                "for Renewable Energy (soil germination temperatures) · "
                "Zone 7a — Louisville, KY",
                className="text-muted text-center mb-3",
                style={"fontSize": "0.72rem"}
            ),
        ])
    ]),

], fluid=True, className="px-3 px-md-4")


# ============================================================
# CALLBACKS
# ------------------------------------------------------------
# All interactive updates go through a single callback that
# responds to either filter dropdown changing. Returns:
#   - Updated risk chart figure
#   - Updated table row data
#   - Filter summary text
# ============================================================

@app.callback(
    [
        Output("risk-chart",     "figure"),
        Output("risk-table",     "data"),
        Output("filter-summary", "children"),
    ],
    [
        Input("category-filter", "value"),
        Input("risk-filter",     "value"),
    ],
)
def update_recommendations(category: str, risk_level: str):
    """
    Filter the risk chart and recommendations table.

    Triggered on page load and whenever either dropdown
    changes. Filters risk_df by category and/or risk level,
    rebuilds the chart and table, and updates the summary.
    """
    df = risk_df.copy()

    if category != "all":
        df = df[df["category_name"] == category]
    if risk_level != "all":
        df = df[df["risk_level"] == risk_level]

    table_data = df[[
        "common_name", "category_name", "risk_level",
        "min_soil_temp_6cm", "risk_desc"
    ]].to_dict("records")

    total   = len(risk_df)
    showing = len(df)
    summary = (
        f"Showing {showing} of {total} plants"
        if showing < total
        else f"Showing all {total} plants"
    )

    return build_risk_chart(df), table_data, summary


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    log.info("Dashboard running at http://localhost:8050")
    app.run(debug=False, host="0.0.0.0", port=8050)
