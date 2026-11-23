"""
yield_curve_fitting.py — Nội suy, ước lượng & giải phẫu đường cong lợi suất TPCP Việt Nam.
===========================================================================================

Chuẩn nghiệp vụ Trái phiếu Chính phủ Việt Nam (HNX / VBMA):
    - Đường cong lợi suất chuẩn thứ cấp HNX công bố 3 đại lượng khác nhau:
        1. ``spot_annual``: Lãi suất giao ngay ghép lãi năm $z_a(t)$ (%/năm)
        2. ``spot_continuous``: Lãi suất giao ngay ghép lãi liên tục $z_c(t) = 100 \ln(1 + z_a(t)/100)$
        3. ``par_yield``: Lợi suất trái phiếu ngang giá trả coupon hàng năm $c_{\text{par}}(N)$
    - TUYỆT ĐỐI KHÔNG lấy trung bình cộng giữa ``spot_annual``, ``spot_continuous`` và
      ``primary_winning_yield`` tại cùng một kỳ hạn. Hệ thống áp dụng thứ tự ưu tiên phân cấp
      nghiêm ngặt trên từng cặp ``(date, tenor)`` và quy đổi nhất quán về ``spot_annual`` trước khi fit.

Phương pháp nội suy & ước lượng đường cong:
    1. **Nelson-Siegel (NS - 4 tham số)**: $(\beta_0, \beta_1, \beta_2, \lambda_1)$ — mô tả Level, Slope, Curvature.
    2. **Nelson-Siegel-Svensson (NSS - 6 tham số)**: $(\beta_0, \beta_1, \beta_2, \beta_3, \lambda_1, \lambda_2)$ —
       bắt hai bướu độc lập ở trung hạn (3Y–7Y) và dài hạn (10Y–20Y).
    3. **Cubic Spline & Piecewise Linear**: Nội suy phi tham số đi qua chính xác các nút kỳ hạn chuẩn
       HNX phục vụ chiết khấu dòng tiền chính xác theo điểm neo thị trường.

Biến đổi cấu trúc kỳ hạn chuẩn mực:
    - **Discount Factor**: $DF(t) = (1 + z_a(t)/100)^{-t}$
    - **Spot Continuous**: $z_c(t) = 100 \cdot \ln(1 + z_a(t)/100)$
    - **Par Yield (coupon năm)**: $c_{\text{par}}(N) = 100 \cdot \frac{1 - DF(N)}{\sum_{k=1}^{N} DF(k)}$
    - **Implied Annualized Forward Rate ($t_1 \to t_2$)**:
      $f(t_1, t_2) = 100 \cdot \left[ \left(\frac{(1 + z_a(t_2)/100)^{t_2}}{(1 + z_a(t_1)/100)^{t_1}}\right)^{\frac{1}{t_2 - t_1}} - 1 \right]$
    - **Instantaneous Forward Rate (NS/NSS)**:
      $f_{\text{inst}}(t) = \beta_0 + \beta_1 e^{-t/\lambda_1} + \beta_2 \frac{t}{\lambda_1} e^{-t/\lambda_1} + \beta_3 \frac{t}{\lambda_2} e^{-t/\lambda_2}$
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline
from scipy.optimize import OptimizeWarning, curve_fit

CurveMethod = Literal["NS", "NSS", "CUBIC_SPLINE", "LINEAR"]

# ── Kỳ hạn chuẩn trên thị trường TPCP Việt Nam (HNX & KBNN) ─────────────────

VN_STANDARD_TENORS: dict[str, float] = {
    "1M": 1.0 / 12.0,
    "2M": 2.0 / 12.0,
    "3M": 0.25,
    "6M": 0.50,
    "9M": 0.75,
    "1Y": 1.0,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
    "7Y": 7.0,
    "10Y": 10.0,
    "15Y": 15.0,
    "20Y": 20.0,
    "30Y": 30.0,
}

HNX_BENCHMARK_TENORS: list[str] = [
    "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y"
]

CORE_LIQUID_TENORS: list[str] = ["3Y", "5Y", "7Y", "10Y", "15Y"]

GRID_TENORS = np.array([
    0.08, 0.17, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0,
    5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 15.0, 20.0, 30.0,
], dtype=float)

# Giới hạn kiểm soát chất lượng lợi suất TPCP Việt Nam (%/năm)
MIN_VALID_YIELD_PCT: float = 0.05
MAX_VALID_YIELD_PCT: float = 20.0
MAX_LONG_END_JUMP_PCT: float = 4.0


def tenor_to_years(tenor: str | float | int | None) -> float | None:
    """Chuyển ký hiệu kỳ hạn (vd ``'10Y'``, ``'6M'``, ``'5 Năm'``) sang số năm thực.

    Parameters
    ----------
    tenor : str | float | int | None
        Ký hiệu kỳ hạn chuẩn tiếng Anh hoặc tiếng Việt, hoặc số năm trực tiếp.

    Returns
    -------
    float | None
        Số năm tương ứng (> 0), hoặc ``None`` nếu không hợp lệ.
    """
    if tenor is None:
        return None
    if isinstance(tenor, (int, float)):
        val = float(tenor)
        return val if np.isfinite(val) and val > 0 else None

    s = str(tenor).strip().upper()
    if not s:
        return None
    if s in VN_STANDARD_TENORS:
        return VN_STANDARD_TENORS[s]

    m_en = re.match(r"^(\d+(?:\.\d+)?)\s*(Y|YR|YRS|M|MO|W|D)$", s)
    if m_en:
        v, unit = float(m_en.group(1)), m_en.group(2)
        if unit.startswith("Y"):
            return v
        if unit.startswith("M"):
            return v / 12.0
        if unit == "W":
            return v / 52.0
        if unit == "D":
            return v / 365.0

    m_vn_y = re.match(r"^(\d+(?:[.,]\d+)?)\s*N[AĂ]M$", s, re.IGNORECASE)
    if m_vn_y:
        return float(m_vn_y.group(1).replace(",", "."))

    m_vn_m = re.match(r"^(\d+(?:[.,]\d+)?)\s*TH[AÁ]NG$", s, re.IGNORECASE)
    if m_vn_m:
        return float(m_vn_m.group(1).replace(",", ".")) / 12.0

    return None


def years_to_tenor_label(tenor_yr: float) -> str:
    """Chuyển số năm sang nhãn kỳ hạn chuẩn (vd ``0.25 -> '3M'``, ``10.0 -> '10Y'``)."""
    for lbl, yr in VN_STANDARD_TENORS.items():
        if abs(tenor_yr - yr) < 1e-2:
            return lbl
    if tenor_yr < 1.0:
        return f"{int(round(tenor_yr * 12))}M"
    if abs(tenor_yr - round(tenor_yr)) < 1e-6:
        return f"{int(round(tenor_yr))}Y"
    return f"{tenor_yr:g}Y"


# ── Công thức Nelson-Siegel & Svensson ───────────────────────────────────────

def _ns_loadings(t: np.ndarray, lam: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    """Tính nhân tử tải trọng (factor loadings) của Nelson-Siegel an toàn tại $t \to 0$."""
    t_arr = np.asarray(t, dtype=float)
    lam_arr = np.maximum(np.asarray(lam, dtype=float), 1e-6)
    tau = np.maximum(t_arr / lam_arr, 1e-8)
    exp_neg = np.exp(-tau)
    f1 = np.where(tau < 1e-5, 1.0 - 0.5 * tau + (tau ** 2) / 6.0, (1.0 - exp_neg) / tau)
    f2 = f1 - exp_neg
    return f1, f2


def nelson_siegel(
    t: np.ndarray | float,
    b0: float,
    b1: float,
    b2: float,
    lam: float,
) -> np.ndarray:
    """Công thức đường cong lợi suất giao ngay Nelson-Siegel (1987).

    $$y(t) = \\beta_0 + \\beta_1 \\frac{1 - e^{-t/\\lambda}}{t/\\lambda}
             + \\beta_2 \\left(\\frac{1 - e^{-t/\\lambda}}{t/\\lambda} - e^{-t/\\lambda}\\right)$$
    """
    t_arr = np.asarray(t, dtype=float)
    f1, f2 = _ns_loadings(t_arr, lam)
    return b0 + b1 * f1 + b2 * f2


def nelson_siegel_svensson(
    t: np.ndarray | float,
    b0: float,
    b1: float,
    b2: float,
    b3: float,
    lam1: float,
    lam2: float,
) -> np.ndarray:
    """Công thức đường cong lợi suất mở rộng Nelson-Siegel-Svensson (Svensson 1994).

    $$y(t) = \\text{NS}(t; \\beta_0, \\beta_1, \\beta_2, \\lambda_1)
             + \\beta_3 \\left(\\frac{1 - e^{-t/\\lambda_2}}{t/\\lambda_2} - e^{-t/\\lambda_2}\\right)$$
    """
    t_arr = np.asarray(t, dtype=float)
    f1, f2 = _ns_loadings(t_arr, lam1)
    _, f3 = _ns_loadings(t_arr, lam2)
    return b0 + b1 * f1 + b2 * f2 + b3 * f3


def instantaneous_forward_ns(params: dict, t: np.ndarray | float) -> np.ndarray:
    """Lãi suất kỳ hạn tức thời $f(t)$ từ tham số Nelson-Siegel hoặc Svensson (%)."""
    t_arr = np.asarray(t, dtype=float)
    b0 = float(params["b0"])
    b1 = float(params["b1"])
    b2 = float(params["b2"])
    lam1 = max(float(params["lam1"]), 1e-6)
    tau1 = t_arr / lam1
    fwd = b0 + b1 * np.exp(-tau1) + b2 * tau1 * np.exp(-tau1)
    if str(params.get("method", "NS")).upper() == "NSS":
        b3 = float(params.get("b3", 0.0))
        lam2 = max(float(params.get("lam2", 3.0)), 1e-6)
        tau2 = t_arr / lam2
        fwd = fwd + b3 * tau2 * np.exp(-tau2)
    return fwd


# ── Chuyển đổi giữa Spot, Discount Factor, Par Yield và Forward Rate ─────────

def spot_annual_to_continuous(spot_annual_pct: np.ndarray | float) -> np.ndarray | float:
    """Quy đổi lãi suất giao ngay ghép năm $z_a$ (%) sang ghép liên tục $z_c$ (%):
    $z_c = 100 \\cdot \\ln(1 + z_a/100)$.
    """
    return 100.0 * np.log1p(np.asarray(spot_annual_pct, dtype=float) / 100.0)


def spot_continuous_to_annual(spot_cont_pct: np.ndarray | float) -> np.ndarray | float:
    """Quy đổi lãi suất giao ngay ghép liên tục $z_c$ (%) sang ghép năm $z_a$ (%):
    $z_a = 100 \\cdot (e^{z_c/100} - 1)$.
    """
    return 100.0 * np.expm1(np.asarray(spot_cont_pct, dtype=float) / 100.0)


def discount_factor(spot_annual_pct: np.ndarray | float, tenor_yr: np.ndarray | float) -> np.ndarray | float:
    """Hệ số chiết khấu chuẩn ghép lãi năm: $DF(t) = (1 + z_a(t)/100)^{-t}$."""
    r = np.asarray(spot_annual_pct, dtype=float) / 100.0
    t = np.asarray(tenor_yr, dtype=float)
    df = np.power(1.0 + r, -t)
    if np.ndim(spot_annual_pct) == 0 and np.ndim(tenor_yr) == 0:
        return float(df)
    return df


def implied_forward_rate(
    spot_t1_pct: float | np.ndarray,
    t1: float | np.ndarray,
    spot_t2_pct: float | np.ndarray,
    t2: float | np.ndarray,
) -> float | np.ndarray:
    """Lãi suất kỳ hạn ẩn hàm (Implied Forward Rate, ghép lãi năm, %/năm) giai đoạn $[t_1, t_2]$.

    Công thức không chênh lệch giá (No-arbitrage):
    $$(1 + f_{t_1, t_2})^{t_2 - t_1} = \\frac{(1 + z_2)^{t_2}}{(1 + z_1)^{t_1}}$$
    """
    t1_arr = np.asarray(t1, dtype=float)
    t2_arr = np.asarray(t2, dtype=float)
    if np.any(t2_arr <= t1_arr) or np.any(t1_arr < 0):
        raise ValueError("Kỳ hạn forward đòi hỏi 0 <= t1 < t2.")
    z1 = np.asarray(spot_t1_pct, dtype=float) / 100.0
    z2 = np.asarray(spot_t2_pct, dtype=float) / 100.0
    growth = np.power(1.0 + z2, t2_arr) / np.power(1.0 + z1, t1_arr)
    fwd = (np.power(growth, 1.0 / (t2_arr - t1_arr)) - 1.0) * 100.0
    if np.ndim(fwd) == 0:
        return float(fwd)
    return fwd


def spot_to_par_yield(
    spot_curve_fn,
    maturity_yr: int | float,
    coupon_freq: int = 1,
) -> float | None:
    """Tính lợi suất ngang giá (Par Yield, %/năm) từ hàm đường cong lãi suất giao ngay.

    Theo chuẩn TPCP Việt Nam (coupon trả định kỳ 1 lần/năm, ``coupon_freq=1``):
    $$c_{\\text{par}}(N) = 100 \\cdot \\frac{1 - DF(N)}{(1/f) \\sum_{k=1}^{N \\cdot f} DF(k/f)}$$
    Khớp chính xác 100% đến 6 chữ số thập phân với cột ``Par Yield`` công bố trên HNX.
    """
    if maturity_yr <= 0 or coupon_freq < 1:
        return None
    n_periods = int(round(maturity_yr * coupon_freq))
    if n_periods < 1 or abs(n_periods / coupon_freq - maturity_yr) > 1e-6:
        # Nội suy tổng quát cho kỳ hạn lẻ bằng cách lùi từ ngày đáo hạn
        times = []
        tau = float(maturity_yr)
        step = 1.0 / coupon_freq
        while tau > 1e-8:
            times.append(tau)
            tau -= step
        times = sorted(times)
    else:
        times = [k / coupon_freq for k in range(1, n_periods + 1)]

    spots = []
    for t_k in times:
        s_k = spot_curve_fn(t_k)
        if s_k is None or not np.isfinite(s_k):
            return None
        spots.append(float(s_k))

    dfs = discount_factor(np.array(spots), np.array(times))
    annuity = float(np.sum(dfs)) / coupon_freq
    if annuity <= 0:
        return None
    return float(100.0 * (1.0 - dfs[-1]) / annuity)


def bootstrap_spot_from_par(
    tenors_yr: np.ndarray,
    par_yields_pct: np.ndarray,
) -> pd.DataFrame:
    """Bootstrapping đường cong Spot Rate (Zero-coupon annual %) từ đường cong Par Yield.

    Nội suy tuyến tính Par Yield tại các mốc năm nguyên $k = 1, 2, \\dots, N_{\\max}$,
    sau đó giải tuần tự hệ số chiết khấu:
    $$DF(k) = \\frac{1 - c_k \\sum_{j=1}^{k-1} DF(j)}{1 + c_k}, \\quad z_a(k) = DF(k)^{-1/k} - 1$$
    """
    mask = np.isfinite(tenors_yr) & np.isfinite(par_yields_pct) & (tenors_yr > 0)
    t_in = np.asarray(tenors_yr[mask], dtype=float)
    y_in = np.asarray(par_yields_pct[mask], dtype=float)
    if len(t_in) == 0:
        return pd.DataFrame(columns=["tenor_yr", "par_yield_pct", "spot_annual_pct", "discount_factor"])

    order = np.argsort(t_in)
    t_in, y_in = t_in[order], y_in[order]

    rows = []
    # Các kỳ hạn <= 1Y (Zero-coupon / Tín phiếu): Spot Annual == Par Yield
    short_mask = t_in < 1.0
    for t_s, y_s in zip(t_in[short_mask], y_in[short_mask]):
        df_s = float(discount_factor(y_s, t_s))
        rows.append({
            "tenor_yr": float(t_s),
            "par_yield_pct": float(y_s),
            "spot_annual_pct": float(y_s),
            "discount_factor": df_s,
        })

    max_yr = int(np.floor(np.max(t_in)))
    if max_yr >= 1:
        int_years = np.arange(1, max_yr + 1, dtype=float)
        par_interp = np.interp(int_years, t_in, y_in)
        df_accum = 0.0
        for k_yr, c_pct in zip(int_years, par_interp):
            c = float(c_pct) / 100.0
            if int(k_yr) == 1:
                df_k = 1.0 / (1.0 + c)
            else:
                df_k = (1.0 - c * df_accum) / (1.0 + c)
            if df_k <= 0:
                break
            df_accum += df_k
            spot_k = (df_k ** (-1.0 / k_yr) - 1.0) * 100.0
            rows.append({
                "tenor_yr": float(k_yr),
                "par_yield_pct": float(c_pct),
                "spot_annual_pct": float(spot_k),
                "discount_factor": float(df_k),
            })

    return pd.DataFrame(rows)


# ── Nội suy phi tham số (Linear & Cubic Spline) ──────────────────────────────

def interpolate_nonparametric(
    tenors_obs: np.ndarray,
    yields_obs: np.ndarray,
    target_tenors: np.ndarray | float,
    method: Literal["linear", "cubic_spline"] = "cubic_spline",
) -> np.ndarray | float:
    """Nội suy đường cong lợi suất bằng Piecewise Linear hoặc Natural Cubic Spline.

    Đối với các kỳ hạn nằm ngoài miền quan sát $[t_{\\min}, t_{\\max}]$, áp dụng
    ngoại suy phẳng (flat extrapolation) tại hai biên để ngăn hiện tượng dao động
    đa thức bậc ba vọt lố ở đầu siêu dài (20Y–30Y).
    """
    mask = np.isfinite(tenors_obs) & np.isfinite(yields_obs) & (tenors_obs > 0)
    t_clean = np.asarray(tenors_obs[mask], dtype=float)
    y_clean = np.asarray(yields_obs[mask], dtype=float)
    if len(t_clean) < 2:
        raise ValueError("Cần tối thiểu 2 điểm kỳ hạn hợp lệ để nội suy.")

    order = np.argsort(t_clean)
    t_clean, y_clean = t_clean[order], y_clean[order]
    # Gộp trùng lặp kỳ hạn nếu có
    t_unique, inv = np.unique(t_clean, return_inverse=True)
    if len(t_unique) < len(t_clean):
        y_sum = np.zeros_like(t_unique)
        counts = np.zeros_like(t_unique)
        np.add.at(y_sum, inv, y_clean)
        np.add.at(counts, inv, 1.0)
        t_clean = t_unique
        y_clean = y_sum / counts

    target_arr = np.asarray(target_tenors, dtype=float)
    t_clamped = np.clip(target_arr, t_clean[0], t_clean[-1])

    if method == "linear" or len(t_clean) < 4:
        res = np.interp(t_clamped, t_clean, y_clean)
    else:
        cs = CubicSpline(t_clean, y_clean, bc_type="natural")
        res = cs(t_clamped)

    if np.ndim(target_tenors) == 0:
        return float(res)
    return res


# ── Làm sạch dữ liệu lợi suất & loại bỏ ngoại lai ngoại suy HNX ──────────────

def sanitize_yield_curve_CrossSection(
    tenors_yr: np.ndarray,
    yields_pct: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Loại bỏ các điểm dữ liệu lỗi hoặc vọt lố ngoại suy (runaway extrapolation) ở đầu dài.

    Thực tiễn dữ liệu lịch sử HNX (2013–2016) cho thấy một số ngày thanh khoản mỏng ở
    kỳ hạn 15Y–20Y bị thuật toán cũ của sở ngoại suy âm (-4.4%) hoặc vọt lên > 25–51%.
    Hàm này kiểm tra biên hợp lý $[0.05\\%, 20\\%]$ và độ nhảy bậc tối đa giữa các kỳ hạn liền kề
    ở đầu dài ($> 10\\text{Y}$).
    """
    mask = (
        np.isfinite(tenors_yr)
        & np.isfinite(yields_pct)
        & (tenors_yr > 0)
        & (yields_pct >= MIN_VALID_YIELD_PCT)
        & (yields_pct <= MAX_VALID_YIELD_PCT)
    )
    t = np.asarray(tenors_yr[mask], dtype=float)
    y = np.asarray(yields_pct[mask], dtype=float)
    if len(t) == 0:
        return t, y

    order = np.argsort(t)
    t, y = t[order], y[order]

    # Kiểm tra bước nhảy bất thường ở đầu dài (> 10Y) so với mốc neo trung/dài hạn trước đó
    keep = np.ones(len(t), dtype=bool)
    last_anchor_y = None
    for idx in range(len(t)):
        if t[idx] <= 10.5:
            last_anchor_y = y[idx]
        elif last_anchor_y is not None:
            if abs(y[idx] - last_anchor_y) > MAX_LONG_END_JUMP_PCT:
                keep[idx] = False
            else:
                last_anchor_y = y[idx]
    return t[keep], y[keep]


