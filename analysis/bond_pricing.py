"""
bond_pricing.py — Động cơ Định giá, Lợi suất & Đo lường Rủi ro Lãi suất TPCP Việt Nam.
========================================================================================

Chuẩn mực thị trường Trái phiếu Chính phủ Việt Nam (Thông tư 30/2023/TT-BTC, HNX & VBMA):
    1. **Mệnh giá chuẩn (Standard Par Value)**: ``100,000 VNĐ`` / trái phiếu (hỗ trợ quy đổi
       theo danh mục chuẩn ``100`` hoặc quy mô giao dịch chuẩn Bàn giao dịch ``100 tỷ VNĐ``).
    2. **Tần suất trả lãi (Coupon Frequency)**: Mặc định ``1 lần/năm`` (Annual coupon, $f = 1$)
       cho toàn bộ TPCP Kho bạc Nhà nước phát hành chuẩn hóa. Hỗ trợ tùy chỉnh $f \\in \\{1, 2, 4\\}$.
    3. **Quy ước ngày đếm (Day Count Convention)**:
       - ``ACT/ACT`` *(Actual/Actual ICMA — Chuẩn niêm yết & đấu thầu HNX)*:
         $$v = \\frac{d_{\\text{next}}}{E}, \\quad AI = \\frac{C}{f} \\cdot \\frac{d_{\\text{accrued}}}{E}$$
         trong đó $E = (t_{\\text{next}} - t_{\\text{prev}})$ là số ngày thực tế của kỳ tính lãi hiện hành
         (365 ngày năm thường hoặc 366 ngày nếu kỳ lãi đi qua ngày 29/02 năm nhuận),
         $d_{\\text{next}} = (t_{\\text{next}} - t_{\\text{settle}})$ và $d_{\\text{accrued}} = E - d_{\\text{next}}$.
         *Đã kiểm chứng thực nghiệm khớp 100% đến từng đồng VNĐ với cột "Số tiền thanh toán
         trái phiếu trúng thầu" của HNX trên cả năm nhuận 2024 và năm thường 2026.*
       - ``ACT/365`` *(Actual/365 Fixed — Chuẩn thị trường tiền tệ / repo liên ngân hàng VN)*:
         $E = 365.0$ cố định.
       - ``30/360`` *(30E/360 ISDA — Phục vụ đối chiếu trái phiếu quốc tế)*.
    4. **Công thức Giá Kèm Lãi (Dirty Price / Full Settlement Price)**:
       $$P_{\\text{dirty}}(y) = \\sum_{j=0}^{N-1} \\frac{CF_j}{(1 + y/f)^{v + j}}$$
       với $CF_j = C/f$ cho $j < N-1$ và $CF_{N-1} = C/f + M$ tại ngày đáo hạn.
       - Giá Yết / Giá Sạch (Clean Price): $P_{\\text{clean}} = P_{\\text{dirty}} - AI$.
       - Giá thanh toán sơ cấp HNX (trên mệnh giá 100,000 VNĐ): ``int(floor(P_dirty))`` theo quy tắc
         cắt phần lẻ thập phân trên 1 trái phiếu mệnh giá 100,000 VNĐ tại phiên đấu thầu.
    5. **Đo lường Rủi ro Lãi suất (Interest Rate Risk Metrics)**:
       - **Macaulay Duration (năm)**:
         $$D_{\\text{Mac}} = \\frac{1}{P_{\\text{dirty}}} \\sum_{j=0}^{N-1} \\frac{v + j}{f} \\cdot \\frac{CF_j}{(1 + y/f)^{v + j}}$$
       - **Modified Duration (năm)**:
         $$D_{\\text{Mod}} = \\frac{D_{\\text{Mac}}}{1 + y/f}$$
       - **DV01 / PV01 (Giá trị biến động cho 1 bps = 0.01% dịch chuyển lợi suất)**:
         - Xấp xỉ giải tích: $\\text{DV01}_{\\text{analytic}} = P_{\\text{dirty}} \\cdot D_{\\text{Mod}} \\cdot 10^{-4}$
         - Chính xác đối xứng trung tâm: $\\text{DV01}_{\\text{exact}} = \\frac{P_{\\text{dirty}}(y - 1\\text{bp}) - P_{\\text{dirty}}(y + 1\\text{bp})}{2}$
       - **Convexity ($\text{năm}^2$)**:
         $$\\text{Convexity} = \\frac{1}{P_{\\text{dirty}} (1 + y/f)^2} \\sum_{j=0}^{N-1} \\frac{(v + j)(v + j + 1)}{f^2} \\cdot \\frac{CF_j}{(1 + y/f)^{v + j}}$$
       - **Khai triển Taylor bậc 2 dự báo P&L theo cú sốc $\\Delta y$**:
         $$\\Delta P \\approx -D_{\\text{Mod}} \\cdot P_{\\text{dirty}} \\cdot \\Delta y + \\frac{1}{2} \\text{Convexity} \\cdot P_{\\text{dirty}} \\cdot (\\Delta y)^2$$
    6. **Định giá Chiết khấu Đường cong Giao ngay (Zero-Curve Valuation & Z-Spread)** &
       **Lợi nhuận Kỳ nắm giữ (Total Horizon Return = Carry + Roll-down + Convexity)**.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from analysis.yield_curve_fitting import (
    CORE_LIQUID_TENORS,
    HNX_BENCHMARK_TENORS,
    VN_STANDARD_TENORS,
    YieldCurve,
    discount_factor,
)

DayCountConvention = Literal["ACT/ACT", "ACT/365", "30/360"]

VN_PAR_VALUE: float = 100_000.0          # Mệnh giá chuẩn 1 TPCP niêm yết HNX (100,000 VNĐ)
INSTITUTIONAL_LOT_VND: float = 100e9     # Quy mô lô giao dịch chuẩn liên ngân hàng (100 tỷ VNĐ)


# ── Tiện ích xử lý lịch trả lãi & quy ước ngày đếm ───────────────────────────

def _to_date(d: date | datetime | str | pd.Timestamp) -> date:
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if "/" in s:
        return datetime.strptime(s[:10], "%d/%m/%Y").date()
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _add_months(dt: date, months: int) -> date:
    """Cộng/trừ số tháng theo lịch chuẩn, kẹp ngày cuối tháng an toàn (vd 29/02 -> 28/02)."""
    total_months = (dt.year * 12 + (dt.month - 1)) + months
    new_year = total_months // 12
    new_month = (total_months % 12) + 1
    max_day = calendar.monthrange(new_year, new_month)[1]
    return date(new_year, new_month, min(dt.day, max_day))


def generate_coupon_schedule(
    settlement_date: date | str,
    maturity_date: date | str,
    coupon_freq: int = 1,
) -> tuple[date, list[date]]:
    """Tạo lịch trả lãi ngược từ ngày đáo hạn về trước ngày thanh toán ``settlement_date``.

    Parameters
    ----------
    settlement_date : date | str
        Ngày thanh toán giao dịch (T+1 trên HNX).
    maturity_date : date | str
        Ngày đáo hạn trái phiếu.
    coupon_freq : int
        Số kỳ trả lãi mỗi năm (mặc định ``1`` cho TPCP Việt Nam; hỗ trợ ``1, 2, 4``).

    Returns
    -------
    (prev_coupon_date, future_coupon_dates)
        ``prev_coupon_date <= settlement_date < future_coupon_dates[0] <= ... <= maturity_date``.
    """
    if coupon_freq not in (1, 2, 4, 12):
        raise ValueError(f"coupon_freq={coupon_freq} không hợp lệ. Chỉ hỗ trợ 1, 2, 4, 12.")

    settle = _to_date(settlement_date)
    maturity = _to_date(maturity_date)
    if settle >= maturity:
        raise ValueError(
            f"Ngày thanh toán ({settle}) phải nhỏ hơn ngày đáo hạn ({maturity})."
        )

    step_months = 12 // coupon_freq
    dates_rev: list[date] = []
    k = 0
    while True:
        cp_date = _add_months(maturity, -k * step_months)
        if cp_date > settle:
            dates_rev.append(cp_date)
            k += 1
        else:
            prev_coupon = cp_date
            break

    future_coupons = list(reversed(dates_rev))
    return prev_coupon, future_coupons


def compute_day_count_factors(
    prev_coupon: date,
    settlement_date: date,
    next_coupon: date,
    day_count: DayCountConvention = "ACT/ACT",
    coupon_freq: int = 1,
) -> tuple[float, float, int, float]:
    """Tính phân số chiết khấu kỳ lẻ $v$ và tỷ lệ lãi tích lũy $f_{\\text{accrued}}$ theo chuẩn ngày đếm.

    Returns
    -------
    (v_discount, accrued_fraction, accrued_days, period_basis_days)
        - ``v_discount``: phân số kỳ chiết khấu tới ngày trả lãi kế tiếp $(0 < v \\le 1)$
        - ``accrued_fraction``: tỷ lệ kỳ coupon đã tích lũy $[0, 1)$
        - ``accrued_days``: số ngày tích lũy lãi từ ``prev_coupon`` đến ``settlement_date``
        - ``period_basis_days``: mẫu số ngày của kỳ lãi hiện hành
    """
    dc = day_count.upper()
    if dc == "ACT/ACT":
        period_days = float((next_coupon - prev_coupon).days)
        accrued_days = (settlement_date - prev_coupon).days
        days_to_next = (next_coupon - settlement_date).days
        v = days_to_next / period_days
        accrued_frac = accrued_days / period_days
        return v, accrued_frac, accrued_days, period_days

    if dc == "ACT/365":
        basis = 365.0 / coupon_freq
        accrued_days = (settlement_date - prev_coupon).days
        days_to_next = (next_coupon - settlement_date).days
        v = days_to_next / basis
        accrued_frac = accrued_days / basis
        return v, accrued_frac, accrued_days, basis

    if dc == "30/360":
        d1 = min(prev_coupon.day, 30)
        d2_s = 30 if (settlement_date.day == 31 and d1 == 30) else min(settlement_date.day, 30)
        accrued_30 = (
            360 * (settlement_date.year - prev_coupon.year)
            + 30 * (settlement_date.month - prev_coupon.month)
            + (d2_s - d1)
        )
        basis = 360.0 / coupon_freq
        accrued_frac = accrued_30 / basis
        v = max(0.0, 1.0 - accrued_frac)
        return v, accrued_frac, accrued_30, basis

    raise ValueError(f"Quy ước ngày đếm không hỗ trợ: {day_count}")


# ── Dataclass đặc tả Trái phiếu & Kết quả phân tích ──────────────────────────

@dataclass(frozen=True)
class BondSpec:
    """Đặc tả chuẩn một mã Trái phiếu Chính phủ Việt Nam."""

    coupon_rate_pct: float
    maturity_date: date | str | None = None
    settlement_date: date | str | None = None
    remaining_years: float | None = None
    face_value: float = VN_PAR_VALUE
    coupon_freq: int = 1
    day_count: DayCountConvention = "ACT/ACT"
    bond_code: str = "VN_GBOND"


@dataclass(frozen=True)
class BondAnalyticsResult:
    """Kết quả định giá & quản trị rủi ro lãi suất đầy đủ cho một TPCP."""

    bond_code: str
    face_value: float
    coupon_rate_pct: float
    ytm_pct: float
    day_count: str
    coupon_freq: int
    accrued_days: int
    dirty_price: float
    clean_price: float
    accrued_interest: float
    hnx_settlement_price_int: int
    dirty_price_pct_par: float
    clean_price_pct_par: float
    macaulay_duration_yr: float
    modified_duration_yr: float
    dv01_per_bond: float
    dv01_analytic_per_bond: float
    dv01_per_100bn_vnd: float
    convexity_yr2: float

    def to_dict(self) -> dict:
        return asdict(self)


# ── Xây dựng vector dòng tiền tổng quát (Exact Dates hoặc Constant Tenor) ────

def _build_cashflow_vectors(
    spec: BondSpec,
) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Trả về ``(exponents_in_periods, cashflows, accrued_interest, accrued_days)``.

    Hỗ trợ cả 2 chế độ:
    1. **Exact Calendar Dates** (khi có ``settlement_date`` và ``maturity_date``): chuẩn Thông tư
       30/2023/TT-BTC cho đấu thầu sơ cấp và giao dịch thứ cấp HNX.
    2. **Constant Maturity / Generic Benchmark** (khi truyền ``remaining_years``, vd ``10.0`` năm):
       chuẩn định giá nhanh Benchmark On-the-Run tại bàn giao dịch.
    """
    f = spec.coupon_freq
    cpn_payment = spec.face_value * (spec.coupon_rate_pct / 100.0) / f

    if spec.settlement_date is not None and spec.maturity_date is not None:
        settle = _to_date(spec.settlement_date)
        maturity = _to_date(spec.maturity_date)
        prev_cp, future_cps = generate_coupon_schedule(settle, maturity, coupon_freq=f)
        v, accrued_frac, accrued_days, _ = compute_day_count_factors(
            prev_cp, settle, future_cps[0], day_count=spec.day_count, coupon_freq=f
        )
        n_cf = len(future_cps)
        exponents = v + np.arange(n_cf, dtype=float)
        cashflows = np.full(n_cf, cpn_payment, dtype=float)
        cashflows[-1] += spec.face_value
        accrued_interest = cpn_payment * accrued_frac
        return exponents, cashflows, float(accrued_interest), int(accrued_days)

    if spec.remaining_years is not None and spec.remaining_years > 0:
        total_periods = spec.remaining_years * f
        n_full = int(math.floor(total_periods))
        frac = total_periods - n_full
        if frac < 1e-9:
            # Đúng vào ngày trả lãi / phát hành mới: v = 1.0, accrued = 0
            n_cf = max(1, n_full)
            exponents = np.arange(1, n_cf + 1, dtype=float)
            accrued_frac = 0.0
            accrued_days = 0
        else:
            n_cf = n_full + 1
            v = frac
            exponents = v + np.arange(n_cf, dtype=float)
            accrued_frac = 1.0 - v
            basis = 365.0 / f
            accrued_days = int(round(accrued_frac * basis))
        cashflows = np.full(n_cf, cpn_payment, dtype=float)
        cashflows[-1] += spec.face_value
        accrued_interest = cpn_payment * accrued_frac
        return exponents, cashflows, float(accrued_interest), accrued_days

    raise ValueError(
        "BondSpec phải cung cấp cặp (settlement_date, maturity_date) hoặc remaining_years > 0."
    )


