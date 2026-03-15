"""
10-K Extraction Evaluation Dashboard.

Interactive Plotly Dash dashboard for analysing individual extraction runs
and comparing any two runs side-by-side.  Auto-discovers all
``eval_results_*.json`` files in ``data/10k/output/``.

Usage:
    uv run python src/dashboard.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, callback, dcc, html

# ---------------------------------------------------------------------------
# Constants & field definitions
# ---------------------------------------------------------------------------

_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "10k" / "output"

FIELD_CATEGORIES: dict[str, list[str]] = {
    "Income Statement": [
        "revenue", "cogs", "gross_profit", "sga", "total_operating_expenses",
        "taxes", "interest_expense", "interest_income", "da", "net_income",
    ],
    "Cash Flow": [
        "cash_from_operations", "change_in_cash", "changes_in_nwc",
        "cash_from_investing", "capex", "acquisitions", "divestitures",
        "cash_from_financing", "exchange_rates_other", "cash_interest_net",
        "cash_taxes", "dividends", "net_share_issuance", "net_debt_issuance",
    ],
    "Balance Sheet": [
        "cash", "accounts_receivable", "inventory", "current_assets",
        "goodwill", "other_intangibles", "total_assets", "short_term_debt",
        "accounts_payable", "accrued_expenses", "deferred_revenue",
        "current_liabilities", "total_liabilities", "shareholders_equity",
        "operating_lease_obligations",
    ],
}

ALL_FIELDS = [f for fields in FIELD_CATEGORIES.values() for f in fields]

CATEGORY_COLORS = {
    "Income Statement": "#5A7DB8",
    "Cash Flow": "#E8A340",
    "Balance Sheet": "#52A878",
}

FIELD_TO_CATEGORY: dict[str, str] = {}
for _cat, _fields in FIELD_CATEGORIES.items():
    for _f in _fields:
        FIELD_TO_CATEGORY[_f] = _cat


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

COLORS = {
    "bg": "#FFFFFF",
    "card": "#F5F5F5",
    "text": "#333333",
    "muted": "#888888",
    "grid": "#E0E0E0",
    "green": "#52A878",
    "red": "#F24D4D",          # softened UBS red
    "ubs_red": "#E60000",
}

# Palette for up to 8 runs in comparison charts (softened ~15%)
RUN_PALETTE = [
    "#5A7DB8", "#52A878", "#E8A340", "#A47EF7",
    "#3EB0CC", "#E560A0", "#85C040", "#F24D4D",
]

_LAYOUT = dict(
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["bg"],
    font_color=COLORS["text"],
    font_size=12,
    margin=dict(l=60, r=30, t=50, b=60),
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _short_doc_name(name: str) -> str:
    parts = name.replace(".pdf", "").split("_")
    if len(parts) >= 4:
        return parts[2].title()
    return name


def _run_label(filename: str) -> str:
    """``eval_results_llm_gpt-51.json`` -> ``llm_gpt-51``"""
    return filename.replace("eval_results_", "").replace(".json", "")


def discover_runs() -> dict[str, dict]:
    """Return ``{label: parsed_json}`` for every ``eval_results_*.json``."""
    runs: dict[str, dict] = {}
    for p in sorted(_OUTPUT_DIR.glob("eval_results_*.json")):
        runs[_run_label(p.name)] = json.loads(p.read_text())
    return runs


def _default_run_a(labels: list[str]) -> str:
    """Pick the first non-table-match run, or fall back to the first."""
    for label in labels:
        if "table-match" not in label:
            return label
    return labels[0]


BASELINE_ID = "table-match"


def run_doc_df(data: dict) -> pd.DataFrame:
    rows = []
    for row in data["rows"]:
        doc = row["inputs.document"]
        rows.append({
            "document": doc,
            "label": f"{_short_doc_name(doc)} ({row['inputs.year']})",
            "year": row["inputs.year"],
            "hit_rate": row["outputs.hit_rate.hit_rate"],
        })
    return pd.DataFrame(rows)


def run_field_df(data: dict) -> pd.DataFrame:
    rows = []
    for row in data["rows"]:
        doc = row["inputs.document"]
        label = f"{_short_doc_name(doc)} ({row['inputs.year']})"
        matches = row["outputs.hit_rate.field_matches"]
        for field in ALL_FIELDS:
            rows.append({
                "field": field,
                "document": doc,
                "label": label,
                "match": matches.get(field, False),
                "category": FIELD_TO_CATEGORY.get(field, "Other"),
            })
    return pd.DataFrame(rows)


def _pct_deviation(expected: float | None, actual: float | None) -> float | None:
    if expected is None or actual is None:
        return None
    if isinstance(expected, float) and math.isnan(expected):
        return None
    if isinstance(actual, float) and math.isnan(actual):
        return None
    if expected == 0:
        return 0.0 if actual == 0 else None
    ratio = actual / expected
    if math.isclose(ratio, 1000, rel_tol=0.01):
        return 0.0
    return (actual - expected) / abs(expected) * 100


# ---------------------------------------------------------------------------
# Figures – single run
# ---------------------------------------------------------------------------

def fig_single_doc_bar(doc_df: pd.DataFrame, color: str = "#636EFA") -> go.Figure:
    fig = go.Figure(go.Bar(
        x=doc_df["label"], y=doc_df["hit_rate"],
        marker_color=color,
        text=[f"{v:.0%}" for v in doc_df["hit_rate"]],
        textposition="outside",
    ))
    fig.update_layout(
        **_LAYOUT,
        title="Hit Rate by Document",
        yaxis=dict(title="Hit Rate", tickformat=".0%", range=[0, 1.12],
                   gridcolor=COLORS["grid"]),
        xaxis=dict(title=""),
    )
    return fig


def fig_single_category_bar(field_df: pd.DataFrame, color: str = "#636EFA") -> go.Figure:
    cat_rates = field_df.groupby("category")["match"].mean().reindex(FIELD_CATEGORIES.keys())
    fig = go.Figure(go.Bar(
        x=cat_rates.index.tolist(),
        y=cat_rates.values,
        marker_color=[CATEGORY_COLORS.get(c, color) for c in cat_rates.index],
        text=[f"{v:.0%}" for v in cat_rates.values],
        textposition="outside",
    ))
    fig.update_layout(
        **_LAYOUT,
        title="Hit Rate by Field Category",
        yaxis=dict(title="Hit Rate", tickformat=".0%", range=[0, 1.15],
                   gridcolor=COLORS["grid"]),
    )
    return fig


def fig_heatmap(field_df: pd.DataFrame, title: str) -> go.Figure:
    pivot = field_df.pivot(index="field", columns="label", values="match").astype(float)
    ordered = [f for f in ALL_FIELDS if f in pivot.index]
    pivot = pivot.loc[ordered]
    # Sort by average match rate (ascending) to align with the field bar chart
    pivot = pivot.loc[pivot.mean(axis=1).sort_values().index]

    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
        colorscale=[[0, COLORS["red"]], [1, COLORS["green"]]],
        showscale=False,
        hovertemplate="Field: %{y}<br>Doc: %{x}<br>Match: %{z:.0f}<extra></extra>",
    ))

    fig.update_layout(
        **{k: v for k, v in _LAYOUT.items() if k != "margin"},
        title=title,
        yaxis=dict(autorange="reversed", dtick=1, tickfont=dict(size=9)),
        xaxis=dict(side="top", tickfont=dict(size=10)),
        height=900, margin=dict(l=160, r=30, t=80, b=30),
    )
    return fig


def fig_field_bar(field_df: pd.DataFrame) -> go.Figure:
    """Horizontal bar: per-field accuracy averaged across documents."""
    agg = field_df.groupby("field")["match"].mean().reindex(ALL_FIELDS).sort_values()
    colors = [CATEGORY_COLORS.get(FIELD_TO_CATEGORY.get(f, ""), "#888")
              for f in agg.index]

    fig = go.Figure(go.Bar(
        y=agg.index.tolist(), x=agg.values, orientation="h",
        marker_color=colors,
        text=[f"{v:.0%}" for v in agg.values],
        textposition="outside",
    ))
    fig.update_layout(
        **{k: v for k, v in _LAYOUT.items() if k != "margin"},
        title="Per-Field Accuracy (avg across documents)",
        xaxis=dict(title="Accuracy", tickformat=".0%", range=[0, 1.15],
                   gridcolor=COLORS["grid"]),
        yaxis=dict(autorange="reversed"),
        height=900,
        margin=dict(l=180, r=30, t=50, b=30),
    )
    return fig


# ---------------------------------------------------------------------------
# Figures – multi-run comparison
# ---------------------------------------------------------------------------

def fig_multi_doc_bar(
    run_names: list[str], doc_dfs: list[pd.DataFrame],
) -> go.Figure:
    fig = go.Figure()
    for i, (name, ddf) in enumerate(zip(run_names, doc_dfs)):
        fig.add_trace(go.Bar(
            x=ddf["label"], y=ddf["hit_rate"], name=name,
            marker_color=RUN_PALETTE[i % len(RUN_PALETTE)],
            text=[f"{v:.0%}" for v in ddf["hit_rate"]],
            textposition="outside",
        ))
    fig.update_layout(
        **_LAYOUT, title="Hit Rate by Document",
        yaxis=dict(title="Hit Rate", tickformat=".0%", range=[0, 1.12],
                   gridcolor=COLORS["grid"]),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
    )
    return fig


def fig_multi_delta(
    run_names: list[str], doc_dfs: list[pd.DataFrame], baseline: str,
) -> go.Figure:
    """Show each run's delta vs the baseline."""
    base_idx = run_names.index(baseline)
    base_hr = doc_dfs[base_idx]["hit_rate"].values
    labels = doc_dfs[0]["label"]

    fig = go.Figure()
    for i, (name, ddf) in enumerate(zip(run_names, doc_dfs)):
        if name == baseline:
            continue
        delta = ddf["hit_rate"].values - base_hr
        fig.add_trace(go.Bar(
            x=labels, y=delta, name=name,
            marker_color=RUN_PALETTE[i % len(RUN_PALETTE)],
            text=[f"{d:+.1%}" for d in delta], textposition="outside",
        ))
    fig.update_layout(
        **_LAYOUT,
        title=f"Advantage Over {baseline} (Δ Hit Rate)",
        yaxis=dict(title="Δ Hit Rate", tickformat="+.0%",
                   gridcolor=COLORS["grid"],
                   zeroline=True, zerolinecolor=COLORS["text"]),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
    )
    return fig


