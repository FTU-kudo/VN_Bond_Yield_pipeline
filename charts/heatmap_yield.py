"""
heatmap_yield.py — Heatmap lợi suất TPCP theo tenor × thời gian.
=================================================================

Biểu đồ heatmap phát hiện:
    - Xu hướng lợi suất theo mùa vụ (Tết, cuối quý)
    - Giai đoạn SBV tăng/hạ lãi suất → lợi suất thay đổi đồng loạt
    - Sự khác biệt về biến động giữa kỳ hạn ngắn và dài

Màu sắc: Đỏ đậm = lợi suất cao, Xanh đậm = lợi suất thấp.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio


TENOR_ORDER = [0.08, 0.17, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0,
               5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 15.0, 20.0, 30.0]

TENOR_LABELS = {
    0.08: "1M", 0.17: "2M", 0.25: "3M", 0.5: "6M", 0.75: "9M",
    1.0: "1Y", 1.5: "18M", 2.0: "2Y", 3.0: "3Y", 4.0: "4Y",
    5.0: "5Y", 6.0: "6Y", 7.0: "7Y", 8.0: "8Y", 9.0: "9Y",
    10.0: "10Y", 15.0: "15Y", 20.0: "20Y", 30.0: "30Y",
}


def load_curve_data(cache_dir: Path) -> pd.DataFrame:
    """Nạp dữ liệu đường cong đã fit hoặc dữ liệu gốc."""
    for fname in ["fitted_curve_ns.parquet", "fitted_curve_nss.parquet"]:
        f = cache_dir / fname
        if f.exists():
            df = pd.read_parquet(f)
            df["date"] = pd.to_datetime(df["date"])
            return df

    # Fallback: dùng dữ liệu gốc
    combined = cache_dir / "combined_tpcp_yields.parquet"
    if combined.exists():
        df = pd.read_parquet(combined)
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["rate_type"].isin(["spot_annual", "primary_winning_yield"])]
        from analysis.yield_curve_fitting import tenor_to_years
        df["tenor_yr"] = df["tenor"].map(tenor_to_years)
        df = df.rename(columns={"value": "yield_pct"})
        return df[["date", "tenor_yr", "yield_pct"]].dropna()

    raise FileNotFoundError("Không có dữ liệu đường cong. Hãy chạy pipeline trước.")


def build_heatmap_matrix(
    curve_df: pd.DataFrame,
    freq: str = "ME",
    tenors: list[float] | None = None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Tạo ma trận heatmap: tenor (hàng) × thời gian (cột).

    Parameters
    ----------
    curve_df : pd.DataFrame
        Columns: date, tenor_yr, yield_pct
    freq : str
        Tần suất resample: 'D'=ngày, 'W'=tuần, 'ME'=tháng
    tenors : list[float] | None
        Danh sách tenor muốn hiển thị

    Returns
    -------
    (matrix_df, date_labels, tenor_labels)
    """
    if tenors is None:
        available = sorted(curve_df["tenor_yr"].unique())
        tenors = [t for t in TENOR_ORDER if any(abs(t - a) < 0.3 for a in available)]

    # Lấy tenor gần nhất trong dữ liệu cho mỗi tenor target
    mapped_tenors = {}
    for t in tenors:
        avail = curve_df["tenor_yr"].unique()
        nearest = min(avail, key=lambda x: abs(x - t))
        if abs(nearest - t) < 0.5:
            mapped_tenors[t] = nearest

    if not mapped_tenors:
        raise ValueError("Không có tenor phù hợp trong dữ liệu")

    # Lọc và pivot
    mask = curve_df["tenor_yr"].isin(mapped_tenors.values())
    sub = curve_df[mask].copy()

    # Resample theo tần suất
    sub = sub.set_index("date")
    sub = (
        sub.groupby("tenor_yr")["yield_pct"]
        .resample(freq)
        .mean()
        .reset_index()
    )
    sub = sub.rename(columns={"date": "period"})

    pivot = sub.pivot_table(index="tenor_yr", columns="period", values="yield_pct")

    # Sắp xếp tenor theo thứ tự
    tenor_order = [t for t in TENOR_ORDER if t in mapped_tenors]
    tenor_actual = [mapped_tenors[t] for t in tenor_order]
    pivot = pivot.reindex([a for a in tenor_actual if a in pivot.index])

    date_labels = [str(d)[:7] for d in pivot.columns]
    tenor_labels = [TENOR_LABELS.get(t, f"{t}Y") for t in tenor_order
                    if mapped_tenors.get(t) in pivot.index]

    return pivot, date_labels, tenor_labels