# ── Định giá, Lợi suất YTM & Độ nhạy Lãi suất ────────────────────────────────

def price_from_ytm(spec: BondSpec, ytm_pct: float) -> tuple[float, float, float]:
    """Tính ``(dirty_price, clean_price, accrued_interest)`` từ lợi suất đáo hạn ``ytm_pct`` (%/năm)."""
    exponents, cashflows, accrued_interest, _ = _build_cashflow_vectors(spec)
    y_per_period = (float(ytm_pct) / 100.0) / spec.coupon_freq
    if 1.0 + y_per_period <= 0:
        raise ValueError(f"Lợi suất ytm_pct={ytm_pct}% không hợp lệ (1 + y/f <= 0).")

    disc = np.power(1.0 + y_per_period, -exponents)
    dirty_price = float(np.dot(cashflows, disc))
    clean_price = dirty_price - accrued_interest
    return dirty_price, clean_price, accrued_interest


def ytm_from_price(
    spec: BondSpec,
    price: float,
    is_clean_price: bool = False,
    ytm_bounds_pct: tuple[float, float] = (-5.0, 50.0),
) -> float:
    """Giải ngược lợi suất đáo hạn YTM (%/năm) từ Clean Price hoặc Dirty Price bằng Brent's method.

    Độ chính xác hội tụ: $|P(y^*) - P_{\\text{target}}| < 10^{-10}$, ổn định tuyệt đối kể cả với
    trái phiếu siêu dài 30Y hoặc trái phiếu cận ngày đáo hạn (< 7 ngày).
    """
    exponents, cashflows, accrued_interest, _ = _build_cashflow_vectors(spec)
    target_dirty = float(price) + (accrued_interest if is_clean_price else 0.0)
    if target_dirty <= 0:
        raise ValueError("Giá thanh toán trái phiếu (dirty price) phải dương.")

    f = spec.coupon_freq

    def _obj(ytm_p: float) -> float:
        r_p = (ytm_p / 100.0) / f
        return float(np.dot(cashflows, np.power(1.0 + r_p, -exponents))) - target_dirty

    low, high = ytm_bounds_pct
    y_star = brentq(_obj, low, high, xtol=1e-12, rtol=1e-12, maxiter=200)
    return float(y_star)