def fig_multi_category(
    run_names: list[str], field_dfs: list[pd.DataFrame],
) -> go.Figure:
    fig = go.Figure()
    for i, (name, fdf) in enumerate(zip(run_names, field_dfs)):
        rates = []
        for cat, fields in FIELD_CATEGORIES.items():
            rates.append(fdf[fdf["field"].isin(fields)]["match"].mean())
        fig.add_trace(go.Bar(
            x=list(FIELD_CATEGORIES.keys()), y=rates, name=name,
            marker_color=RUN_PALETTE[i % len(RUN_PALETTE)],
            text=[f"{v:.0%}" for v in rates], textposition="outside",
        ))
    fig.update_layout(
        **_LAYOUT, title="Hit Rate by Field Category",
        yaxis=dict(title="Hit Rate", tickformat=".0%", range=[0, 1.15],
                   gridcolor=COLORS["grid"]),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
    )
    return fig


def fig_multi_field_bar(
    run_names: list[str], field_dfs: list[pd.DataFrame],
) -> go.Figure:
    """Grouped vertical bar: per-field accuracy for each run (fields on x-axis)."""
    # Sort by first run's accuracy ascending
    agg_first = field_dfs[0].groupby("field")["match"].mean().reindex(ALL_FIELDS)
    order = agg_first.sort_values().index.tolist()

    fig = go.Figure()
    for i, (name, fdf) in enumerate(zip(run_names, field_dfs)):
        agg = fdf.groupby("field")["match"].mean().reindex(order)
        fig.add_trace(go.Bar(
            x=agg.index.tolist(), y=agg.values,
            name=name,
            marker_color=RUN_PALETTE[i % len(RUN_PALETTE)],
        ))
    fig.update_layout(
        **_LAYOUT,
        title="Per-Field Accuracy (avg across documents)",
        yaxis=dict(title="Accuracy", tickformat=".0%", range=[0, 1.15],
                   gridcolor=COLORS["grid"]),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
    )
    return fig


