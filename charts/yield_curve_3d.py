"""
yield_curve_3d.py — Đường cong lợi suất 3D theo thời gian (Plotly Surface).
=============================================================================

Biểu đồ 3D surface cho thấy TOÀN BỘ sự dịch chuyển của đường cong
lợi suất TPCP theo thời gian — điều không thể thấy được trên biểu đồ 2D:
    - Trục X: Kỳ hạn (tenor, năm)
    - Trục Y: Thời gian (ngày)
    - Trục Z: Lợi suất (%)

Gọi cách dùng:
    python charts/yield_curve_3d.py --cache-dir ./cache
    python charts/yield_curve_3d.py --from 2022-01-01 --output exports/3d_surface.html
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio


def load_fitted_curve(cache_dir: Path, method: str = "ns") -> pd.DataFrame:
    """Nạp đường cong đã fit từ cache."""
    f = cache_dir / f"fitted_curve_{method}.parquet"
    if not f.exists():
        raise FileNotFoundError(
            f"Chưa có {f}.\n"
            "Hãy chạy: python analysis/yield_curve_fitting.py --cache-dir ./cache"
        )
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["date"])
    return df


def build_3d_surface(
    curve_df: pd.DataFrame,
    date_from: str | None = None,
    date_to: str | None = None,
    max_dates: int = 500,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Xây dựng lưới 3D cho surface plot.

    Parameters
    ----------
    curve_df : pd.DataFrame
        Columns: date, tenor_yr, yield_pct
    date_from, date_to : str | None
        Lọc khoảng ngày
    max_dates : int
        Giới hạn số ngày để không quá nặng (lấy đều).

    Returns
    -------
    (X_tenor, Y_date_idx, Z_yield, date_labels)
    """
    df = curve_df.copy()
    if date_from:
        df = df[df["date"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["date"] <= pd.Timestamp(date_to)]

    dates = sorted(df["date"].unique())

    # Subsample nếu quá nhiều ngày
    if len(dates) > max_dates:
        step = len(dates) // max_dates
        dates = dates[::step]

    tenors = sorted(df["tenor_yr"].unique())

    # Pivot: ngày × tenor
    pivot = df[df["date"].isin(dates)].pivot_table(
        index="date", columns="tenor_yr", values="yield_pct"
    )
    pivot = pivot.reindex(columns=tenors)
    pivot = pivot.interpolate(axis=1)  # nội suy ngang để lấp NaN

    X, Y = np.meshgrid(
        np.array(tenors, dtype=float),
        np.arange(len(pivot)),
    )
    Z = pivot.values

    date_labels = [str(d)[:10] for d in pivot.index]
    return X, Y, Z, date_labels


def create_figure(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    date_labels: list[str],
    title: str = "Đường Cong Lợi Suất TPCP Việt Nam",
) -> go.Figure:
    """Tạo Plotly figure 3D surface cao cấp."""
    n_dates = len(date_labels)
    tick_step = max(1, n_dates // 10)
    y_tickvals = list(range(0, n_dates, tick_step))
    y_ticktext = [date_labels[i] for i in y_tickvals]

    fig = go.Figure(data=[
        go.Surface(
            x=X, y=Y, z=Z,
            colorscale="RdYlGn_r",   # Đỏ = cao, xanh = thấp (giống bond market)
            colorbar=dict(
                title="Lợi suất (%)",
                titlefont=dict(color="#e2e8f0"),
                tickfont=dict(color="#e2e8f0"),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.1)",
            ),
            opacity=0.9,
            contours=dict(
                z=dict(show=True, usecolormap=True,
                       project_z=True, width=1, color="rgba(255,255,255,0.3)")
            ),
            hovertemplate=(
                "<b>Kỳ hạn:</b> %{x:.1f} năm<br>"
                "<b>Lợi suất:</b> %{z:.3f}%<extra></extra>"
            ),
        )
    ])

    fig.update_layout(
        title=dict(
            text=title,
            x=0.5, xanchor="center",
            font=dict(size=20, color="#f1f5f9", family="Inter, sans-serif"),
        ),
        paper_bgcolor="#0f172a",
        scene=dict(
            bgcolor="#0f172a",
            xaxis=dict(
                title="Kỳ hạn (năm)",
                titlefont=dict(color="#94a3b8"),
                tickfont=dict(color="#94a3b8"),
                gridcolor="rgba(148,163,184,0.15)",
                showbackground=True,
                backgroundcolor="#1e293b",
            ),
            yaxis=dict(
                title="Thời gian",
                titlefont=dict(color="#94a3b8"),
                tickfont=dict(color="#94a3b8", size=9),
                tickvals=y_tickvals,
                ticktext=y_ticktext,
                gridcolor="rgba(148,163,184,0.15)",
                showbackground=True,
                backgroundcolor="#1e293b",
            ),
            zaxis=dict(
                title="Lợi suất (%)",
                titlefont=dict(color="#94a3b8"),
                tickfont=dict(color="#94a3b8"),
                gridcolor="rgba(148,163,184,0.15)",
                showbackground=True,
                backgroundcolor="#1e293b",
            ),
            camera=dict(
                eye=dict(x=1.5, y=-1.8, z=0.8)
            ),
            aspectmode="manual",
            aspectratio=dict(x=2, y=2.5, z=0.8),
        ),
        font=dict(family="Inter, sans-serif"),
        margin=dict(l=0, r=0, t=60, b=0),
        height=700,
    )

    return fig


def add_current_curve_highlight(fig: go.Figure, X, Y, Z) -> go.Figure:
    """Thêm đường cong ngày gần nhất nổi bật màu vàng."""
    last_z = Z[-1, :]
    last_x = X[0, :]
    last_y_idx = Y[-1, 0]

    fig.add_trace(go.Scatter3d(
        x=last_x,
        y=[last_y_idx] * len(last_x),
        z=last_z,
        mode="lines",
        line=dict(color="#fbbf24", width=5),
        name="Đường cong hôm nay",
        hovertemplate="<b>Hiện tại</b><br>%{x:.1f}Y: %{z:.3f}%<extra></extra>",
    ))
    return fig


def run(
    cache_dir: str | Path = "./cache",
    date_from: str | None = None,
    output: str | None = None,
    method: str = "ns",
    verbose: bool = True,
) -> str:
    """Tạo và xuất biểu đồ 3D surface.

    Returns
    -------
    str : đường dẫn file HTML output
    """
    cache_dir = Path(cache_dir)
    curve_df = load_fitted_curve(cache_dir, method=method)

    if verbose:
        print(f"Đang xây dựng 3D surface: {len(curve_df)} điểm dữ liệu...")

    X, Y, Z, date_labels = build_3d_surface(curve_df, date_from=date_from)

    date_range = f"{date_labels[0]} → {date_labels[-1]}"
    title = f"Đường Cong Lợi Suất TPCP Việt Nam ({date_range})"

    fig = create_figure(X, Y, Z, date_labels, title=title)
    fig = add_current_curve_highlight(fig, X, Y, Z)

    # Xuất file
    if output is None:
        exports_dir = Path("exports")
        exports_dir.mkdir(exist_ok=True)
        output = str(exports_dir / "yield_curve_3d.html")

    pio.write_html(fig, output, include_plotlyjs="cdn", full_html=True)
    if verbose:
        print(f"Đã lưu: {output}")

    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Vẽ đường cong lợi suất 3D")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--from", dest="date_from", default=None,
                   help="Ngày bắt đầu, vd 2020-01-01")
    p.add_argument("--output", default=None, help="Đường dẫn file HTML output")
    p.add_argument("--method", choices=["ns", "nss"], default="ns")
    args = p.parse_args()
    run(args.cache_dir, date_from=args.date_from, output=args.output, method=args.method)