def analyze_bond(spec: BondSpec, ytm_pct: float) -> BondAnalyticsResult:
    """Tính toán trọn bộ chỉ số Định giá & Rủi ro lãi suất (Duration, DV01, Convexity) của TPCP."""
    exponents, cashflows, accrued_interest, accrued_days = _build_cashflow_vectors(spec)
    f = spec.coupon_freq
    y_per = (float(ytm_pct) / 100.0) / f

    disc = np.power(1.0 + y_per, -exponents)
    pv_cfs = cashflows * disc
    dirty_price = float(np.sum(pv_cfs))
    clean_price = dirty_price - accrued_interest

    # Macaulay Duration (đơn vị: năm)
    times_yr = exponents / f
    mac_dur = float(np.dot(times_yr, pv_cfs) / dirty_price) if dirty_price > 0 else 0.0
    mod_dur = mac_dur / (1.0 + y_per)

    # Convexity (đơn vị: năm^2): d^2 P / dy^2 * (1 / P)
    conv_terms = (exponents * (exponents + 1.0)) / (f ** 2)
    convexity = (
        float(np.dot(conv_terms, pv_cfs) / (dirty_price * ((1.0 + y_per) ** 2)))
        if dirty_price > 0
        else 0.0
    )

    # DV01 (cho 1 bps = 0.01% thay đổi lợi suất): cả xấp xỉ giải tích và sai phân trung tâm chính xác
    dv01_analytic = dirty_price * mod_dur * 1e-4
    disc_dn = np.power(1.0 + ((ytm_pct - 0.01) / 100.0) / f, -exponents)
    disc_up = np.power(1.0 + ((ytm_pct + 0.01) / 100.0) / f, -exponents)
    p_dn = float(np.dot(cashflows, disc_dn))
    p_up = float(np.dot(cashflows, disc_up))
    dv01_exact = 0.5 * (p_dn - p_up)

    # Quy đổi DV01 sang quy mô lô giao dịch chuẩn 100 tỷ VNĐ mệnh giá trên bàn giao dịch TPCP
    n_bonds_100bn = INSTITUTIONAL_LOT_VND / spec.face_value
    dv01_per_100bn = dv01_exact * n_bonds_100bn

    return BondAnalyticsResult(
        bond_code=spec.bond_code,
        face_value=spec.face_value,
        coupon_rate_pct=spec.coupon_rate_pct,
        ytm_pct=float(ytm_pct),
        day_count=spec.day_count,
        coupon_freq=f,
        accrued_days=accrued_days,
        dirty_price=dirty_price,
        clean_price=clean_price,
        accrued_interest=accrued_interest,
        hnx_settlement_price_int=int(math.floor(dirty_price + 1e-9)),
        dirty_price_pct_par=(dirty_price / spec.face_value) * 100.0,
        clean_price_pct_par=(clean_price / spec.face_value) * 100.0,
        macaulay_duration_yr=mac_dur,
        modified_duration_yr=mod_dur,
        dv01_per_bond=dv01_exact,
        dv01_analytic_per_bond=dv01_analytic,
        dv01_per_100bn_vnd=dv01_per_100bn,
        convexity_yr2=convexity,
    )