def fig_multi_detail_table(
    doc_name: str,
    run_names: list[str],
    run_data: list[dict],
) -> go.Figure:
    """Comparison table with one column per run."""
    rows_by_run = []
    for data in run_data:
        rows_by_run.append(
            next(r for r in data["rows"] if r["inputs.document"] == doc_name)
        )

    expected = rows_by_run[0]["inputs.expected"]
    fields: list[str] = []
    exp_v: list[str] = []
    for field in ALL_FIELDS:
        fields.append(field)
        e = expected.get(field)
        exp_v.append("—" if e is None else f"{e:,.3f}")

    col_values: list[list[str]] = [fields, exp_v]
    col_fills: list[list[str]] = [
        ["#F0F0F0"] * len(fields),
        ["#F0F0F0"] * len(fields),
    ]
    col_font_colors: list[list[str]] = [
        ["#333333"] * len(fields),
        ["#333333"] * len(fields),
    ]
    header_names = ["<b>Field</b>", "<b>Expected</b>"]

    for name, row in zip(run_names, rows_by_run):
        actual = row["inputs.actual"]
        matches = row["outputs.hit_rate.field_matches"]
        vals, colors = [], []
        for field in ALL_FIELDS:
            v = actual.get(field)
            vals.append("—" if v is None else f"{v:,.3f}")
            colors.append(
                COLORS["green"] if matches.get(field, False) else COLORS["red"]
            )
        col_values.append(vals)
        col_fills.append(colors)
        col_font_colors.append(["white"] * len(fields))
        header_names.append(f"<b>{name}</b>")

    fig = go.Figure(go.Table(
        header=dict(
            values=header_names,
            fill_color="#333333", font=dict(color="white", size=12),
            align="left",
        ),
        cells=dict(
            values=col_values,
            fill_color=col_fills,
            font=dict(color=col_font_colors, size=11), align="left", height=26,
        ),
    ))
    fig.update_layout(
        **{k: v for k, v in _LAYOUT.items() if k != "margin"},
        title=f"Field Detail — {_short_doc_name(doc_name)}",
        height=60 + 26 * len(fields) + 40,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


# ---------------------------------------------------------------------------
# Dash App
# ---------------------------------------------------------------------------

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "10-K Extraction Accuracy Benchmarks"

_CARD = {
    "backgroundColor": COLORS["card"],
    "borderRadius": "12px",
    "padding": "24px 32px",
    "textAlign": "center",
    "flex": "1",
    "minWidth": "160px",
    "border": "1px solid #E0E0E0",
}

_DD_STYLE = {"width": "300px", "color": "#111"}


def _kpi_card(title: str, value: str, color: str = COLORS["text"],
              subtitle: str = "") -> html.Div:
    children = [
        html.Div(title, style={"fontSize": "14px", "color": COLORS["muted"]}),
        html.Div(value, style={"fontSize": "44px", "fontWeight": "bold",
                               "color": color}),
    ]
    if subtitle:
        children.append(
            html.Div(subtitle, style={"fontSize": "12px", "color": COLORS["muted"]}))
    return html.Div(style=_CARD, children=children)


# ---- Layout (function so it re-discovers runs on each page load) ----------

def serve_layout():
    runs = discover_runs()
    run_labels = list(runs.keys())
    n_docs = len(runs[run_labels[0]]["rows"]) if run_labels else 0

    return html.Div(
        style={"backgroundColor": COLORS["bg"], "color": COLORS["text"],
               "fontFamily": "'Segoe UI', sans-serif", "minHeight": "100vh",
               "padding": "24px"},
        children=[
            # Hidden store to pass current run labels to callbacks
            dcc.Store(id="run-labels-store", data=run_labels),
        # Header banner
        html.Div(
            style={
                "background": "#FFFFFF",
                "borderRadius": "16px",
                "padding": "28px 32px 20px",
                "marginBottom": "24px",
                "boxShadow": "0 2px 12px rgba(0,0,0,0.08)",
                "borderBottom": "4px solid #E60000",
            },
            children=[
                html.H1(
                    "10-K Extraction Accuracy Benchmarks",
                    style={"textAlign": "center", "margin": "0 0 6px",
                           "fontSize": "32px", "fontWeight": "700",
                           "letterSpacing": "0.5px",
                           "color": "#333333"},
                ),
                html.P(
                    "Evaluation Dashboard",
                    style={"textAlign": "center", "margin": "0 0 12px",
                           "fontSize": "16px", "fontWeight": "300",
                           "color": "#888888", "letterSpacing": "3px",
                           "textTransform": "uppercase"},
                ),
                html.Div(
                    style={"display": "flex", "justifyContent": "center",
                           "gap": "32px", "flexWrap": "wrap"},
                    children=[
                        html.Span(
                            [html.Span(f"{len(run_labels)}",
                                       style={"fontWeight": "bold",
                                              "color": COLORS["ubs_red"]}),
                             " runs"],
                            style={"fontSize": "14px", "color": "#888"},
                        ),
                        html.Span("·", style={"color": "#CCC"}),
                        html.Span(
                            [html.Span(f"{len(ALL_FIELDS)}",
                                       style={"fontWeight": "bold",
                                              "color": "#2E5090"}),
                             " fields"],
                            style={"fontSize": "14px", "color": "#888"},
                        ),
                        html.Span("·", style={"color": "#CCC"}),
                        html.Span(
                            [html.Span(
                                f"{n_docs}",
                                style={"fontWeight": "bold",
                                       "color": "#2E8B57"}),
                             " documents"],
                            style={"fontSize": "14px", "color": "#888"},
                        ),
                    ],
                ),
            ],
        ),

        dcc.Tabs(
            id="tabs", value="single",
            colors={"border": "#E0E0E0", "primary": COLORS["ubs_red"],
                    "background": "#F5F5F5"},
            style={"marginBottom": "16px"},
            children=[
                dcc.Tab(
                    label="Single Run Analysis", value="single",
                    style={"color": "#555",
                           "backgroundColor": "#F5F5F5",
                           "fontWeight": "500"},
                    selected_style={"color": "white",
                                    "backgroundColor": COLORS["ubs_red"],
                                    "fontWeight": "600"},
                ),
                dcc.Tab(
                    label="Compare Runs", value="compare",
                    style={"color": "#555",
                           "backgroundColor": "#F5F5F5",
                           "fontWeight": "500"},
                    selected_style={"color": "white",
                                    "backgroundColor": COLORS["ubs_red"],
                                    "fontWeight": "600"},
                ),
            ],
        ),
        html.Div(id="tab-content"),
        ],
    )

app.layout = serve_layout


# ---- Tab routing -----------------------------------------------------------

@callback(Output("tab-content", "children"),
          Input("tabs", "value"),
          Input("run-labels-store", "data"))
def render_tab(tab: str, run_labels: list[str]):
    if tab == "single":
        default = _default_run_a(run_labels)
        return html.Div([
            html.Div(
                style={"display": "flex", "alignItems": "center",
                       "gap": "12px", "marginBottom": "20px"},
                children=[
                    html.Label("Run:", style={"fontWeight": "bold"}),
                    dcc.Dropdown(
                        id="single-run-dd",
                        options=[{"label": l, "value": l} for l in run_labels],
                        value=default, style=_DD_STYLE,
                    ),
                ],
            ),
            html.Div(id="single-content"),
        ])

    # compare tab
    non_baseline = [l for l in run_labels if l != BASELINE_ID]
    default_baseline = BASELINE_ID if BASELINE_ID in run_labels else run_labels[-1]
    return html.Div([
        html.Div(
            style={"display": "flex", "gap": "32px", "marginBottom": "20px",
                   "flexWrap": "wrap", "alignItems": "center"},
            children=[
                html.Div(
                    style={"display": "flex", "alignItems": "center",
                           "gap": "12px", "flex": "1"},
                    children=[
                        html.Label("Runs:",
                                   style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="cmp-runs",
                            options=[{"label": l, "value": l}
                                     for l in run_labels],
                            value=non_baseline or run_labels[:1],
                            multi=True,
                            style={"flex": "1", "color": "#111",
                                   "minWidth": "300px"},
                        ),
                    ],
                ),
                html.Div(
                    style={"display": "flex", "alignItems": "center",
                           "gap": "12px"},
                    children=[
                        html.Label("Baseline:",
                                   style={"fontWeight": "bold",
                                          "color": COLORS["ubs_red"]}),
                        dcc.Dropdown(
                            id="cmp-baseline",
                            options=[{"label": l, "value": l}
                                     for l in run_labels],
                            value=default_baseline,
                            style=_DD_STYLE,
                        ),
                    ],
                ),
            ],
        ),
        html.Div(id="compare-content"),
    ])


# ---- Single run callback ---------------------------------------------------

@callback(Output("single-content", "children"),
          Input("single-run-dd", "value"))
def render_single(run_id: str | None):
    if run_id is None:
        return html.Div()

    runs = discover_runs()
    data = runs[run_id]
    avg = data["metrics"]["hit_rate.hit_rate"]
    ddf = run_doc_df(data)
    fdf = run_field_df(data)

    best = ddf.loc[ddf["hit_rate"].idxmax()]
    worst = ddf.loc[ddf["hit_rate"].idxmin()]

    return html.Div([
        # KPI row
        html.Div(
            style={"display": "flex", "gap": "20px", "marginBottom": "28px",
                   "flexWrap": "wrap"},
            children=[
                _kpi_card("Average Hit Rate", f"{avg:.1%}", RUN_PALETTE[0]),
                _kpi_card("Best Document", f"{best['hit_rate']:.0%}",
                          COLORS["green"], best["label"]),
                _kpi_card("Worst Document", f"{worst['hit_rate']:.0%}",
                          COLORS["red"], worst["label"]),
                _kpi_card("Documents", str(len(ddf)),
                          subtitle=f"{len(ALL_FIELDS)} fields each"),
            ],
        ),
        # Charts — row 1: doc bar + category bar side by side
        html.Div(
            style={"display": "flex", "gap": "8px"},
            children=[
                html.Div(dcc.Graph(figure=fig_single_doc_bar(ddf, RUN_PALETTE[0])),
                         style={"flex": "1"}),
                html.Div(dcc.Graph(figure=fig_single_category_bar(fdf, RUN_PALETTE[0])),
                         style={"flex": "1"}),
            ],
        ),
        # Charts — row 2: heatmap + field bar side by side
        html.Div(
            style={"display": "flex", "gap": "8px"},
            children=[
                html.Div(
                    dcc.Graph(figure=fig_heatmap(fdf, f"Field Matches — {run_id}")),
                    style={"flex": "1"},
                ),
                html.Div(dcc.Graph(figure=fig_field_bar(fdf)),
                         style={"flex": "1"}),
            ],
        ),
    ])


# ---- Compare runs callback -------------------------------------------------

@callback(Output("compare-content", "children"),
          Input("cmp-runs", "value"), Input("cmp-baseline", "value"))
def render_compare(selected: list[str] | None, baseline: str | None):
    if not selected or baseline is None:
        return html.Div()

    runs = discover_runs()
    # Ensure baseline is always included and listed last
    all_names = [n for n in selected if n != baseline] + [baseline]
    all_data = [runs[n] for n in all_names]
    doc_dfs = [run_doc_df(d) for d in all_data]
    field_dfs = [run_field_df(d) for d in all_data]
    documents = doc_dfs[0]["document"].tolist()

    # KPI cards for every run
    kpi_children = []
    for i, name in enumerate(all_names):
        avg = all_data[i]["metrics"]["hit_rate.hit_rate"]
        color = RUN_PALETTE[i % len(RUN_PALETTE)]
        subtitle = "(baseline)" if name == baseline else ""
        kpi_children.append(_kpi_card(name, f"{avg:.1%}", color, subtitle))

    return html.Div([
        # KPI row
        html.Div(
            style={"display": "flex", "gap": "20px", "marginBottom": "28px",
                   "flexWrap": "wrap"},
            children=kpi_children,
        ),
        # Document detail (most important — show first)
        html.H3("Document Detail", style={"marginTop": "12px"}),
        html.Div(
            style={"display": "flex", "alignItems": "center", "gap": "12px",
                   "marginBottom": "16px"},
            children=[
                html.Label("Document:"),
                dcc.Dropdown(
                    id="cmp-doc-dd",
                    options=[
                        {"label": f"{_short_doc_name(d)} "
                                  f"({doc_dfs[0][doc_dfs[0]['document']==d]['year'].iloc[0]})",
                         "value": d}
                        for d in documents
                    ],
                    value=documents[0],
                    style={"width": "400px", "color": "#111"},
                ),
            ],
        ),
        html.Div(id="cmp-detail-content"),

        # Charts — row 1: doc bar + category bar side by side
        html.Div(
            style={"display": "flex", "gap": "8px"},
            children=[
                html.Div(dcc.Graph(figure=fig_multi_doc_bar(all_names, doc_dfs)),
                         style={"flex": "1"}),
                html.Div(dcc.Graph(figure=fig_multi_category(all_names, field_dfs)),
                         style={"flex": "1"}),
            ],
        ),
        # Charts — row 2: per-field accuracy full width
        dcc.Graph(figure=fig_multi_field_bar(all_names, field_dfs)),

        # Heatmaps side-by-side
        html.H3("Field Match Heatmaps", style={"marginTop": "24px"}),
        html.Div(
            style={"display": "flex", "gap": "8px", "flexWrap": "wrap"},
            children=[
                html.Div(
                    dcc.Graph(figure=fig_heatmap(fdf, f"Field Matches — {name}")),
                    style={"flex": "1", "minWidth": "400px"},
                )
                for name, fdf in zip(all_names, field_dfs)
            ],
        ),
    ])


@callback(Output("cmp-detail-content", "children"),
          Input("cmp-doc-dd", "value"),
          Input("cmp-runs", "value"),
          Input("cmp-baseline", "value"))
def render_compare_detail(doc: str | None, selected: list[str] | None,
                          baseline: str | None):
    if doc is None or not selected or baseline is None:
        return html.Div()
    runs = discover_runs()
    all_names = [n for n in selected if n != baseline] + [baseline]
    all_data = [runs[n] for n in all_names]
    return html.Div([
        dcc.Graph(figure=fig_multi_detail_table(doc, all_names, all_data)),
    ])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)
