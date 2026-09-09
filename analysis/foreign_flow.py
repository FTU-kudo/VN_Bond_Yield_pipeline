"""
foreign_flow.py — Phân tích dòng vốn khối ngoại trên thị trường TPCP.
=======================================================================

Nguồn dữ liệu:
    hnx_foreign_*.parquet — dữ liệu bóc từ PDF HNX:
    (output của vnbond.hnx.foreign() hoặc pipeline tương tự)

    Schema mỗi dòng: trade_date | tenor_label | tenor_years |
        dom_buy_vol | dom_buy_vnd | dom_sell_vol | dom_sell_vnd |
        for_buy_vol | for_buy_vnd | for_sell_vol | for_sell_vnd |
        for_net_bn

Phân tích:
    1. Net foreign flow theo tenor → kỳ hạn nào được khối ngoại ưa thích
    2. Rolling 20/30-ngày net flow → xu hướng tích lũy / tháo
    3. So sánh cường độ giao dịch: khối ngoại vs nội
    4. Tương quan net flow với thay đổi lợi suất (khối ngoại thúc đẩy yield)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def load_foreign(cache_dir: str | Path = "./cache") -> pd.DataFrame:
    """Nạp dữ liệu giao dịch theo nhà đầu tư từ cache.

    Tìm các file:
        - hnx_foreign.parquet  (output vnbond.hnx.foreign)
        - hnx_foreign_daily.parquet  (nếu có pipeline riêng)
    """
    cache_dir = Path(cache_dir)
    candidates = [
        cache_dir / "hnx_foreign.parquet",
        cache_dir / "hnx_foreign_daily.parquet",
    ]
    for f in candidates:
        if f.exists():
            df = pd.read_parquet(f)
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["trade_date"])
            return df
    raise FileNotFoundError(
        "Không tìm thấy file dữ liệu khối ngoại. "
        "Hãy chạy pipeline thu thập dữ liệu foreign trước:\n"
        "  python -c \"import vnbond as vm; vm.hnx.foreign('2020-01-01')\""
    )


def compute_daily_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Tổng hợp net flow theo ngày.

    Returns DataFrame với:
        trade_date | for_net_bn_total | for_buy_bn | for_sell_bn |
        dom_net_bn | market_total_bn | for_share_pct
    """
    grp = df.groupby("trade_date").agg(
        for_buy_vnd=("for_buy_vnd", "sum"),
        for_sell_vnd=("for_sell_vnd", "sum"),
        for_net_bn=("for_net_bn", "sum"),
        dom_buy_vnd=("dom_buy_vnd", "sum"),
        dom_sell_vnd=("dom_sell_vnd", "sum"),
    ).reset_index()

    grp["dom_net_bn"] = (grp["dom_buy_vnd"] - grp["dom_sell_vnd"]) / 1e9
    grp["market_total_bn"] = (
        grp["for_buy_vnd"] + grp["for_sell_vnd"] +
        grp["dom_buy_vnd"] + grp["dom_sell_vnd"]
    ) / 1e9
    grp["for_share_pct"] = (
        (grp["for_buy_vnd"] + grp["for_sell_vnd"]) /
        grp["market_total_bn"].replace(0, float("nan")) / 1e9 * 100
    )
    return grp.sort_values("trade_date").reset_index(drop=True)