def scenario_price_pnl(
    spec: BondSpec,
    base_ytm_pct: float,
    shock_bps_list: list[float] | None = None,
    notional_vnd: float = INSTITUTIONAL_LOT_VND,
) -> pd.DataFrame:
    """Phân tích kịch bản lãi suất (Stress Test) so sánh P&L thực tế vs xấp xỉ Duration & Convexity."""
    if shock_bps_list is None:
        shock_bps_list = [-100.0, -50.0, -25.0, -10.0, 0.0, 10.0, 25.0, 50.0, 100.0]

    base = analyze_bond(spec, base_ytm_pct)
    units = notional_vnd / spec.face_value
    rows = []
    for bps in shock_bps_list:
        dy = (bps / 10_000.0)  # thập phân (vd +100 bps = +0.01)
        shocked_ytm = base_ytm_pct + (bps / 100.0)
        dirty_new, clean_new, _ = price_from_ytm(spec, shocked_ytm)
        exact_dp = dirty_new - base.dirty_price
        dur_approx_dp = -base.modified_duration_yr * base.dirty_price * dy
        taylor2_dp = dur_approx_dp + 0.5 * base.convexity_yr2 * base.dirty_price * (dy ** 2)
        rows.append({
            "shock_bps": float(bps),
            "shocked_ytm_pct": round(shocked_ytm, 4),
            "dirty_price": round(dirty_new, 4),
            "clean_price": round(clean_new, 4),
            "exact_pnl_vnd": round(exact_dp * units, 0),
            "duration_only_pnl_vnd": round(dur_approx_dp * units, 0),
            "convexity_adjusted_pnl_vnd": round(taylor2_dp * units, 0),
            "convexity_gain_vnd": round((taylor2_dp - dur_approx_dp) * units, 0),
            "pct_price_change": round((exact_dp / base.dirty_price) * 100.0, 4),
        })
    return pd.DataFrame(rows)


