"""
spread_analysis.py — Phân tích spread và term premium trái phiếu chính phủ VN.
================================================================================

Các chỉ báo được tính:
    1. Term spread (10Y-2Y, 10Y-3M, 5Y-2Y)
       → Đo độ dốc đường cong, dự báo chu kỳ kinh tế
    2. 2s5s10s Butterfly spread
       → Đo độ cong; phát hiện bóp méo thị trường
    3. Cảnh báo đảo ngược đường cong (inverted yield curve)
       → Signal lịch sử đáng tin cậy nhất về suy thoái
    4. Lịch sử rolling spreads
       → Phát hiện xu hướng và điểm cực trị

Nguồn dữ liệu:
    - Đường cong fit từ analysis/yield_curve_fitting.py (ưu tiên)
    - Hoặc trực tiếp từ combined_tpcp_yields.parquet (fallback)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ── Hàm tính spread từ đường cong đã fit ─────────────────────────────────────

def compute_spreads_from_curve(curve_df: pd.DataFrame) -> pd.DataFrame:
    """Tính các spread từ DataFrame đường cong đã fit (long format).

    Parameters
    ----------
    curve_df : pd.DataFrame
        Columns: date, tenor_yr, yield_pct (output của yield_curve_fitting.py)

    Returns
    -------
    DataFrame với các cột spread theo ngày
    """
    # Pivot: date × tenor_yr → yield_pct
    pivot = curve_df.pivot_table(index="date", columns="tenor_yr", values="yield_pct")
    pivot.columns = [f"y_{c}yr" for c in pivot.columns]
    pivot = pivot.sort_index()

    result = pd.DataFrame(index=pivot.index)
    result.index.name = "date"

    # ── Term spreads ─────────────────────────────────────────────────────────
    # Hàm helper: tìm cột gần nhất với tenor yêu cầu
    def _nearest_col(target_yr: float) -> str | None:
        cols = {float(c.replace("y_", "").replace("yr", "")): c
                for c in pivot.columns}
        if not cols:
            return None
        nearest = min(cols.keys(), key=lambda x: abs(x - target_yr))
        return cols[nearest]

    y2_col = _nearest_col(2.0)
    y5_col = _nearest_col(5.0)
    y10_col = _nearest_col(10.0)
    y03m_col = _nearest_col(0.25)
    y1_col = _nearest_col(1.0)
    y30_col = _nearest_col(30.0)

    if y10_col and y2_col:
        result["spread_10y_2y"] = pivot[y10_col] - pivot[y2_col]
    if y10_col and y03m_col:
        result["spread_10y_3m"] = pivot[y10_col] - pivot[y03m_col]
    if y5_col and y2_col:
        result["spread_5y_2y"] = pivot[y5_col] - pivot[y2_col]
    if y10_col and y1_col:
        result["spread_10y_1y"] = pivot[y10_col] - pivot[y1_col]
    if y30_col and y10_col:
        result["spread_30y_10y"] = pivot[y30_col] - pivot[y10_col]

    # ── 2s5s10s Butterfly ────────────────────────────────────────────────────
    if all(c for c in [y2_col, y5_col, y10_col]):
        result["butterfly_2s5s10s"] = (
            2 * pivot[y5_col] - pivot[y2_col] - pivot[y10_col]
        )

    # ── Cờ đảo ngược ────────────────────────────────────────────────────────
    if "spread_10y_2y" in result:
        result["inverted_10y2y"] = result["spread_10y_2y"] < 0
    if "spread_10y_3m" in result:
        result["inverted_10y3m"] = result["spread_10y_3m"] < 0

    # ── Cột phụ trợ: mức yield điểm neo ─────────────────────────────────────
    if y10_col:
        result["y_10y"] = pivot[y10_col]
    if y2_col:
        result["y_2y"] = pivot[y2_col]
    if y5_col:
        result["y_5y"] = pivot[y5_col]
    if y03m_col:
        result["y_3m"] = pivot[y03m_col]

    return result.reset_index()


def compute_spreads_direct(combined_parquet: str | Path,
                            rate_type: str = "spot_annual") -> pd.DataFrame:
    """Tính spread trực tiếp từ combined_tpcp_yields.parquet (không cần fit).

    Ít chính xác hơn fitting nhưng nhanh hơn và không yêu cầu scipy.
    Dùng khi chỉ cần tổng quan nhanh.
    """
    from analysis.yield_curve_fitting import tenor_to_years  # type: ignore

    df = pd.read_parquet(combined_parquet)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["rate_type"] == rate_type].copy()

    # Map tenor → năm
    from analysis.yield_curve_fitting import tenor_to_years as t2y
    df["tenor_yr"] = df["tenor"].map(t2y)
    df = df.dropna(subset=["tenor_yr", "value"])

    pivot = df.groupby(["date", "tenor_yr"])["value"].mean().unstack("tenor_yr")
    result = pd.DataFrame(index=pivot.index)

    # Tìm cột gần nhất
    def _near(target):
        if pivot.empty:
            return None
        diffs = (pivot.columns - target).abs()
        best = diffs.idxmin()
        return best if diffs[best] < 0.5 else None

    c2 = _near(2.0); c5 = _near(5.0); c10 = _near(10.0)
    c3m = _near(0.25); c1 = _near(1.0)

    if c10 and c2:
        result["spread_10y_2y"] = pivot[c10] - pivot[c2]
    if c10 and c3m:
        result["spread_10y_3m"] = pivot[c10] - pivot[c3m]
    if c5 and c2:
        result["spread_5y_2y"] = pivot[c5] - pivot[c2]
    if all(c for c in [c2, c5, c10]):
        result["butterfly_2s5s10s"] = 2 * pivot[c5] - pivot[c2] - pivot[c10]
    if "spread_10y_2y" in result:
        result["inverted_10y2y"] = result["spread_10y_2y"] < 0

    if c10:
        result["y_10y"] = pivot[c10]
    if c2:
        result["y_2y"] = pivot[c2]

    return result.reset_index()


# ── Phân tích thống kê spread ─────────────────────────────────────────────────

def spread_statistics(spreads_df: pd.DataFrame) -> pd.DataFrame:
    """Thống kê mô tả các spread.

    Returns
    -------
    DataFrame với percentile, mean, std, hiện tại, z-score
    """
    numeric_cols = [c for c in spreads_df.columns
                    if c.startswith("spread_") or c.startswith("butterfly_")]
    rows = []
    for col in numeric_cols:
        s = spreads_df[col].dropna()
        if len(s) < 10:
            continue
        current = s.iloc[-1]
        z_score = (current - s.mean()) / s.std() if s.std() > 0 else 0
        rows.append({
            "spread": col,
            "current_bps": round(current * 100, 1),
            "mean_bps": round(s.mean() * 100, 1),
            "std_bps": round(s.std() * 100, 1),
            "pct_5": round(s.quantile(0.05) * 100, 1),
            "pct_25": round(s.quantile(0.25) * 100, 1),
            "pct_75": round(s.quantile(0.75) * 100, 1),
            "pct_95": round(s.quantile(0.95) * 100, 1),
            "z_score": round(z_score, 2),
            "n_days": len(s),
        })
    return pd.DataFrame(rows)


def inversion_history(spreads_df: pd.DataFrame,
                       spread_col: str = "spread_10y_2y") -> pd.DataFrame:
    """Lịch sử các giai đoạn đảo ngược đường cong.

    Trả về DataFrame mỗi dòng là một giai đoạn đảo ngược với:
    start, end, duration_days, min_spread_bps
    """
    if spread_col not in spreads_df.columns:
        return pd.DataFrame()

    df = spreads_df[["date", spread_col]].dropna().sort_values("date")
    df = df.reset_index(drop=True)
    df["inverted"] = df[spread_col] < 0

    periods = []
    in_inv = False
    start_d = None
    min_s = 0.0

    for _, row in df.iterrows():
        if row["inverted"] and not in_inv:
            in_inv = True
            start_d = row["date"]
            min_s = row[spread_col]
        elif row["inverted"] and in_inv:
            min_s = min(min_s, row[spread_col])
        elif not row["inverted"] and in_inv:
            end_d = row["date"]
            dur = (pd.Timestamp(end_d) - pd.Timestamp(start_d)).days
            periods.append({
                "start": start_d,
                "end": end_d,
                "duration_days": dur,
                "min_spread_bps": round(min_s * 100, 1),
            })
            in_inv = False

    return pd.DataFrame(periods)


# ── Rolling spread analysis ───────────────────────────────────────────────────

def rolling_percentile_rank(spreads_df: pd.DataFrame,
                             col: str = "spread_10y_2y",
                             window: int = 252) -> pd.Series:
    """Rank phần trăm rolling của spread trong cửa sổ 252 ngày làm việc.

    Giá trị 0.95 = spread hiện tại cao hơn 95% lịch sử gần đây.
    Giá trị < 0.10 = spread thấp bất thường — tín hiệu thắt chặt mạnh.
    """
    s = spreads_df.set_index("date")[col].dropna()
    return s.rolling(window, min_periods=min(window // 4, 30)).rank(pct=True)


# ── Export ────────────────────────────────────────────────────────────────────

def run(
    cache_dir: str | Path = "./cache",
    use_fitted: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Tính toàn bộ spread và lưu kết quả.

    Parameters
    ----------
    cache_dir : str | Path
        Thư mục cache chứa Parquet
    use_fitted : bool
        True → dùng đường cong đã fit (chính xác hơn).
        False → tính thẳng từ dữ liệu gốc (nhanh hơn, ít điểm hơn).
    """
    cache_dir = Path(cache_dir)
    out_file = cache_dir / "spread_analysis.parquet"

    if use_fitted:
        curve_parquet = cache_dir / "fitted_curve_ns.parquet"
        if not curve_parquet.exists():
            if verbose:
                print("Chưa có fitted_curve_ns.parquet, dùng direct fallback.")
            use_fitted = False
        else:
            curve_df = pd.read_parquet(curve_parquet)
            spreads = compute_spreads_from_curve(curve_df)

    if not use_fitted:
        combined = cache_dir / "combined_tpcp_yields.parquet"
        if not combined.exists():
            raise FileNotFoundError(f"Không tìm thấy {combined}")
        spreads = compute_spreads_direct(combined)

    spreads.to_parquet(out_file, index=False)

    if verbose:
        print(f"Đã lưu spread_analysis.parquet: {len(spreads)} ngày")
        stats = spread_statistics(spreads)
        if len(stats):
            print("\nThống kê spread hiện tại:")
            print(stats.to_string(index=False))

    return spreads


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Tính spread đường cong lợi suất TPCP")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--no-fitted", action="store_true")
    args = p.parse_args()
    run(args.cache_dir, use_fitted=not args.no_fitted)
