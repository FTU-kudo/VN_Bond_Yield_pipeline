"""
yield_curve_fitting.py — Nội suy và fitting đường cong lợi suất TPCP.
=======================================================================

Phương pháp:
    1. **Nelson-Siegel (NS)**: chuẩn quốc tế, dùng cho hầu hết ngân hàng
       trung ương. 4 tham số (β0, β1, β2, λ) mô tả level, slope, curvature.
    2. **Svensson (NSS)**: mở rộng NS với thêm β3, λ2 — khớp tốt hơn cho
       đường cong có bướu hoặc hình dạng phức tạp.

Tại sao cần fitting thay vì dùng thẳng số liệu HNX?
    - Số liệu HNX chỉ có tại các kỳ hạn cố định (3M, 6M, 1Y, ..., 30Y).
    - Nhiều kỳ hạn bị thiếu một số ngày.
    - Các ứng dụng Black-Scholes / cost-of-carry cần lợi suất tại kỳ hạn
      TUỲ Ý (ví dụ: 47 ngày, 2.7 năm).
    - Fitting cho đường cong TRỞ NÊN LIÊN TỤC và NHẤT QUÁN theo toán học.

Output:
    - DataFrame tham số NS/NSS theo ngày
    - DataFrame đường cong đầy đủ (grid 0.1Y → 30Y) theo ngày
    - Hàm truy vấn spot rate tại tenor tuỳ ý
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, OptimizeWarning

# ── Tenor chuẩn hoá sang năm ────────────────────────────────────────────────

_TENOR_MAP: dict[str, float] = {
    "3M": 0.25, "6M": 0.5, "9M": 0.75,
    "1Y": 1.0,  "2Y": 2.0,  "3Y": 3.0,
    "5Y": 5.0,  "7Y": 7.0,  "10Y": 10.0,
    "15Y": 15.0, "20Y": 20.0, "30Y": 30.0,
}


def tenor_to_years(tenor: str) -> float | None:
    """Chuyển ký hiệu tenor (vd '10Y', '6M') sang số năm.

    Hỗ trợ các ký hiệu HNX thực tế: '1Y', '3M', '12M', '5Y', v.v.
    Trả None nếu không nhận dạng được.
    """
    tenor = tenor.strip().upper()
    if tenor in _TENOR_MAP:
        return _TENOR_MAP[tenor]
    import re
    m = re.match(r"^(\d+(?:\.\d+)?)(Y|M)$", tenor)
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2)
    return v if unit == "Y" else v / 12


# ── Nelson-Siegel ─────────────────────────────────────────────────────────────

def nelson_siegel(t: np.ndarray, b0: float, b1: float, b2: float, lam: float) -> np.ndarray:
    """Công thức Nelson-Siegel.

    y(t) = β0 + β1·[(1-e^(-t/λ))/(t/λ)] + β2·[(1-e^(-t/λ))/(t/λ) - e^(-t/λ)]

    Diễn giải tham số:
        β0 : level (lãi suất dài hạn bền vững)
        β1 : slope (βslope âm = đường cong dốc lên bình thường)
        β2 : curvature (bướu giữa)
        λ  : tốc độ suy giảm
    """
    tau = t / lam
    factor1 = (1 - np.exp(-tau)) / tau
    factor2 = factor1 - np.exp(-tau)
    return b0 + b1 * factor1 + b2 * factor2


def nelson_siegel_svensson(
    t: np.ndarray,
    b0: float, b1: float, b2: float, b3: float,
    lam1: float, lam2: float
) -> np.ndarray:
    """Công thức Nelson-Siegel-Svensson (Svensson 1994).

    Thêm b3 và lam2 để bắt bướu thứ hai — khớp tốt hơn với đường cong
    phức tạp (thường thấy khi VN đang trong chu kỳ thắt chặt tiền tệ).
    """
    tau1 = t / lam1
    tau2 = t / lam2
    f1 = (1 - np.exp(-tau1)) / tau1
    f2 = f1 - np.exp(-tau1)
    f3 = (1 - np.exp(-tau2)) / tau2 - np.exp(-tau2)
    return b0 + b1 * f1 + b2 * f2 + b3 * f3


# ── Fit một ngày ─────────────────────────────────────────────────────────────

NS_P0 = [5.0, -2.0, 1.0, 1.5]
NS_BOUNDS = ([0, -15, -15, 0.1], [15, 15, 15, 10])

NSS_P0 = [5.0, -2.0, 1.0, 1.0, 1.5, 3.0]
NSS_BOUNDS = ([0, -15, -15, -15, 0.1, 0.1], [15, 15, 15, 15, 10, 10])


def fit_day(
    tenors_yr: np.ndarray,
    yields_pct: np.ndarray,
    method: str = "NS",
) -> dict | None:
    """Fit đường cong cho một ngày.

    Parameters
    ----------
    tenors_yr : np.ndarray
        Kỳ hạn (năm), ví dụ [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    yields_pct : np.ndarray
        Lợi suất (%), phải cùng thứ tự với tenors_yr
    method : "NS" | "NSS"
        Mô hình Nelson-Siegel hoặc Svensson

    Returns
    -------
    dict với các tham số fitted, hoặc None nếu không fit được (< 3 điểm)
    """
    mask = np.isfinite(tenors_yr) & np.isfinite(yields_pct)
    t, y = tenors_yr[mask], yields_pct[mask]
    if len(t) < 3:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OptimizeWarning)
        try:
            if method == "NS":
                popt, pcov = curve_fit(
                    nelson_siegel, t, y,
                    p0=NS_P0, bounds=NS_BOUNDS, maxfev=10000
                )
                residuals = y - nelson_siegel(t, *popt)
                return {
                    "method": "NS",
                    "b0": popt[0], "b1": popt[1], "b2": popt[2], "lam1": popt[3],
                    "rmse": float(np.sqrt(np.mean(residuals**2))),
                    "n_points": len(t),
                }
            else:  # NSS
                popt, pcov = curve_fit(
                    nelson_siegel_svensson, t, y,
                    p0=NSS_P0, bounds=NSS_BOUNDS, maxfev=20000
                )
                residuals = y - nelson_siegel_svensson(t, *popt)
                return {
                    "method": "NSS",
                    "b0": popt[0], "b1": popt[1], "b2": popt[2], "b3": popt[3],
                    "lam1": popt[4], "lam2": popt[5],
                    "rmse": float(np.sqrt(np.mean(residuals**2))),
                    "n_points": len(t),
                }
        except (RuntimeError, ValueError):
            return None


def predict_yield(params: dict, tenor_yr: float) -> float | None:
    """Tính lợi suất tại tenor tuỳ ý từ tham số đã fit.

    Ví dụ: predict_yield(params, 2.5) → lợi suất kỳ hạn 2.5 năm
    Trả None nếu params là None hoặc tenor <= 0.
    """
    if params is None or tenor_yr <= 0:
        return None
    t = np.array([tenor_yr])
    try:
        if params["method"] == "NS":
            return float(nelson_siegel(t, params["b0"], params["b1"],
                                       params["b2"], params["lam1"])[0])
        else:
            return float(nelson_siegel_svensson(
                t, params["b0"], params["b1"], params["b2"], params["b3"],
                params["lam1"], params["lam2"]
            )[0])
    except Exception:
        return None


# ── Fit toàn bộ lịch sử ──────────────────────────────────────────────────────

GRID_TENORS = np.array([
    0.08, 0.17, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0,
    5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 15.0, 20.0, 30.0
])


def fit_all_history(
    combined_parquet: str | Path,
    method: str = "NS",
    rate_types: list[str] | None = None,
    cache_dir: str | Path = "./cache",
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit đường cong cho toàn bộ lịch sử từ file reconcile.

    Parameters
    ----------
    combined_parquet : str | Path
        Đường dẫn đến combined_tpcp_yields.parquet (output của reconcile.py)
    method : "NS" | "NSS"
        Mô hình fitting
    rate_types : list[str] | None
        Lọc rate_type. Mặc định: ưu tiên "spot_annual" > "primary_winning_yield"
    cache_dir : str | Path
        Thư mục lưu kết quả fitting
    verbose : bool
        In tiến độ

    Returns
    -------
    params_df : DataFrame các tham số NS/NSS theo ngày
    curve_df  : DataFrame đường cong đầy đủ (long format) theo ngày
    """
    df = pd.read_parquet(combined_parquet)
    df["date"] = pd.to_datetime(df["date"])

    # Ưu tiên nguồn dữ liệu
    if rate_types is None:
        priority = ["spot_annual", "spot_continuous", "primary_winning_yield"]
        rate_types = [r for r in priority if r in df["rate_type"].unique()]

    df = df[df["rate_type"].isin(rate_types)].copy()
    df["tenor_yr"] = df["tenor"].map(tenor_to_years)
    df = df.dropna(subset=["tenor_yr", "value"])

    dates = sorted(df["date"].unique())
    params_rows, curve_rows = [], []

    for i, d in enumerate(dates):
        sub = df[df["date"] == d].groupby("tenor_yr")["value"].mean()
        t_arr = np.array(sub.index, dtype=float)
        y_arr = np.array(sub.values, dtype=float)

        params = fit_day(t_arr, y_arr, method=method)
        if params:
            row = {"date": d, **params}
            params_rows.append(row)

            # Tính đường cong liên tục trên grid
            for tenor in GRID_TENORS:
                y_hat = predict_yield(params, tenor)
                if y_hat is not None and 0 < y_hat < 30:
                    curve_rows.append({
                        "date": d,
                        "tenor_yr": tenor,
                        "yield_pct": y_hat,
                        "method": method,
                    })

        if verbose and (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(dates)}] fit xong {d.date()}")

    params_df = pd.DataFrame(params_rows)
    curve_df = pd.DataFrame(curve_rows)

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if len(params_df):
        params_df.to_parquet(cache_dir / f"fitted_params_{method.lower()}.parquet", index=False)
    if len(curve_df):
        curve_df.to_parquet(cache_dir / f"fitted_curve_{method.lower()}.parquet", index=False)

    if verbose:
        ok = len(params_df)
        total = len(dates)
        print(f"\nFitting xong: {ok}/{total} ngày có đủ điểm ({100*ok/total:.1f}%)")
        if len(params_df):
            print(f"RMSE trung bình: {params_df['rmse'].mean():.4f}%")

    return params_df, curve_df