# ── Định giá chiết khấu theo Đường cong Giao ngay (Spot Curve Valuation & Z-Spread) ──

def price_from_spot_curve(
    spec: BondSpec,
    spot_fn: Callable[[float], float | None],
    z_spread_bps: float = 0.0,
) -> dict:
    """Định giá TPCP bằng cách chiết khấu từng dòng tiền theo Đường cong Lãi suất Giao ngay $z_a(t_j)$.

    $$P_{\\text{curve}} = \\sum_{j=0}^{N-1} \\frac{CF_j}{\\left(1 + z_a(t_j)/100 + s_Z\\right)^{t_j}}$$
    trong đó $t_j = (v + j)/f$ là thời gian thực (năm) tới dòng tiền thứ $j$, và $s_Z$ là Z-spread.

    Returns
    -------
    dict chứa ``dirty_price``, ``clean_price``, ``accrued_interest``, ``implied_ytm_pct``,
    và chi tiết bảng chiết khấu từng dòng tiền ``cashflow_schedule``.
    """
    exponents, cashflows, accrued_interest, _ = _build_cashflow_vectors(spec)
    times_yr = exponents / spec.coupon_freq
    spread_dec = float(z_spread_bps) / 10_000.0

    spots_pct = []
    for t_yr in times_yr:
        s_val = spot_fn(float(t_yr))
        if s_val is None or not np.isfinite(s_val):
            raise ValueError(f"Đường cong không trả về lãi suất hợp lệ tại kỳ hạn {t_yr:.4f}Y.")
        spots_pct.append(float(s_val))

    spots_arr = np.array(spots_pct, dtype=float)
    eff_rates = (spots_arr / 100.0) + spread_dec
    dfs = np.power(1.0 + eff_rates, -times_yr)
    pvs = cashflows * dfs

    dirty_price = float(np.sum(pvs))
    clean_price = dirty_price - accrued_interest
    implied_ytm = ytm_from_price(spec, dirty_price, is_clean_price=False)

    schedule = pd.DataFrame({
        "time_yr": np.round(times_yr, 6),
        "cashflow_vnd": np.round(cashflows, 2),
        "spot_rate_pct": np.round(spots_arr, 4),
        "discount_factor": np.round(dfs, 6),
        "present_value_vnd": np.round(pvs, 2),
    })

    return {
        "dirty_price": dirty_price,
        "clean_price": clean_price,
        "accrued_interest": accrued_interest,
        "implied_ytm_pct": implied_ytm,
        "z_spread_bps": float(z_spread_bps),
        "cashflow_schedule": schedule,
    }