def create_heatmap_figure(
    pivot: pd.DataFrame,
    date_labels: list[str],
    tenor_labels: list[str],
) -> go.Figure:
    """Tạo figure heatmap Plotly."""
    # Lọc bớt nhãn ngày nếu quá nhiều
    n_cols = len(date_labels)
    step = max(1, n_cols // 30)
    x_tick_idx = list(range(0, n_cols, step))
    x_tick_text = [date_labels[i] for i in x_tick_idx]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=date_labels,
        y=tenor_labels,
        colorscale=[
            [0.0, "#1e3a5f"],    # Xanh đậm: lợi suất thấp nhất
            [0.2, "#2563eb"],    # Xanh: thấp
            [0.4, "#38bdf8"],    # Xanh nhạt: trung bình thấp
            [0.5, "#f0f9ff"],    # Trắng: trung tính
            [0.6, "#fbbf24"],    # Vàng: trung bình cao
            [0.8, "#ef4444"],    # Đỏ: cao
            [1.0, "#7f1d1d"],    # Đỏ đậm: cao nhất
        ],
        colorbar=dict(
            title="Lợi suất (%)",
            titlefont=dict(color="#e2e8f0"),
            tickfont=dict(color="#e2e8f0"),
            bgcolor="rgba(0,0,0,0)",
        ),
        hovertemplate=(
            "<b>Tenor:</b> %{y}<br>"
            "<b>Thời gian:</b> %{x}<br>"
            "<b>Lợi suất:</b> %{z:.3f}%<extra></extra>"
        ),
        xgap=0.5, ygap=1,
    ))

    fig.update_layout(
        title=dict(
            text="Heatmap Lợi Suất TPCP Việt Nam",
            x=0.5, xanchor="center",
            font=dict(size=20, color="#f1f5f9", family="Inter, sans-serif"),
        ),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        xaxis=dict(
            title="Thời gian",
            titlefont=dict(color="#94a3b8"),
            tickfont=dict(color="#94a3b8", size=9),
            tickvals=x_tick_idx,
            ticktext=x_tick_text,
            tickangle=-45,
            gridcolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(
            title="Kỳ hạn",
            titlefont=dict(color="#94a3b8"),
            tickfont=dict(color="#94a3b8"),
            gridcolor="rgba(0,0,0,0)",
        ),
        font=dict(family="Inter, sans-serif", color="#e2e8f0"),
        margin=dict(l=80, r=40, t=80, b=80),
        height=550,
    )

    return fig


def create_spread_heatmap(spreads_df: pd.DataFrame) -> go.Figure:
    """Heatmap thứ hai: term spread theo thời gian (bar chart dạng heatmap)."""
    df = spreads_df[["date", "spread_10y_2y"]].dropna().copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    colors = np.where(df["spread_10y_2y"] >= 0, "#22c55e", "#ef4444")

    fig = go.Figure(data=[
        go.Bar(
            x=df["date"],
            y=df["spread_10y_2y"] * 100,  # basis points
            marker_color=colors.tolist(),
            opacity=0.8,
            name="Spread 10Y-2Y",
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Spread: %{y:.1f} bps<extra></extra>",
        )
    ])

    fig.add_hline(
        y=0, line_dash="solid",
        line_color="rgba(255,255,255,0.4)", line_width=1,
        annotation_text="Ngưỡng đảo ngược",
        annotation_font_color="#94a3b8",
    )

    fig.update_layout(
        title=dict(
            text="Term Spread 10Y-2Y TPCP (basis points)",
            x=0.5, xanchor="center",
            font=dict(size=16, color="#f1f5f9"),
        ),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="rgba(148,163,184,0.1)"),
        yaxis=dict(
            title="Spread (bps)",
            titlefont=dict(color="#94a3b8"),
            tickfont=dict(color="#94a3b8"),
            gridcolor="rgba(148,163,184,0.1)",
            zerolinecolor="rgba(255,255,255,0.3)",
        ),
        showlegend=False,
        font=dict(family="Inter, sans-serif"),
        height=300,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return fig


def run(
    cache_dir: str | Path = "./cache",
    output: str | None = None,
    freq: str = "ME",
    verbose: bool = True,
) -> str:
    """Tạo và xuất heatmap."""
    cache_dir = Path(cache_dir)
    curve_df = load_curve_data(cache_dir)

    pivot, date_labels, tenor_labels = build_heatmap_matrix(curve_df, freq=freq)
    fig = create_heatmap_figure(pivot, date_labels, tenor_labels)

    # Ghép thêm spread chart bên dưới nếu có
    spread_file = cache_dir / "spread_analysis.parquet"
    if spread_file.exists():
        from plotly.subplots import make_subplots
        spreads = pd.read_parquet(spread_file)
        fig_spread = create_spread_heatmap(spreads)

        # Combine
        combined = make_subplots(
            rows=2, cols=1,
            row_heights=[0.7, 0.3],
            vertical_spacing=0.08,
            subplot_titles=["Lợi suất theo tenor × thời gian",
                            "Term Spread 10Y-2Y (bps)"],
        )
        for trace in fig.data:
            combined.add_trace(trace, row=1, col=1)
        for trace in fig_spread.data:
            combined.add_trace(trace, row=2, col=1)
        combined.update_layout(
            paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
            font=dict(family="Inter, sans-serif", color="#e2e8f0"),
            title=dict(
                text="Phân Tích Lợi Suất TPCP Việt Nam",
                x=0.5, xanchor="center",
                font=dict(size=22, color="#f1f5f9"),
            ),
            height=900, showlegend=False,
        )
        combined.add_hline(y=0, row=2, col=1,
                           line_color="rgba(255,255,255,0.4)")
        fig = combined

    if output is None:
        exports_dir = Path("exports")
        exports_dir.mkdir(exist_ok=True)
        output = str(exports_dir / "heatmap_yield.html")

    pio.write_html(fig, output, include_plotlyjs="cdn", full_html=True)
    if verbose:
        print(f"Đã lưu: {output}")

    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Vẽ heatmap lợi suất TPCP")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--output", default=None)
    p.add_argument("--freq", default="ME", help="Tần suất: D/W/ME")
    args = p.parse_args()
    run(args.cache_dir, output=args.output, freq=args.freq)
