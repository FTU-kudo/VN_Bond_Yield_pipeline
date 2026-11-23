"""
spread_analysis.py — Phân tích Spread, Term Premium & Động học Đường cong TPCP Việt Nam.
=========================================================================================

Các chỉ báo chuyên sâu chuẩn Bàn giao dịch & Nghiên cứu Vĩ mô:
    1. **Term Spreads toàn diện**:
       ``10Y-2Y``, ``10Y-1Y``, ``10Y-3M``, ``10Y-5Y``, ``5Y-2Y``, ``15Y-10Y``, ``30Y-10Y``.
    2. **Butterfly Spreads (Curvature Proxies)**:
       ``2s5s10s`` ($2 Y_{5Y} - Y_{2Y} - Y_{10Y}$) và ``5s10s15s`` ($2 Y_{10Y} - Y_{5Y} - Y_{15Y}$).
    3. **Phân loại Trạng thái Đường cong (Curve Regime Classification)** *(hấp thụ & nâng cấp từ
       VN_bond_analytics)*:
       - ``Inverted (Đảo ngược)``: Spread 10Y-2Y < 0 bps
       - ``Flat (Phẳng / Thắt chặt)``: 0 <= Spread 10Y-2Y < 25 bps
       - ``Normal (Dốc lên chuẩn)``: 25 <= Spread 10Y-2Y <= 100 bps
       - ``Steep (Dốc mạnh / Nới lỏng)``: Spread 10Y-2Y > 100 bps
    4. **Giải phẫu Động học Đường cong 3 Nhân tố (Level, Slope, Curvature & PCA)**:
       - Phân tích thành phần chính (Eigenvalue / SVD Principal Component Analysis) trên biến động
         lợi suất hàng ngày $\\Delta y_t$ của các kỳ hạn chuẩn HNX để bóc tách tỷ trọng phương sai giải thích
         bởi **PC1 (Parallel Shift / Level)**, **PC2 (Twist / Slope)**, và **PC3 (Butterfly / Curvature)**.
       - Phân loại chế độ dịch chuyển từng phiên/tuần/tháng:
         ``Bull Steepening``, ``Bull Flattening``, ``Bear Steepening``, ``Bear Flattening``.
    5. **Đo lường Biến động Lợi suất Thực tế (Realized Yield Volatility)**:
       Độ lệch chuẩn biến động lợi suất chuẩn hóa theo năm (bps/năm) trên cửa sổ 20D và 60D
       ($\\sigma_{\\text{bps}} = \\text{std}(\\Delta y_{\\text{bps}}) \\times \\sqrt{252}$).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def classify_curve_regime(spread_pct: pd.Series) -> pd.Series:
    """Phân loại trạng thái độ dốc đường cong dựa trên chênh lệch kỳ hạn (đơn vị %)."""
    conds = [
        spread_pct < 0.0,
        (spread_pct >= 0.0) & (spread_pct < 0.25),
        (spread_pct >= 0.25) & (spread_pct <= 1.00),
        spread_pct > 1.00,
    ]
    labels = [
        "Inverted (Đảo ngược)",
        "Flat (Phẳng)",
        "Normal (Bình thường)",
        "Steep (Dốc mạnh)",
    ]
    return pd.Series(
        np.select(conds, labels, default="Unknown"),
        index=spread_pct.index,
        dtype="object",
    )


def classify_shift_regime(dy_short: pd.Series, dy_long: pd.Series) -> pd.Series:
    """Phân loại 4 chế độ dịch chuyển đường cong kinh điển của bàn giao dịch Fixed Income:
    - Bull Steepening: Lãi suất giảm, đầu ngắn giảm mạnh hơn đầu dài (NHNN hạ lãi suất / bơm thanh khoản).
    - Bull Flattening: Lãi suất giảm, đầu dài giảm mạnh hơn đầu ngắn (kỳ vọng lạm phát hạ nhiệt / cầu bảo hiểm-NH mạnh).
    - Bear Steepening: Lãi suất tăng, đầu dài tăng nhanh hơn đầu ngắn (áp lực cung sơ cấp kỳ hạn dài / kỳ vọng lạm phát).
    - Bear Flattening: Lãi suất tăng, đầu ngắn tăng nhanh hơn đầu dài (NHNN hút tín phiếu / căng thẳng thanh khoản OMO).
    """
    d_slope = dy_long - dy_short
    avg_level = 0.5 * (dy_short + dy_long)
    conds = [
        (avg_level < -1e-4) & (d_slope > 0),
        (avg_level < -1e-4) & (d_slope <= 0),
        (avg_level > 1e-4) & (d_slope > 0),
        (avg_level > 1e-4) & (d_slope <= 0),
    ]
    labels = [
        "Bull Steepening",
        "Bull Flattening",
        "Bear Steepening",
        "Bear Flattening",
    ]
    return pd.Series(
        np.select(conds, labels, default="Unchanged"),
        index=dy_short.index,
        dtype="object",
    )


def compute_spreads_from_curve(curve_df: pd.DataFrame) -> pd.DataFrame:
    """Tính toàn bộ hệ thống Term Spread, Butterfly, Empirical Level/Slope/Curvature và Realized Volatility."""
    pivot = curve_df.pivot_table(index="date", columns="tenor_yr", values="yield_pct")
    pivot = pivot.sort_index()

    def _nearest_col(target_yr: float) -> float | None:
        if pivot.empty:
            return None
        nearest = min(pivot.columns, key=lambda x: abs(float(x) - target_yr))
        return nearest if abs(float(nearest) - target_yr) < 0.35 else None

    c3m = _nearest_col(0.25)
    c1 = _nearest_col(1.0)
    c2 = _nearest_col(2.0)
    c3 = _nearest_col(3.0)
    c5 = _nearest_col(5.0)
    c7 = _nearest_col(7.0)
    c10 = _nearest_col(10.0)
    c15 = _nearest_col(15.0)
    c20 = _nearest_col(20.0)
    c30 = _nearest_col(30.0)

    result = pd.DataFrame(index=pivot.index)
    result.index.name = "date"

    # ── Term spreads (đơn vị: % năm) ─────────────────────────────────────────
    if c10 is not None and c2 is not None:
        result["spread_10y_2y"] = pivot[c10] - pivot[c2]
    if c10 is not None and c3m is not None:
        result["spread_10y_3m"] = pivot[c10] - pivot[c3m]
    if c10 is not None and c1 is not None:
        result["spread_10y_1y"] = pivot[c10] - pivot[c1]
    if c10 is not None and c5 is not None:
        result["spread_10y_5y"] = pivot[c10] - pivot[c5]
    if c5 is not None and c2 is not None:
        result["spread_5y_2y"] = pivot[c5] - pivot[c2]
    if c15 is not None and c10 is not None:
        result["spread_15y_10y"] = pivot[c15] - pivot[c10]
    if c30 is not None and c10 is not None:
        result["spread_30y_10y"] = pivot[c30] - pivot[c10]

    # ── Butterfly Curvature Spreads ──────────────────────────────────────────
    if all(x is not None for x in (c2, c5, c10)):
        result["butterfly_2s5s10s"] = 2.0 * pivot[c5] - pivot[c2] - pivot[c10]
    if all(x is not None for x in (c5, c10, c15)):
        result["butterfly_5s10s15s"] = 2.0 * pivot[c10] - pivot[c5] - pivot[c15]

    # ── Cờ đảo ngược & phân loại trạng thái đường cong ───────────────────────
    if "spread_10y_2y" in result:
        result["inverted_10y2y"] = result["spread_10y_2y"] < 0
        result["curve_regime"] = classify_curve_regime(result["spread_10y_2y"])
    if "spread_10y_3m" in result:
        result["inverted_10y3m"] = result["spread_10y_3m"] < 0

    # ── Các mốc lợi suất chuẩn neo (Anchor Tenor Yields) ─────────────────────
    tenor_exports = [
        (c3m, "y_3m"), (c1, "y_1y"), (c2, "y_2y"), (c3, "y_3y"),
        (c5, "y_5y"), (c7, "y_7y"), (c10, "y_10y"), (c15, "y_15y"),
        (c20, "y_20y"), (c30, "y_30y"),
    ]
    for col_src, col_dst in tenor_exports:
        if col_src is not None:
            result[col_dst] = pivot[col_src]

    # ── Nhân tố thực nghiệm Level / Slope / Curvature & Biến động 20D/60D ────
    if all(k in result for k in ("y_2y", "y_5y", "y_10y")):
        result["emp_level_pct"] = (result["y_2y"] + result["y_5y"] + result["y_10y"]) / 3.0
        result["emp_slope_pct"] = result["y_10y"] - result["y_2y"]
        result["emp_curvature_pct"] = 2.0 * result["y_5y"] - (result["y_2y"] + result["y_10y"])

        dy2_1d = result["y_2y"].diff()
        dy10_1d = result["y_10y"].diff()
        result["shift_regime_1d"] = classify_shift_regime(dy2_1d, dy10_1d)
        result["shift_regime_20d"] = classify_shift_regime(
            result["y_2y"].diff(20), result["y_10y"].diff(20)
        )

    if "y_10y" in result:
        dy10_bps = result["y_10y"].diff() * 100.0
        result["vol_10y_20d_bps"] = dy10_bps.rolling(20, min_periods=10).std() * np.sqrt(252.0)
        result["vol_10y_60d_bps"] = dy10_bps.rolling(60, min_periods=20).std() * np.sqrt(252.0)

    return result.reset_index()


def compute_spreads_direct(
    combined_parquet: str | Path,
    rate_type: str = "spot_annual",
) -> pd.DataFrame:
    """Tính spread trực tiếp từ bảng dữ liệu gốc (không qua bước fitting)."""
    from analysis.yield_curve_fitting import extract_clean_spot_panel

    raw_df = pd.read_parquet(combined_parquet)
    pivot = extract_clean_spot_panel(raw_df, rate_priority=[rate_type])
    if pivot.empty:
        return pd.DataFrame()

    long_df = (
        pivot.reset_index()
        .melt(id_vars="date", var_name="tenor_yr", value_name="yield_pct")
        .dropna()
    )
    return compute_spreads_from_curve(long_df)


def compute_yield_curve_pca(
    curve_df: pd.DataFrame,
    tenors_yr: list[float] | None = None,
    use_changes: bool = True,
) -> dict:
    """Phân tích Thành phần Chính (PCA via SVD) của biến động đường cong lợi suất TPCP Việt Nam.

    Trên thị trường trái phiếu chính phủ toàn cầu (Litterman & Scheinkman, 1991) và tại Việt Nam,
    3 thành phần chính đầu tiên giải thích > 98% tổng phương sai biến động đường cong:
        - **PC1 (Level / Parallel Shift)**: Cùng dấu trên mọi kỳ hạn.
        - **PC2 (Slope / Steepening-Flattening Twist)**: Trái dấu giữa đầu ngắn (<= 2Y) và đầu dài (>= 10Y).
        - **PC3 (Curvature / Butterfly Flex)**: Dương ở bụng (3Y–7Y), âm ở hai cánh (1Y và 15Y–20Y).
    """
    if tenors_yr is None:
        tenors_yr = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]

    pivot = curve_df.pivot_table(index="date", columns="tenor_yr", values="yield_pct").sort_index()
    avail_cols = []
    col_labels = []
    for t in tenors_yr:
        if pivot.empty:
            break
        nearest = min(pivot.columns, key=lambda c: abs(float(c) - t))
        if abs(float(nearest) - t) < 0.2 and nearest not in avail_cols:
            avail_cols.append(nearest)
            col_labels.append(float(nearest))

    mat_df = pivot[avail_cols].dropna()
    if use_changes:
        mat_df = mat_df.diff().dropna()
        # Loại bỏ các bước nhảy do gãy ngày nghỉ dài hoặc nhiễu dữ liệu đầu kỳ (> 150 bps/ngày)
        mat_df = mat_df[(mat_df.abs() <= 1.5).all(axis=1)]

    if len(mat_df) < 30 or len(avail_cols) < 3:
        return {}

    X = mat_df.to_numpy(dtype=float) * 100.0  # Quy ra bps
    X_centered = X - np.mean(X, axis=0, keepdims=True)
    _, s_vals, vt = np.linalg.svd(X_centered, full_matrices=False)
    variances = (s_vals ** 2) / (len(X_centered) - 1)
    explained_ratio = variances / np.sum(variances)

    components = vt[:3].copy()  # (3, K)
    # Chuẩn hóa chiều dấu trực quan kinh tế:
    # PC1 dương = lợi suất tăng toàn tuyến (Level Up)
    if np.sum(components[0]) < 0:
        components[0] *= -1.0
    # PC2 dương = độ dốc tăng (Long end - Short end > 0)
    if components[1, -1] - components[1, 0] < 0:
        components[1] *= -1.0
    # PC3 dương = bụng giữa (5Y) vồng lên so với 2 cánh
    mid_idx = len(col_labels) // 2
    if components[2, mid_idx] - 0.5 * (components[2, 0] + components[2, -1]) < 0:
        components[2] *= -1.0

    scores = X_centered @ components.T  # (N_days, 3)

    loadings_df = pd.DataFrame({
        "tenor_yr": col_labels,
        "pc1_level": np.round(components[0], 4),
        "pc2_slope": np.round(components[1], 4),
        "pc3_curvature": np.round(components[2], 4),
    })

    scores_df = pd.DataFrame({
        "date": mat_df.index,
        "pc1_level_score": np.round(scores[:, 0], 3),
        "pc2_slope_score": np.round(scores[:, 1], 3),
        "pc3_curvature_score": np.round(scores[:, 2], 3),
    })

    return {
        "explained_variance_ratio": {
            "PC1_Level": round(float(explained_ratio[0]), 4),
            "PC2_Slope": round(float(explained_ratio[1]), 4),
            "PC3_Curvature": round(float(explained_ratio[2]), 4),
            "Cumulative_3PC": round(float(np.sum(explained_ratio[:3])), 4),
        },
        "n_observations": int(len(X_centered)),
        "loadings": loadings_df,
        "scores": scores_df,
    }


def spread_statistics(spreads_df: pd.DataFrame) -> pd.DataFrame:
    """Thống kê mô tả phân phối lịch sử, phân vị & Z-score của các Term Spread và Butterfly."""
    numeric_cols = [
        c for c in spreads_df.columns
        if c.startswith("spread_") or c.startswith("butterfly_")
    ]
    rows = []
    for col in numeric_cols:
        s = spreads_df[col].dropna()
        if len(s) < 10:
            continue
        current = float(s.iloc[-1])
        std_val = float(s.std())
        z_score = (current - float(s.mean())) / std_val if std_val > 0 else 0.0
        pct_rank = float((s <= current).mean() * 100.0)
        rows.append({
            "spread": col,
            "current_bps": round(current * 100, 1),
            "mean_bps": round(float(s.mean()) * 100, 1),
            "std_bps": round(std_val * 100, 1),
            "pct_5": round(float(s.quantile(0.05)) * 100, 1),
            "pct_25": round(float(s.quantile(0.25)) * 100, 1),
            "pct_75": round(float(s.quantile(0.75)) * 100, 1),
            "pct_95": round(float(s.quantile(0.95)) * 100, 1),
            "percentile_rank": round(pct_rank, 1),
            "z_score": round(z_score, 2),
            "n_days": int(len(s)),
        })
    return pd.DataFrame(rows)


def inversion_history(
    spreads_df: pd.DataFrame,
    spread_col: str = "spread_10y_2y",
) -> pd.DataFrame:
    """Trích xuất toàn bộ các chu kỳ đảo ngược đường cong (kể cả chu kỳ đang tiếp diễn ở cuối mẫu)."""
    if spread_col not in spreads_df.columns:
        return pd.DataFrame()

    df = spreads_df[["date", spread_col]].dropna().sort_values("date").reset_index(drop=True)
    df["inverted"] = df[spread_col] < 0

    periods = []
    in_inv = False
    start_d = None
    min_s = 0.0

    for _, row in df.iterrows():
        if row["inverted"] and not in_inv:
            in_inv = True
            start_d = row["date"]
            min_s = float(row[spread_col])
        elif row["inverted"] and in_inv:
            min_s = min(min_s, float(row[spread_col]))
        elif not row["inverted"] and in_inv:
            end_d = row["date"]
            dur = (pd.Timestamp(end_d) - pd.Timestamp(start_d)).days
            periods.append({
                "start": start_d,
                "end": end_d,
                "duration_days": dur,
                "min_spread_bps": round(min_s * 100, 1),
                "ongoing": False,
            })
            in_inv = False

    if in_inv and start_d is not None:
        end_d = df["date"].iloc[-1]
        dur = (pd.Timestamp(end_d) - pd.Timestamp(start_d)).days
        periods.append({
            "start": start_d,
            "end": end_d,
            "duration_days": dur,
            "min_spread_bps": round(min_s * 100, 1),
            "ongoing": True,
        })

    return pd.DataFrame(periods)


def rolling_percentile_rank(
    spreads_df: pd.DataFrame,
    col: str = "spread_10y_2y",
    window: int = 252,
) -> pd.Series:
    """Rank phần trăm rolling trong cửa sổ ``window`` phiên giao dịch."""
    s = spreads_df.set_index("date")[col].dropna()
    return s.rolling(window, min_periods=min(window // 4, 30)).rank(pct=True)


def run(
    cache_dir: str | Path = "./cache",
    use_fitted: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Chạy phân tích Spread, Động học Đường cong & PCA và lưu vào Parquet."""
    cache_dir = Path(cache_dir)
    out_file = cache_dir / "spread_analysis.parquet"
    curve_df = None

    if use_fitted:
        curve_parquet = cache_dir / "fitted_curve_ns.parquet"
        if not curve_parquet.exists():
            if verbose:
                print("Chưa có fitted_curve_ns.parquet, chuyển sang direct fallback.")
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

    # Tính PCA động học 3 nhân tố Level / Slope / Curvature
    if curve_df is not None and len(curve_df):
        pca_res = compute_yield_curve_pca(curve_df)
        if pca_res:
            pca_res["loadings"].to_parquet(cache_dir / "curve_pca_loadings.parquet", index=False)
            pca_res["scores"].to_parquet(cache_dir / "curve_pca_scores.parquet", index=False)
            ev_df = pd.DataFrame([pca_res["explained_variance_ratio"]])
            ev_df["n_observations"] = pca_res["n_observations"]
            ev_df.to_parquet(cache_dir / "curve_pca_summary.parquet", index=False)
            if verbose:
                ev = pca_res["explained_variance_ratio"]
                print(
                    f"  PCA Đường cong ({pca_res['n_observations']} phiên): "
                    f"PC1 Level={ev['PC1_Level']*100:.1f}% | "
                    f"PC2 Slope={ev['PC2_Slope']*100:.1f}% | "
                    f"PC3 Curvature={ev['PC3_Curvature']*100:.1f}% "
                    f"(Tổng={ev['Cumulative_3PC']*100:.1f}%)"
                )

    if verbose:
        print(f"  Đã lưu spread_analysis.parquet: {len(spreads)} ngày")
        stats = spread_statistics(spreads)
        if len(stats):
            print("\n  Thống kê Spread & Butterfly hiện tại:")
            print(stats.to_string(index=False))

    return spreads


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Phân tích Spread & PCA đường cong lợi suất TPCP")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--no-fitted", action="store_true")
    args = p.parse_args()
    run(args.cache_dir, use_fitted=not args.no_fitted)