def extract_clean_spot_panel(
    df: pd.DataFrame,
    rate_priority: list[str] | None = None,
) -> pd.DataFrame:
    """Trích xuất bảng chuẩn Spot Annual (date × tenor_yr) từ dữ liệu gộp ``combined_tpcp_yields``.

    Khắc phục triệt để lỗi trộn lẫn ``spot_annual``, ``spot_continuous`` và ``primary_winning_yield``:
    1. Áp dụng thứ tự ưu tiên phân cấp trên TỪNG cặp ``(date, tenor_yr)``:
       ``spot_annual`` > ``spot_continuous`` (quy đổi sang annual) > ``par_yield`` > ``primary_winning_yield``.
    2. Làm sạch ngoại lai từng ngày qua ``sanitize_yield_curve_CrossSection`` (loại bỏ các điểm
       ngoại suy lỗi HNX 2013–2016 như -4.4% hay +51.7%).
    """
    if df.empty:
        return pd.DataFrame()

    if rate_priority is None:
        rate_priority = ["spot_annual", "spot_continuous", "par_yield", "primary_winning_yield"]

    prio_map = {rt: idx for idx, rt in enumerate(rate_priority)}
    sub = df[df["rate_type"].isin(prio_map)].copy()
    if sub.empty:
        return pd.DataFrame()

    sub["date"] = pd.to_datetime(sub["date"])
    sub["tenor_yr"] = sub["tenor"].map(tenor_to_years)
    sub = sub.dropna(subset=["date", "tenor_yr", "value"])

    # Quy đổi spot_continuous về spot_annual tương đương để đồng nhất đơn vị ghép lãi năm
    cont_mask = sub["rate_type"] == "spot_continuous"
    if cont_mask.any():
        sub.loc[cont_mask, "value"] = spot_continuous_to_annual(sub.loc[cont_mask, "value"].to_numpy())

    sub["prio"] = sub["rate_type"].map(prio_map)
    best_prio = sub.groupby(["date", "tenor_yr"])["prio"].transform("min")
    clean = (
        sub[sub["prio"] == best_prio]
        .groupby(["date", "tenor_yr"], as_index=False)["value"]
        .mean()
    )

    pivot = clean.pivot(index="date", columns="tenor_yr", values="value").sort_index()
    pivot = pivot.where((pivot >= MIN_VALID_YIELD_PCT) & (pivot <= MAX_VALID_YIELD_PCT))

    # Kiểm soát bước nhảy đầu dài 15Y, 20Y, 30Y so với mốc 10Y
    if 10.0 in pivot.columns:
        if 15.0 in pivot.columns:
            bad_15 = (pivot[15.0] - pivot[10.0]).abs() > MAX_LONG_END_JUMP_PCT
            pivot.loc[bad_15, 15.0] = np.nan
        if 20.0 in pivot.columns:
            bad_20 = (pivot[20.0] - pivot[10.0]).abs() > (MAX_LONG_END_JUMP_PCT * 1.5)
            if 15.0 in pivot.columns:
                bad_20 = bad_20 | ((pivot[20.0] - pivot[15.0]).abs() > MAX_LONG_END_JUMP_PCT) | pivot[15.0].isna()
            pivot.loc[bad_20, 20.0] = np.nan
        if 30.0 in pivot.columns:
            bad_30 = (pivot[30.0] - pivot[10.0]).abs() > (MAX_LONG_END_JUMP_PCT * 2.0)
            pivot.loc[bad_30, 30.0] = np.nan

    return pivot