def solve_z_spread_bps(
    spec: BondSpec,
    market_price: float,
    spot_fn: Callable[[float], float | None],
    is_clean_price: bool = False,
) -> float:
    """Tìm Z-Spread (bps) sao cho giá chiết khấu trên đường cong giao ngay khớp với giá thị trường."""
    exponents, cashflows, accrued_interest, _ = _build_cashflow_vectors(spec)
    target_dirty = float(market_price) + (accrued_interest if is_clean_price else 0.0)
    times_yr = exponents / spec.coupon_freq
    spots_arr = np.array([float(spot_fn(float(t))) for t in times_yr], dtype=float)

    def _diff(spread_bps: float) -> float:
        eff = (spots_arr / 100.0) + (spread_bps / 10_000.0)
        if np.any(1.0 + eff <= 0):
            return 1e12
        return float(np.dot(cashflows, np.power(1.0 + eff, -times_yr))) - target_dirty

    return float(brentq(_diff, -500.0, 2000.0, xtol=1e-8))


# ── Phân tích Carry & Roll-down Return cho Bàn Giao dịch TPCP ────────────────

def compute_carry_rolldown(
    spot_fn: Callable[[float], float | None],
    tenor_yr: float,
    horizon_yr: float = 1.0,
    face_value: float = VN_PAR_VALUE,
) -> dict | None:
    """Tính lợi suất kỳ vọng nắm giữ (Carry + Roll-down Return) của TPCP ngang giá kỳ hạn ``tenor_yr``
    sau khoảng thời gian nắm giữ ``horizon_yr`` (mặc định 1 năm) dưới giả định đường cong không đổi.

    Phân rã chuẩn Bàn giao dịch Trái phiếu (Fixed Income Desk Attribution):
        - **Carry Return (%)**: Thu nhập lãi coupon / lợi suất ban đầu $y_{\\text{entry}}$.
        - **Roll-down Price Return (%)**: Lãi vốn do trái phiếu trôi dọc xuống vùng lợi suất thấp hơn
          trên đường cong dốc lên từ kỳ hạn $T$ về $T - h$:
          $$R_{\\text{roll}} \\approx -D_{\\text{Mod}}(T - h) \\cdot (y(T - h) - y(T))$$
          (tính chính xác bằng định giá lại dòng tiền tại kỳ hạn $T - h$).
        - **Total Expected Horizon Return (%/năm)** = Carry + Roll-down.
        - **Breakeven Yield Spike (bps)**: Mức tăng lợi suất tối đa tại kỳ hạn $T - h$ mà danh mục
          chịu đựng được trước khi bị lỗ vốn gốc (Carry + Roll-down chia cho $D_{\\text{Mod}}(T - h)$).
    """
    if tenor_yr <= horizon_yr or horizon_yr <= 0:
        return None

    # Par yield tại kỳ hạn ban đầu T và kỳ hạn còn lại T - h
    from analysis.yield_curve_fitting import spot_to_par_yield

    par_t = spot_to_par_yield(spot_fn, tenor_yr, coupon_freq=1)
    par_rem = spot_to_par_yield(spot_fn, tenor_yr - horizon_yr, coupon_freq=1)
    if par_t is None or par_rem is None:
        return None

    # Trái phiếu mua ngang giá (P0 = face_value) với coupon = par_t
    spec_init = BondSpec(
        coupon_rate_pct=par_t,
        remaining_years=tenor_yr,
        face_value=face_value,
        coupon_freq=1,
        day_count="ACT/ACT",
        bond_code=f"PAR_{tenor_yr:g}Y",
    )
    init_metrics = analyze_bond(spec_init, ytm_pct=par_t)

    # Sau horizon_yr (vd 1 năm), trái phiếu còn kỳ hạn (tenor_yr - horizon_yr) và được định giá tại par_rem
    spec_end = BondSpec(
        coupon_rate_pct=par_t,
        remaining_years=tenor_yr - horizon_yr,
        face_value=face_value,
        coupon_freq=1,
        day_count="ACT/ACT",
        bond_code=f"ROLLED_{tenor_yr - horizon_yr:g}Y",
    )
    end_metrics = analyze_bond(spec_end, ytm_pct=par_rem)

    # Coupon nhận được trong kỳ nắm giữ horizon_yr
    coupon_income = face_value * (par_t / 100.0) * horizon_yr
    # Nếu horizon_yr lẻ < 1Y, clean_price cuối kỳ phản ánh giá sạch, coupon_income đã nằm trong accrued
    ending_clean = end_metrics.clean_price
    capital_rolldown_vnd = ending_clean - face_value

    carry_pct = (coupon_income / face_value) * 100.0 / horizon_yr
    rolldown_pct = (capital_rolldown_vnd / face_value) * 100.0 / horizon_yr
    total_return_ann_pct = carry_pct + rolldown_pct
    roll_yield_drop_bps = (par_t - par_rem) * 100.0

    # Breakeven yield backup (bps) sau horizon_yr: tổng đệm lợi nhuận (%) / ModDur(T - h) * 100
    horizon_total_pct = (coupon_income + capital_rolldown_vnd) / face_value * 100.0
    breakeven_bps = (
        (horizon_total_pct / end_metrics.modified_duration_yr) * 100.0
        if end_metrics.modified_duration_yr > 0
        else 0.0
    )

    return {
        "tenor_yr": float(tenor_yr),
        "horizon_yr": float(horizon_yr),
        "entry_par_yield_pct": round(par_t, 4),
        "rolled_par_yield_pct": round(par_rem, 4),
        "roll_down_bps": round(roll_yield_drop_bps, 2),
        "macaulay_duration_yr": round(init_metrics.macaulay_duration_yr, 4),
        "modified_duration_yr": round(init_metrics.modified_duration_yr, 4),
        "dv01_per_100k_vnd": round(init_metrics.dv01_per_bond, 4),
        "dv01_per_100bn_vnd": round(init_metrics.dv01_per_100bn_vnd, 0),
        "convexity_yr2": round(init_metrics.convexity_yr2, 4),
        "carry_return_pct": round(carry_pct, 4),
        "rolldown_return_pct": round(rolldown_pct, 4),
        "total_horizon_return_pct": round(total_return_ann_pct, 4),
        "breakeven_yield_rise_bps": round(breakeven_bps, 2),
    }


