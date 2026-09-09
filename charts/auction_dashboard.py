"""
auction_dashboard.py — Dashboard phân tích kết quả đấu thầu sơ cấp TPCP.
==========================================================================

Subplot 4 panel:
    1. Stop-out rate theo tenor (line chart lịch sử)
    2. Bid-to-cover ratio (bar chart, phiên thành công vs thất bại)
    3. Khối lượng phát hành theo quý theo tenor (stacked bar)
    4. Tỷ lệ thành công đấu thầu theo tenor (bar chart màu traffic light)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio

# Màu sắc chuẩn cho từng tenor
TENOR_COLORS = {
    "1Y": "#38bdf8",    # xanh nhạt
    "2Y": "#818cf8",    # tím nhạt
    "3Y": "#a78bfa",    # tím
    "5Y": "#34d399",    # xanh lá
    "7Y": "#fbbf24",    # vàng
    "10Y": "#f97316",   # cam
    "15Y": "#ef4444",   # đỏ
    "20Y": "#dc2626",   # đỏ đậm
    "30Y": "#991b1b",   # đỏ rất đậm
}

MAIN_TENORS = [1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]
TENOR_LABEL_MAP = {
    1.0: "1Y", 2.0: "2Y", 3.0: "3Y", 5.0: "5Y",
    7.0: "7Y", 10.0: "10Y", 15.0: "15Y", 20.0: "20Y", 30.0: "30Y",
}


def load_auction_data(cache_dir: Path) -> dict:
    """Nạp dữ liệu đấu thầu từ cache."""
    result = {}
    files = {
        "bcr": "auction_bcr_by_tenor.parquet",
        "success": "auction_success_rate.parquet",
        "volume": "auction_volume_by_quarter.parquet",
        "recent": "auction_recent_90d.parquet",
        "stop_10y": "auction_stop_out_10y.parquet",
    }
    for key, fname in files.items():
        f = cache_dir / fname
        if f.exists():
            result[key] = pd.read_parquet(f)
        else:
            result[key] = pd.DataFrame()

    # Nếu chưa có file phân tích, nạp raw và tính
    raw_file = cache_dir / "hnx_auctions_daily.parquet"
    if not any(len(v) for v in result.values()) and raw_file.exists():
        from analysis.auction_statistics import load_auctions, prepare_auction_df
        from analysis.auction_statistics import (
            bid_to_cover_summary, auction_success_rate,
            issuance_volume_by_quarter, stop_out_rate_history
        )
        raw = load_auctions(cache_dir)
        df = prepare_auction_df(raw)
        result["bcr"] = bid_to_cover_summary(df)
        result["success"] = auction_success_rate(df)
        result["volume"] = issuance_volume_by_quarter(df)
        result["stop_10y"] = stop_out_rate_history(df, 10.0)

    return result


def _tenor_label(yr: float) -> str:
    """Tên hiển thị cho tenor."""
    for t, label in TENOR_LABEL_MAP.items():
        if abs(yr - t) < 0.3:
            return label
    return f"{yr:.0f}Y"


def panel_stop_out_history(stop_df: pd.DataFrame, tenor_yr: float = 10.0) -> go.Figure:
    """Panel 1: Lịch sử stop-out rate và winning rate 10Y."""
    fig = go.Figure()

    if stop_df.empty:
        return fig

    date_col = [c for c in stop_df.columns if "date" in c.lower()][0]
    stop_df = stop_df.sort_values(date_col)

    if "stop_out_rate" in stop_df.columns:
        fig.add_trace(go.Scatter(
            x=stop_df[date_col],
            y=stop_df["stop_out_rate"],
            mode="lines",
            name="Stop-out rate",
            line=dict(color="#ef4444", width=1.5),
            opacity=0.8,
        ))

    if "winning_rate_pct" in stop_df.columns:
        fig.add_trace(go.Scatter(
            x=stop_df[date_col],
            y=stop_df["winning_rate_pct"],
            mode="lines",
            name="Winning rate (bình quân)",
            line=dict(color="#fbbf24", width=2),
        ))

    return fig


def panel_bcr(bcr_df: pd.DataFrame) -> go.Figure:
    """Panel 2: BCR theo tenor (bar chart)."""
    fig = go.Figure()

    if bcr_df.empty:
        return fig

    df = bcr_df.dropna(subset=["bcr_mean"]).copy()
    df["label"] = df["tenor_yr"].map(_tenor_label)
    df = df[df["tenor_yr"].isin(MAIN_TENORS)]

    colors = [TENOR_COLORS.get(_tenor_label(t), "#64748b") for t in df["tenor_yr"]]

    fig.add_trace(go.Bar(
        x=df["label"],
        y=df["bcr_mean"],
        error_y=dict(type="data", array=df["bcr_std"].fillna(0).tolist(),
                     color="rgba(255,255,255,0.3)"),
        marker_color=colors,
        marker_line_color="rgba(255,255,255,0.2)",
        marker_line_width=1,
        text=[f"{v:.2f}x" for v in df["bcr_mean"]],
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=10),
        name="BCR trung bình",
        hovertemplate="<b>%{x}</b><br>BCR: %{y:.2f}x<br>n=%{customdata}<extra></extra>",
        customdata=df["n_auctions"].tolist(),
    ))

    fig.add_hline(y=1.0, line_dash="dash",
                  line_color="rgba(255,255,255,0.3)", line_width=1,
                  annotation_text="BCR=1 (vừa đủ)",
                  annotation_font_color="#94a3b8")
    return fig


def panel_volume_quarterly(vol_df: pd.DataFrame) -> go.Figure:
    """Panel 3: Khối lượng phát hành theo quý (stacked bar)."""
    fig = go.Figure()

    if vol_df.empty:
        return fig

    vol_df = vol_df.copy()
    vol_df["label"] = vol_df["tenor_yr"].map(_tenor_label)
    vol_df = vol_df[vol_df["tenor_yr"].isin(MAIN_TENORS)]

    quarters = sorted(vol_df["quarter"].unique())[-12:]  # 12 quý gần nhất
    vol_df = vol_df[vol_df["quarter"].isin(quarters)]

    for tenor in sorted(vol_df["tenor_yr"].unique()):
        sub = vol_df[vol_df["tenor_yr"] == tenor]
        label = _tenor_label(tenor)
        color = TENOR_COLORS.get(label, "#64748b")

        q_vals = []
        for q in quarters:
            row = sub[sub["quarter"] == q]
            q_vals.append(row["win_volume_bn"].sum() if len(row) else 0)

        fig.add_trace(go.Bar(
            x=quarters,
            y=q_vals,
            name=label,
            marker_color=color,
            opacity=0.85,
            hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:.0f}} tỷ<extra></extra>",
        ))

    fig.update_layout(barmode="stack")
    return fig


def panel_success_rate(success_df: pd.DataFrame) -> go.Figure:
    """Panel 4: Tỷ lệ thành công gần nhất theo tenor (traffic light)."""
    fig = go.Figure()

    if success_df.empty:
        return fig

    # Lấy 4 quý gần nhất
    recent_quarters = sorted(success_df["quarter"].unique())[-4:]
    df = success_df[success_df["quarter"].isin(recent_quarters)]
    df = df[df["tenor_yr"].isin(MAIN_TENORS)]

    df_avg = df.groupby("tenor_yr")["success_rate"].mean().reset_index()
    df_avg["label"] = df_avg["tenor_yr"].map(_tenor_label)

    # Màu traffic light
    def rate_color(r):
        if r >= 0.8:
            return "#22c55e"
        elif r >= 0.5:
            return "#fbbf24"
        return "#ef4444"

    colors = [rate_color(r) for r in df_avg["success_rate"]]

    fig.add_trace(go.Bar(
        x=df_avg["label"],
        y=df_avg["success_rate"] * 100,
        marker_color=colors,
        text=[f"{v*100:.0f}%" for v in df_avg["success_rate"]],
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=11),
        hovertemplate="<b>%{x}</b><br>Tỷ lệ thành công: %{y:.1f}%<extra></extra>",
        name="Tỷ lệ thành công",
    ))

    return fig


def create_dashboard(data: dict) -> go.Figure:
    """Tạo dashboard 4-panel tổng hợp."""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=[
            "Stop-out Rate TPCP 10Y (lịch sử)",
            "Bid-to-Cover Ratio theo Tenor",
            "Khối Lượng Phát Hành theo Quý (tỷ đồng)",
            "Tỷ Lệ Đấu Thầu Thành Công (4 quý gần nhất)",
        ],
        vertical_spacing=0.14,
        horizontal_spacing=0.1,
    )

    # Panel 1: Stop-out rate
    p1 = panel_stop_out_history(data.get("stop_10y", pd.DataFrame()))
    for trace in p1.data:
        fig.add_trace(trace, row=1, col=1)

    # Panel 2: BCR
    p2 = panel_bcr(data.get("bcr", pd.DataFrame()))
    for trace in p2.data:
        fig.add_trace(trace, row=1, col=2)

    # Panel 3: Volume
    p3 = panel_volume_quarterly(data.get("volume", pd.DataFrame()))
    for trace in p3.data:
        fig.add_trace(trace, row=2, col=1)

    # Panel 4: Success rate
    p4 = panel_success_rate(data.get("success", pd.DataFrame()))
    for trace in p4.data:
        fig.add_trace(trace, row=2, col=2)

    # Layout chung
    axis_style = dict(
        gridcolor="rgba(148,163,184,0.1)",
        zerolinecolor="rgba(148,163,184,0.2)",
        tickfont=dict(color="#94a3b8"),
        titlefont=dict(color="#94a3b8"),
    )

    fig.update_layout(
        title=dict(
            text="Dashboard Đấu Thầu Sơ Cấp TPCP Việt Nam",
            x=0.5, xanchor="center",
            font=dict(size=22, color="#f1f5f9", family="Inter, sans-serif"),
        ),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        font=dict(family="Inter, sans-serif", color="#e2e8f0"),
        height=800,
        showlegend=True,
        legend=dict(
            bgcolor="rgba(30,41,59,0.8)",
            bordercolor="rgba(148,163,184,0.2)",
            font=dict(color="#94a3b8"),
        ),
        barmode="stack",
        margin=dict(l=50, r=50, t=100, b=60),
    )

    for i in range(1, 5):
        row, col = (1 if i <= 2 else 2), (i if i <= 2 else i - 2)
        fig.update_xaxes(axis_style, row=row, col=col)
        fig.update_yaxes(axis_style, row=row, col=col)

    # Đường BCR=1 trên panel 2
    p2_hlines = [t for t in p2.layout.shapes if hasattr(t, "y0")]
    fig.add_hline(y=1.0, row=1, col=2,
                  line_dash="dash", line_color="rgba(255,255,255,0.3)")
    return fig


def run(
    cache_dir: str | Path = "./cache",
    output: str | None = None,
    verbose: bool = True,
) -> str:
    """Tạo và xuất dashboard đấu thầu."""
    cache_dir = Path(cache_dir)
    data = load_auction_data(cache_dir)

    if all(df.empty for df in data.values()):
        raise RuntimeError(
            "Không có dữ liệu đấu thầu. "
            "Hãy chạy hnx_auctions_daily.py và auction_statistics.py trước."
        )

    fig = create_dashboard(data)

    if output is None:
        exports_dir = Path("exports")
        exports_dir.mkdir(exist_ok=True)
        output = str(exports_dir / "auction_dashboard.html")

    pio.write_html(fig, output, include_plotlyjs="cdn", full_html=True)
    if verbose:
        print(f"Đã lưu: {output}")

    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Dashboard phân tích đấu thầu TPCP")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    run(args.cache_dir, output=args.output)