def compute_tenor_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Net flow khối ngoại theo tenor (tổng toàn bộ lịch sử).

    Cho thấy kỳ hạn nào được NĐT nước ngoài mua ròng nhiều nhất.
    """
    if "tenor_years" not in df.columns:
        return pd.DataFrame()
    grp = df.groupby("tenor_years").agg(
        for_net_bn_total=("for_net_bn", "sum"),
        for_buy_bn=("for_buy_vnd", lambda x: x.sum() / 1e9),
        for_sell_bn=("for_sell_vnd", lambda x: x.sum() / 1e9),
        n_days=("trade_date", "nunique"),
    ).reset_index()
    grp["for_net_bn_avg_per_day"] = grp["for_net_bn_total"] / grp["n_days"]
    return grp.sort_values("tenor_years")


def rolling_net_flow(daily_df: pd.DataFrame,
                     windows: list[int] | None = None) -> pd.DataFrame:
    """Net flow rolling theo các cửa sổ thời gian (tỷ đồng).

    Parameters
    ----------
    daily_df : pd.DataFrame
        Output của compute_daily_flow()
    windows : list[int]
        Cửa sổ rolling (ngày làm việc). Mặc định: [5, 20, 60]
    """
    if windows is None:
        windows = [5, 20, 60]
    df = daily_df.set_index("trade_date").copy()
    result = df[["for_net_bn"]].copy()
    result["for_net_bn_cumsum"] = result["for_net_bn"].cumsum()

    for w in windows:
        result[f"rolling_{w}d_sum"] = result["for_net_bn"].rolling(w).sum()
        result[f"rolling_{w}d_avg"] = result["for_net_bn"].rolling(w).mean()

    return result.reset_index()


def flow_yield_correlation(
    daily_flow: pd.DataFrame,
    spreads_df: pd.DataFrame,
    lag_days: int = 5,
) -> pd.DataFrame:
    """Tương quan giữa net flow và thay đổi lợi suất (10Y).

    Kiểm tra giả thuyết: khi NĐT nước ngoài mua ròng →
    lợi suất giảm (giá tăng) sau lag_days ngày.

    Parameters
    ----------
    daily_flow : pd.DataFrame
        Output của compute_daily_flow(), phải có cột trade_date, for_net_bn
    spreads_df : pd.DataFrame
        Output của spread_analysis.py, phải có cột date, y_10y
    lag_days : int
        Độ trễ kiểm tra (ngày dương lịch)
    """
    flow = daily_flow[["trade_date", "for_net_bn"]].copy()
    flow = flow.set_index("trade_date")

    if "y_10y" not in spreads_df.columns:
        return pd.DataFrame()

    yields = spreads_df[["date", "y_10y"]].copy()
    yields = yields.set_index("date")
    yields["yield_change_1d"] = yields["y_10y"].diff()

    # Merge
    merged = flow.join(yields, how="inner")
    merged["flow_lag"] = merged["for_net_bn"].shift(lag_days)
    merged = merged.dropna()

    corr = merged[["flow_lag", "yield_change_1d"]].corr().iloc[0, 1]

    return pd.DataFrame([{
        "lag_days": lag_days,
        "correlation": round(corr, 4),
        "n_observations": len(merged),
        "interpretation": (
            "NĐT ngoại mua ròng trước, yield giảm sau" if corr < -0.1
            else "NĐT ngoại bán ròng trước, yield tăng sau" if corr > 0.1
            else "Tương quan yếu"
        )
    }])


# ── Entry point ───────────────────────────────────────────────────────────────

def run(cache_dir: str | Path = "./cache", verbose: bool = True) -> dict:
    """Chạy toàn bộ phân tích foreign flow và lưu kết quả."""
    cache_dir = Path(cache_dir)

    try:
        df = load_foreign(cache_dir)
    except FileNotFoundError as e:
        if verbose:
            print(f"CẢNH BÁO: {e}")
            print("Bỏ qua phân tích foreign flow.")
        return {}

    daily = compute_daily_flow(df)
    tenor = compute_tenor_flow(df)
    rolling = rolling_net_flow(daily)

    daily.to_parquet(cache_dir / "foreign_daily_flow.parquet", index=False)
    tenor.to_parquet(cache_dir / "foreign_tenor_flow.parquet", index=False)
    rolling.to_parquet(cache_dir / "foreign_rolling_flow.parquet", index=False)

    if verbose:
        print(f"Foreign flow: {len(daily)} ngày ({daily['trade_date'].min().date()} → "
              f"{daily['trade_date'].max().date()})")
        recent_30 = daily.tail(30)["for_net_bn"].sum()
        print(f"Net flow 30 ngày gần nhất: {recent_30:+.1f} tỷ đồng")

        if len(tenor):
            top = tenor.nlargest(3, "for_net_bn_total")
            print("\nTenor được mua ròng nhiều nhất:")
            for _, row in top.iterrows():
                print(f"  {row['tenor_years']:.1f}Y: {row['for_net_bn_total']:+.1f} tỷ")

    return {"daily": daily, "tenor": tenor, "rolling": rolling}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Phân tích dòng vốn khối ngoại TPCP")
    p.add_argument("--cache-dir", default="./cache")
    args = p.parse_args()
    run(args.cache_dir)