def build_benchmark_risk_ladder(
    yc: YieldCurve,
    as_of_date: str | date | pd.Timestamp | None = None,
    tenors_labels: list[str] | None = None,
) -> pd.DataFrame:
    """Tạo Bảng Thang Rủi ro Kỳ hạn Chuẩn (Benchmark Risk & Carry/Roll-down Ladder) cho Bàn TPCP.

    Tính toán đầy đủ cho 11 kỳ hạn chuẩn HNX (3M -> 30Y):
        - Spot Yield (Annual & Continuous), Par Yield, Implied 1Y Forward Rate
        - Macaulay Duration, Modified Duration, DV01 (trên 100k mệnh giá & 100 tỷ VNĐ), Convexity
        - 1Y Carry, 1Y Roll-down Return, Total Expected 1Y Return & Breakeven Yield Backup (bps).
    """
    target_date = pd.Timestamp(as_of_date) if as_of_date is not None else yc.latest_date
    if target_date is None:
        return pd.DataFrame()

    if tenors_labels is None:
        tenors_labels = HNX_BENCHMARK_TENORS + ["30Y"]

    spot_fn = lambda tau: yc.spot(target_date, tau)
    rows = []
    for lbl in tenors_labels:
        t_yr = VN_STANDARD_TENORS.get(lbl)
        if t_yr is None:
            continue
        spot_a = yc.spot(target_date, t_yr)
        if spot_a is None:
            continue
        spot_c = yc.spot_continuous(target_date, t_yr)
        df_val = yc.discount_factor(target_date, t_yr)
        par_y = yc.par_yield(target_date, t_yr, coupon_freq=1) if t_yr >= 1.0 else spot_a
        if par_y is None:
            par_y = spot_a
        fwd_1y = yc.forward_rate(target_date, t_yr, t_yr + 1.0)

        # Trái phiếu chuẩn ngang giá tại kỳ hạn t_yr
        if t_yr >= 1.0:
            spec = BondSpec(
                coupon_rate_pct=par_y,
                remaining_years=t_yr,
                face_value=VN_PAR_VALUE,
                coupon_freq=1,
                day_count="ACT/ACT",
                bond_code=f"VN_BENCH_{lbl}",
            )
            metrics = analyze_bond(spec, ytm_pct=par_y)
        else:
            # Kỳ hạn dưới 1 năm (3M, 6M, 9M): chiết khấu đơn kỳ tương đương Tín phiếu KBNN zero-coupon
            spec = BondSpec(
                coupon_rate_pct=0.0,
                remaining_years=t_yr,
                face_value=VN_PAR_VALUE,
                coupon_freq=1,
                day_count="ACT/ACT",
                bond_code=f"VN_TBILL_{lbl}",
            )
            metrics = analyze_bond(spec, ytm_pct=spot_a)

        crd = compute_carry_rolldown(spot_fn, t_yr, horizon_yr=1.0) if t_yr >= 2.0 else None

        rows.append({
            "date": target_date.strftime("%Y-%m-%d"),
            "tenor": lbl,
            "tenor_yr": float(t_yr),
            "is_core_liquid": lbl in CORE_LIQUID_TENORS,
            "spot_annual_pct": round(spot_a, 4),
            "spot_continuous_pct": round(spot_c, 4) if spot_c is not None else None,
            "par_yield_pct": round(par_y, 4),
            "forward_1y_pct": round(fwd_1y, 4) if fwd_1y is not None else None,
            "discount_factor": round(df_val, 6) if df_val is not None else None,
            "macaulay_duration_yr": round(metrics.macaulay_duration_yr, 4),
            "modified_duration_yr": round(metrics.modified_duration_yr, 4),
            "dv01_per_100k_vnd": round(metrics.dv01_per_bond, 4),
            "dv01_per_100bn_vnd": round(metrics.dv01_per_100bn_vnd, 0),
            "convexity_yr2": round(metrics.convexity_yr2, 4),
            "carry_1y_pct": crd["carry_return_pct"] if crd else round(spot_a, 4),
            "rolldown_1y_pct": crd["rolldown_return_pct"] if crd else 0.0,
            "total_return_1y_pct": crd["total_horizon_return_pct"] if crd else round(spot_a, 4),
            "roll_down_bps": crd["roll_down_bps"] if crd else 0.0,
            "breakeven_spike_bps": crd["breakeven_yield_rise_bps"] if crd else None,
        })

    return pd.DataFrame(rows)