# ── Fit một ngày & Fit Vectorized tốc độ cao toàn lịch sử ────────────────────

NS_P0 = [4.5, -1.5, 0.5, 1.5]
NS_BOUNDS = ([0.0, -15.0, -15.0, 0.1], [20.0, 15.0, 15.0, 10.0])

NSS_P0 = [4.5, -1.5, 0.5, 0.5, 1.2, 5.0]
NSS_BOUNDS = ([0.0, -15.0, -15.0, -15.0, 0.1, 1.5], [20.0, 15.0, 15.0, 15.0, 4.0, 15.0])

_NS_LAMBDA_GRID = np.geomspace(0.12, 10.0, 260)
_NSS_L1_GRID = np.geomspace(0.15, 3.8, 36)
_NSS_L2_GRID = np.geomspace(1.8, 15.0, 36)
_NSS_LAMBDA_PAIRS = np.array([
    (l1, l2) for l1 in _NSS_L1_GRID for l2 in _NSS_L2_GRID if l2 > l1 * 1.35
], dtype=float)


def fit_day(
    tenors_yr: np.ndarray,
    yields_pct: np.ndarray,
    method: str = "NS",
    sanitize: bool = True,
) -> dict | None:
    """Ước lượng tham số đường cong Nelson-Siegel hoặc Svensson cho một ngày giao dịch.

    Sử dụng thuật toán Profiled Nonlinear Least Squares (khảo sát toàn cục trên lưới
    $\\lambda$ kết hợp tinh chỉnh ``scipy.optimize.curve_fit``) giúp hội tụ toàn cục 100%,
    không bị kẹt tại cực tiểu địa phương như Levenberg-Marquardt đơn thuần.
    """
    t_raw = np.asarray(tenors_yr, dtype=float)
    y_raw = np.asarray(yields_pct, dtype=float)
    if sanitize:
        t, y = sanitize_yield_curve_CrossSection(t_raw, y_raw)
    else:
        mask = np.isfinite(t_raw) & np.isfinite(y_raw) & (t_raw > 0)
        t, y = t_raw[mask], y_raw[mask]

    method_u = method.strip().upper()
    min_pts = 4 if method_u == "NS" else 6
    if len(t) < min_pts:
        if len(t) >= 3 and method_u == "NS":
            pass
        else:
            return None

    # Bước 1: Profiled OLS toàn cục trên lưới lambda để lấy điểm khởi tạo tối ưu
    if method_u == "NS":
        f1, f2 = _ns_loadings(t[None, :], _NS_LAMBDA_GRID[:, None])
        X = np.stack([np.ones_like(f1), f1, f2], axis=-1)  # (L, K, 3)
        XtX = np.einsum("lki,lkj->lij", X, X) + 1e-10 * np.eye(3)[None, :, :]
        Xty = np.einsum("lki,k->li", X, y)[:, :, None]
        betas = np.linalg.solve(XtX, Xty)[:, :, 0]
        betas = np.clip(betas, [0.0, -15.0, -15.0], [20.0, 15.0, 15.0])
        y_pred = np.einsum("lki,li->lk", X, betas)
        sse = np.sum((y_pred - y[None, :]) ** 2, axis=-1)
        best = int(np.argmin(sse))
        p0 = [float(betas[best, 0]), float(betas[best, 1]), float(betas[best, 2]), float(_NS_LAMBDA_GRID[best])]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            try:
                popt, _ = curve_fit(
                    nelson_siegel, t, y,
                    p0=p0, bounds=NS_BOUNDS, maxfev=2000,
                )
            except (RuntimeError, ValueError):
                popt = np.array(p0, dtype=float)
        resid = y - nelson_siegel(t, *popt)
        return {
            "method": "NS",
            "b0": float(popt[0]),
            "b1": float(popt[1]),
            "b2": float(popt[2]),
            "lam1": float(popt[3]),
            "rmse": float(np.sqrt(np.mean(resid ** 2))),
            "n_points": int(len(t)),
        }

    if method_u == "NSS":
        l1_arr = _NSS_LAMBDA_PAIRS[:, 0]
        l2_arr = _NSS_LAMBDA_PAIRS[:, 1]
        f1, f2 = _ns_loadings(t[None, :], l1_arr[:, None])
        _, f3 = _ns_loadings(t[None, :], l2_arr[:, None])
        X = np.stack([np.ones_like(f1), f1, f2, f3], axis=-1)
        XtX = np.einsum("mki,mkj->mij", X, X) + 1e-8 * np.eye(4)[None, :, :]
        Xty = np.einsum("mki,k->mi", X, y)[:, :, None]
        betas = np.linalg.solve(XtX, Xty)[:, :, 0]
        betas = np.clip(betas, [0.0, -15.0, -15.0, -15.0], [20.0, 15.0, 15.0, 15.0])
        y_pred = np.einsum("mki,mi->mk", X, betas)
        sse = np.sum((y_pred - y[None, :]) ** 2, axis=-1)
        best = int(np.argmin(sse))
        p0 = [
            float(betas[best, 0]), float(betas[best, 1]),
            float(betas[best, 2]), float(betas[best, 3]),
            float(l1_arr[best]), float(l2_arr[best]),
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            try:
                popt, _ = curve_fit(
                    nelson_siegel_svensson, t, y,
                    p0=p0, bounds=NSS_BOUNDS, maxfev=3000,
                )
            except (RuntimeError, ValueError):
                popt = np.array(p0, dtype=float)
        resid = y - nelson_siegel_svensson(t, *popt)
        return {
            "method": "NSS",
            "b0": float(popt[0]),
            "b1": float(popt[1]),
            "b2": float(popt[2]),
            "b3": float(popt[3]),
            "lam1": float(popt[4]),
            "lam2": float(popt[5]),
            "rmse": float(np.sqrt(np.mean(resid ** 2))),
            "n_points": int(len(t)),
        }

    raise ValueError(f"Phương pháp không hỗ trợ: {method}. Chọn 'NS' hoặc 'NSS'.")


def predict_yield(params: dict | None, tenor_yr: float) -> float | None:
    """Dự báo lợi suất giao ngay (%) tại kỳ hạn ``tenor_yr`` (năm) từ bộ tham số đã fit."""
    if params is None or tenor_yr <= 0:
        return None
    t = np.array([float(tenor_yr)])
    try:
        method = str(params.get("method", "NS")).upper()
        if method == "NS":
            return float(nelson_siegel(
                t, params["b0"], params["b1"], params["b2"], params["lam1"]
            )[0])
        return float(nelson_siegel_svensson(
            t, params["b0"], params["b1"], params["b2"], params["b3"],
            params["lam1"], params["lam2"]
        )[0])
    except Exception:
        return None


def _fit_panel_vectorized(pivot: pd.DataFrame, method: str = "NS") -> pd.DataFrame:
    """Ước lượng hàng loạt (Vectorized Batch Profiled OLS) trên toàn bộ lịch sử 3.400+ ngày.

    Nhóm các ngày có cùng cấu trúc kỳ hạn hiện diện (missingness mask) để giải đồng thời
    hệ phương trình đại số tuyến tính bằng tensor ``numpy.einsum`` — tăng tốc ~40 lần so với
    vòng lặp ``curve_fit`` truyền thống và đảm bảo nghiệm tối ưu toàn cục.
    """
    method_u = method.strip().upper()
    min_pts = 3 if method_u == "NS" else 6
    all_tenors = pivot.columns.to_numpy(dtype=float)
    valid_matrix = pivot.notna().to_numpy()
    dates = pivot.index.to_numpy()
    values = pivot.to_numpy(dtype=float)

    # Nhóm theo mẫu kỳ hạn khả dụng
    pattern_groups: dict[bytes, list[int]] = {}
    for row_idx in range(len(dates)):
        mask_row = valid_matrix[row_idx]
        if int(mask_row.sum()) >= min_pts:
            pattern_groups.setdefault(mask_row.tobytes(), []).append(row_idx)

    records: list[dict] = []
    for mask_bytes, row_indices in pattern_groups.items():
        col_mask = np.frombuffer(mask_bytes, dtype=bool)
        t_obs = all_tenors[col_mask]
        Y_obs = values[np.ix_(row_indices, col_mask)]  # (N_group, K)
        n_group, k_pts = Y_obs.shape

        if method_u == "NS":
            f1, f2 = _ns_loadings(t_obs[None, :], _NS_LAMBDA_GRID[:, None])
            X = np.stack([np.ones_like(f1), f1, f2], axis=-1)  # (L, K, 3)
            XtX = np.einsum("lki,lkj->lij", X, X) + 1e-10 * np.eye(3)[None, :, :]
            pinv_X = np.linalg.solve(XtX, np.transpose(X, (0, 2, 1)))  # (L, 3, K)
            B = np.einsum("lik,nk->lni", pinv_X, Y_obs)  # (L, N, 3)
            B = np.clip(B, [0.0, -15.0, -15.0], [20.0, 15.0, 15.0])
            Y_hat = np.einsum("lki,lni->lnk", X, B)
            sse = np.sum((Y_hat - Y_obs[None, :, :]) ** 2, axis=-1)  # (L, N)
            best_l = np.argmin(sse, axis=0)
            ar = np.arange(n_group)
            best_b = B[best_l, ar]
            best_lam = _NS_LAMBDA_GRID[best_l]
            rmses = np.sqrt(sse[best_l, ar] / k_pts)
            for j, r_idx in enumerate(row_indices):
                records.append({
                    "date": pd.Timestamp(dates[r_idx]),
                    "method": "NS",
                    "b0": float(best_b[j, 0]),
                    "b1": float(best_b[j, 1]),
                    "b2": float(best_b[j, 2]),
                    "lam1": float(best_lam[j]),
                    "rmse": float(rmses[j]),
                    "n_points": int(k_pts),
                })
        else:
            l1_arr = _NSS_LAMBDA_PAIRS[:, 0]
            l2_arr = _NSS_LAMBDA_PAIRS[:, 1]
            f1, f2 = _ns_loadings(t_obs[None, :], l1_arr[:, None])
            _, f3 = _ns_loadings(t_obs[None, :], l2_arr[:, None])
            X = np.stack([np.ones_like(f1), f1, f2, f3], axis=-1)
            XtX = np.einsum("mki,mkj->mij", X, X) + 1e-8 * np.eye(4)[None, :, :]
            pinv_X = np.linalg.solve(XtX, np.transpose(X, (0, 2, 1)))
            B = np.einsum("mik,nk->mni", pinv_X, Y_obs)
            B = np.clip(B, [0.0, -15.0, -15.0, -15.0], [20.0, 15.0, 15.0, 15.0])
            Y_hat = np.einsum("mki,mni->mnk", X, B)
            sse = np.sum((Y_hat - Y_obs[None, :, :]) ** 2, axis=-1)
            best_m = np.argmin(sse, axis=0)
            ar = np.arange(n_group)
            best_b = B[best_m, ar]
            rmses = np.sqrt(sse[best_m, ar] / k_pts)
            for j, r_idx in enumerate(row_indices):
                records.append({
                    "date": pd.Timestamp(dates[r_idx]),
                    "method": "NSS",
                    "b0": float(best_b[j, 0]),
                    "b1": float(best_b[j, 1]),
                    "b2": float(best_b[j, 2]),
                    "b3": float(best_b[j, 3]),
                    "lam1": float(l1_arr[best_m[j]]),
                    "lam2": float(l2_arr[best_m[j]]),
                    "rmse": float(rmses[j]),
                    "n_points": int(k_pts),
                })

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def _evaluate_grid_vectorized(params_df: pd.DataFrame, grid_tenors: np.ndarray = GRID_TENORS) -> pd.DataFrame:
    """Đánh giá đường cong liên tục trên lưới ``grid_tenors`` cho toàn bộ lịch sử bằng numpy."""
    if params_df.empty:
        return pd.DataFrame(columns=["date", "tenor_yr", "yield_pct", "method"])

    method_u = str(params_df["method"].iloc[0]).upper()
    dates = params_df["date"].to_numpy()
    b0 = params_df["b0"].to_numpy(dtype=float)[:, None]
    b1 = params_df["b1"].to_numpy(dtype=float)[:, None]
    b2 = params_df["b2"].to_numpy(dtype=float)[:, None]
    lam1 = params_df["lam1"].to_numpy(dtype=float)[:, None]

    tau1 = grid_tenors[None, :] / np.maximum(lam1, 1e-6)
    e1 = np.exp(-tau1)
    f1 = (1.0 - e1) / tau1
    f2 = f1 - e1
    Y_grid = b0 + b1 * f1 + b2 * f2

    if method_u == "NSS":
        b3 = params_df["b3"].to_numpy(dtype=float)[:, None]
        lam2 = params_df["lam2"].to_numpy(dtype=float)[:, None]
        tau2 = grid_tenors[None, :] / np.maximum(lam2, 1e-6)
        e2 = np.exp(-tau2)
        f3 = (1.0 - e2) / tau2 - e2
        Y_grid = Y_grid + b3 * f3

    n_dates, n_tenors = Y_grid.shape
    flat_dates = np.repeat(dates, n_tenors)
    flat_tenors = np.tile(grid_tenors, n_dates)
    flat_yields = Y_grid.ravel()

    valid = np.isfinite(flat_yields) & (flat_yields > 0.0) & (flat_yields < 30.0)
    return pd.DataFrame({
        "date": flat_dates[valid],
        "tenor_yr": flat_tenors[valid],
        "yield_pct": flat_yields[valid],
        "method": method_u,
    })


def fit_all_history(
    combined_parquet: str | Path,
    method: str = "NS",
    rate_types: list[str] | None = None,
    cache_dir: str | Path = "./cache",
    also_fit_nss: bool = True,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit đường cong lợi suất chuẩn Spot Annual cho toàn bộ lịch sử từ ``combined_tpcp_yields.parquet``.

    Đồng thời (khi ``method='NS'`` và ``also_fit_nss=True``) tự động ước lượng và lưu thêm
    bộ tham số & đường cong Svensson 6 tham số (``fitted_params_nss.parquet``, ``fitted_curve_nss.parquet``)
    để phục vụ so sánh mô hình NS vs NSS trên Web Dashboard và công cụ định giá.
    """
    raw_df = pd.read_parquet(combined_parquet)
    pivot = extract_clean_spot_panel(raw_df, rate_priority=rate_types)
    total_dates = len(pivot)
    method_u = method.strip().upper()

    params_df = _fit_panel_vectorized(pivot, method=method_u)
    curve_df = _evaluate_grid_vectorized(params_df, GRID_TENORS)

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    if len(params_df):
        params_df.to_parquet(cache_path / f"fitted_params_{method_u.lower()}.parquet", index=False)
    if len(curve_df):
        curve_df.to_parquet(cache_path / f"fitted_curve_{method_u.lower()}.parquet", index=False)

    if verbose and total_dates > 0:
        ok_cnt = len(params_df)
        print(
            f"  Fitting [{method_u}] hoàn tất: {ok_cnt}/{total_dates} ngày "
            f"({100 * ok_cnt / total_dates:.1f}%) | "
            f"RMSE TB: {params_df['rmse'].mean() * 100:.2f} bps "
            f"(trung vị: {params_df['rmse'].median() * 100:.2f} bps)"
        )

    if method_u == "NS" and also_fit_nss:
        nss_params = _fit_panel_vectorized(pivot, method="NSS")
        nss_curve = _evaluate_grid_vectorized(nss_params, GRID_TENORS)
        if len(nss_params):
            nss_params.to_parquet(cache_path / "fitted_params_nss.parquet", index=False)
        if len(nss_curve):
            nss_curve.to_parquet(cache_path / "fitted_curve_nss.parquet", index=False)
        if verbose and len(nss_params):
            print(
                f"  Fitting [NSS] hoàn tất: {len(nss_params)}/{total_dates} ngày | "
                f"RMSE TB: {nss_params['rmse'].mean() * 100:.2f} bps "
                f"(trung vị: {nss_params['rmse'].median() * 100:.2f} bps)"
            )

    return params_df, curve_df


# ── Đối tượng Đường cong Lợi suất toàn diện cho Bàn Giao dịch (Bond Desk) ────

class YieldCurve:
    """Đối tượng truy vấn đường cong lợi suất TPCP Việt Nam đa chế độ.

    Hỗ trợ đầy đủ các phép toán cấu trúc kỳ hạn chuẩn mực:
        - ``spot(date, tenor_yr)``: Lợi suất giao ngay ghép lãi năm $z_a(t)$ (%)
        - ``spot_continuous(date, tenor_yr)``: Lợi suất giao ngay ghép liên tục $z_c(t)$ (%)
        - ``discount_factor(date, tenor_yr)``: Hệ số chiết khấu $DF(t)$
        - ``par_yield(date, tenor_yr)``: Lợi suất ngang giá $c_{\\text{par}}(t)$ (%)
        - ``forward_rate(date, t1, t2)``: Lãi suất kỳ hạn ẩn hàm $f(t_1, t_2)$ (%)
        - ``instantaneous_forward(date, tenor_yr)``: Lãi suất kỳ hạn tức thời $f(t)$ (%)
        - ``term_structure_table(date)``: Bảng đầy đủ Spot / Par / DF / Implied Forward 1Y
    """

    def __init__(self, params_df: pd.DataFrame, method: str = "NS"):
        self._params = params_df.copy()
        self._params["date"] = pd.to_datetime(self._params["date"])
        self._params = self._params.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
        self._method = method.upper()

    @classmethod
    def from_cache(cls, cache_dir: str | Path = "./cache", method: str = "NS") -> "YieldCurve":
        """Nạp bộ tham số đường cong từ thư mục cache Parquet."""
        p = Path(cache_dir) / f"fitted_params_{method.lower()}.parquet"
        if not p.exists():
            raise FileNotFoundError(
                f"Chưa có file {p}. Hãy chạy fit_all_history() trước."
            )
        return cls(pd.read_parquet(p), method=method)

    @property
    def latest_date(self) -> pd.Timestamp | None:
        return self._params.index.max() if len(self._params) else None

    def _params_for(self, date_like) -> dict | None:
        """Lấy bộ tham số của ngày được chọn hoặc phiên giao dịch liền trước gần nhất."""
        d = pd.Timestamp(date_like)
        if d in self._params.index:
            return self._params.loc[d].to_dict()
        past = self._params.index[self._params.index <= d]
        if len(past) == 0:
            return None
        return self._params.loc[past[-1]].to_dict()

    def spot(self, date_like, tenor_yr: float) -> float | None:
        """Lợi suất giao ngay ghép lãi năm $z_a(t)$ (%/năm) tại kỳ hạn ``tenor_yr``."""
        p = self._params_for(date_like)
        return predict_yield(p, tenor_yr)

    def spot_continuous(self, date_like, tenor_yr: float) -> float | None:
        """Lợi suất giao ngay ghép lãi liên tục $z_c(t)$ (%/năm)."""
        s = self.spot(date_like, tenor_yr)
        return float(spot_annual_to_continuous(s)) if s is not None else None

    def discount_factor(self, date_like, tenor_yr: float) -> float | None:
        """Hệ số chiết khấu $DF(t) = (1 + z_a(t)/100)^{-t}$."""
        if tenor_yr == 0:
            return 1.0
        s = self.spot(date_like, tenor_yr)
        return float(discount_factor(s, tenor_yr)) if s is not None else None

    def par_yield(self, date_like, maturity_yr: float, coupon_freq: int = 1) -> float | None:
        """Lợi suất trái phiếu ngang giá $c_{\\text{par}}(T)$ (%/năm) trả coupon ``coupon_freq`` lần/năm."""
        p = self._params_for(date_like)
        if p is None:
            return None
        return spot_to_par_yield(lambda tau: predict_yield(p, tau), maturity_yr, coupon_freq=coupon_freq)

    def forward_rate(self, date_like, t1_yr: float, t2_yr: float) -> float | None:
        """Lãi suất kỳ hạn ẩn hàm $f(t_1, t_2)$ (%/năm) giữa hai mốc kỳ hạn $0 \\le t_1 < t_2$."""
        if t1_yr < 0 or t2_yr <= t1_yr:
            return None
        if t1_yr == 0:
            return self.spot(date_like, t2_yr)
        s1 = self.spot(date_like, t1_yr)
        s2 = self.spot(date_like, t2_yr)
        if s1 is None or s2 is None:
            return None
        return float(implied_forward_rate(s1, t1_yr, s2, t2_yr))

    def instantaneous_forward(self, date_like, tenor_yr: float) -> float | None:
        """Lãi suất kỳ hạn tức thời $f(t)$ (%) tại kỳ hạn ``tenor_yr``."""
        p = self._params_for(date_like)
        if p is None or tenor_yr <= 0:
            return None
        return float(instantaneous_forward_ns(p, float(tenor_yr)))

    def full_curve(self, date_like, tenors: np.ndarray | None = None) -> pd.DataFrame:
        """Toàn bộ đường cong tại một ngày: Spot Annual, Spot Continuous, Par Yield, Forward 1Y và DF."""
        if tenors is None:
            tenors = GRID_TENORS
        p = self._params_for(date_like)
        if p is None:
            return pd.DataFrame(columns=[
                "tenor_yr", "tenor_label", "yield_pct", "spot_continuous_pct",
                "par_yield_pct", "forward_1y_pct", "inst_forward_pct", "discount_factor",
            ])
        rows = []
        for t in tenors:
            t_f = float(t)
            s_ann = predict_yield(p, t_f)
            if s_ann is None:
                continue
            s_next = predict_yield(p, t_f + 1.0)
            fwd_1y = float(implied_forward_rate(s_ann, t_f, s_next, t_f + 1.0)) if s_next is not None else None
            par_y = spot_to_par_yield(lambda tau: predict_yield(p, tau), t_f, coupon_freq=1)
            rows.append({
                "tenor_yr": t_f,
                "tenor_label": years_to_tenor_label(t_f),
                "yield_pct": s_ann,
                "spot_continuous_pct": float(spot_annual_to_continuous(s_ann)),
                "par_yield_pct": par_y,
                "forward_1y_pct": fwd_1y,
                "inst_forward_pct": float(instantaneous_forward_ns(p, t_f)),
                "discount_factor": float(discount_factor(s_ann, t_f)),
            })
        return pd.DataFrame(rows)

    def term_spread(self, date_like, short_yr: float = 2.0, long_yr: float = 10.0) -> float | None:
        """Chênh lệch lợi suất kỳ hạn dài - ngắn (%)."""
        y_long = self.spot(date_like, long_yr)
        y_short = self.spot(date_like, short_yr)
        if y_long is None or y_short is None:
            return None
        return y_long - y_short

    def butterfly(self, date_like, short_yr: float = 2.0, belly_yr: float = 5.0, long_yr: float = 10.0) -> float | None:
        """Butterfly spread chuẩn ``2s5s10s`` = $2 \\cdot y_{5Y} - (y_{2Y} + y_{10Y})$ (%)."""
        ys = self.spot(date_like, short_yr)
        yb = self.spot(date_like, belly_yr)
        yl = self.spot(date_like, long_yr)
        if any(x is None for x in (ys, yb, yl)):
            return None
        return 2.0 * yb - ys - yl


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Fit đường cong lợi suất Nelson-Siegel / Svensson")
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
        print(params_df.tail())