# ── Truy vấn nhanh ───────────────────────────────────────────────────────────

class YieldCurve:
    """Wrapper tiện lợi để truy vấn đường cong lợi suất đã fit.

    Ví dụ sử dụng:
        vc = YieldCurve.from_cache("./cache")
        vc.spot("2026-09-05", 2.5)   # → lợi suất kỳ hạn 2.5 năm ngày 05/09/2026
        vc.term_spread("2026-09-05")  # → spread 10Y-2Y
    """

    def __init__(self, params_df: pd.DataFrame, method: str = "NS"):
        self._params = params_df.copy()
        self._params["date"] = pd.to_datetime(self._params["date"])
        self._params = self._params.set_index("date")
        self._method = method

    @classmethod
    def from_cache(cls, cache_dir: str | Path = "./cache", method: str = "NS") -> "YieldCurve":
        """Nạp từ file Parquet đã lưu."""
        p = Path(cache_dir) / f"fitted_params_{method.lower()}.parquet"
        if not p.exists():
            raise FileNotFoundError(
                f"Chưa có file {p}. Hãy chạy fit_all_history() trước."
            )
        return cls(pd.read_parquet(p), method=method)

    def _params_for(self, date_like) -> dict | None:
        """Lấy tham số của ngày gần nhất có dữ liệu."""
        d = pd.Timestamp(date_like)
        if d in self._params.index:
            return self._params.loc[d].to_dict()
        # Lùi về ngày gần nhất trong quá khứ
        past = self._params.index[self._params.index <= d]
        if len(past) == 0:
            return None
        return self._params.loc[past[-1]].to_dict()

    def spot(self, date_like, tenor_yr: float) -> float | None:
        """Lợi suất spot tại tenor tuỳ ý (năm)."""
        p = self._params_for(date_like)
        return predict_yield(p, tenor_yr)

    def full_curve(self, date_like, tenors: np.ndarray | None = None) -> pd.DataFrame:
        """Toàn bộ đường cong tại một ngày → DataFrame (tenor_yr, yield_pct)."""
        if tenors is None:
            tenors = GRID_TENORS
        p = self._params_for(date_like)
        if p is None:
            return pd.DataFrame(columns=["tenor_yr", "yield_pct"])
        rows = []
        for t in tenors:
            y = predict_yield(p, t)
            if y is not None:
                rows.append({"tenor_yr": t, "yield_pct": y})
        return pd.DataFrame(rows)

    def term_spread(self, date_like, short_yr: float = 2.0, long_yr: float = 10.0) -> float | None:
        """Term spread = lợi suất dài hạn - lợi suất ngắn hạn (%).

        Mặc định: 10Y - 2Y (chỉ báo chu kỳ kinh tế quan trọng nhất).
        Giá trị âm → đường cong đảo ngược (inversion) → cảnh báo suy thoái.
        """
        y_long = self.spot(date_like, long_yr)
        y_short = self.spot(date_like, short_yr)
        if y_long is None or y_short is None:
            return None
        return y_long - y_short

    def butterfly(self, date_like) -> float | None:
        """2s5s10s butterfly spread = 2×5Y - 2Y - 10Y.

        Đo độ cong của đường cong. Dương = bướu cao ở giữa.
        """
        y2 = self.spot(date_like, 2.0)
        y5 = self.spot(date_like, 5.0)
        y10 = self.spot(date_like, 10.0)
        if any(x is None for x in [y2, y5, y10]):
            return None
        return 2 * y5 - y2 - y10


if __name__ == "__main__":
    # Ví dụ sử dụng
    import argparse

    p = argparse.ArgumentParser(description="Fit đường cong lợi suất Nelson-Siegel")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--method", choices=["NS", "NSS"], default="NS")
    args = p.parse_args()

    combined = Path(args.cache_dir) / "combined_tpcp_yields.parquet"
    if not combined.exists():
        print(f"Chưa có {combined}. Hãy chạy reconcile.py trước.")
    else:
        params_df, curve_df = fit_all_history(
            combined, method=args.method, cache_dir=args.cache_dir
        )
        print(f"\nKết quả lưu tại {args.cache_dir}/")
        print(params_df.tail())