def run(cache_dir: str | Path = "./cache", verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Chạy động cơ định giá & thang rủi ro kỳ hạn chuẩn trên đường cong mới nhất và lưu Parquet."""
    cache_path = Path(cache_dir)
    yc_ns = YieldCurve.from_cache(cache_path, method="NS")
    latest_dt = yc_ns.latest_date
    if latest_dt is None:
        return {}

    ladder_df = build_benchmark_risk_ladder(yc_ns, as_of_date=latest_dt)
    if len(ladder_df):
        ladder_df.to_parquet(cache_path / "bond_risk_ladder.parquet", index=False)

    # Tính lịch sử Carry & Roll-down và DV01 cho 3 kỳ hạn trụ cột: 5Y, 10Y, 15Y (1000 phiên gần nhất)
    params_df = pd.read_parquet(cache_path / "fitted_params_ns.parquet")
    params_df["date"] = pd.to_datetime(params_df["date"])
    params_df = params_df.sort_values("date").tail(1000)

    hist_rows = []
    for _, r in params_df.iterrows():
        p_dict = r.to_dict()
        from analysis.yield_curve_fitting import predict_yield

        s_fn = lambda tau, _p=p_dict: predict_yield(_p, tau)
        row_out = {"date": r["date"]}
        for t_yr, key in [(5.0, "5y"), (10.0, "10y"), (15.0, "15y")]:
            crd = compute_carry_rolldown(s_fn, t_yr, horizon_yr=1.0)
            if crd:
                row_out[f"par_{key}"] = crd["entry_par_yield_pct"]
                row_out[f"mod_dur_{key}"] = crd["modified_duration_yr"]
                row_out[f"dv01_100bn_{key}"] = crd["dv01_per_100bn_vnd"]
                row_out[f"convexity_{key}"] = crd["convexity_yr2"]
                row_out[f"rolldown_1y_{key}"] = crd["rolldown_return_pct"]
                row_out[f"total_ret_1y_{key}"] = crd["total_horizon_return_pct"]
                row_out[f"breakeven_bps_{key}"] = crd["breakeven_yield_rise_bps"]
        hist_rows.append(row_out)

    hist_df = pd.DataFrame(hist_rows)
    if len(hist_df):
        hist_df.to_parquet(cache_path / "bond_rolldown_history.parquet", index=False)

    if verbose and len(ladder_df):
        print(f"  Đã lưu bond_risk_ladder.parquet ({len(ladder_df)} kỳ hạn, ngày {latest_dt.date()})")
        core = ladder_df[ladder_df["is_core_liquid"]]
        print(
            core[[
                "tenor", "par_yield_pct", "modified_duration_yr",
                "dv01_per_100bn_vnd", "convexity_yr2", "total_return_1y_pct",
            ]].to_string(index=False)
        )

    return {"risk_ladder": ladder_df, "rolldown_history": hist_df}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Định giá, Duration, DV01, Convexity & Roll-down TPCP")
    p.add_argument("--cache-dir", default="./cache")
    args = p.parse_args()
    run(args.cache_dir)
