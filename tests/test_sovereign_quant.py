"""
test_sovereign_quant.py — Bộ kiểm thử chuẩn mực nghiệp vụ Định lượng TPCP Việt Nam.
====================================================================================

Kiểm chứng toàn diện các tiêu chuẩn bắt buộc:
    1. Đối chiếu thực nghiệm công thức định giá HNX (Thông tư 30/2023/TT-BTC, Actual/Actual ICMA)
       khớp 100% đến từng đồng VNĐ với giá thanh toán đấu thầu thực tế trên HNX (năm nhuận 2024 & 2026).
    2. Đẳng thức biến đổi cấu trúc kỳ hạn: Spot Annual <-> Spot Continuous <-> Discount Factor <->
       Par Yield <-> Implied Forward Rate (khớp 100% số liệu HNX ngày 20/11/2026).
    3. Độ chính xác giải ngược YTM (Brent's method < 1e-10), Duration, DV01, Convexity, Z-Spread,
       và Carry + Roll-down Return.
    4. Kiểm soát chất lượng dữ liệu: không trộn lẫn spot_annual/spot_continuous/primary_winning_yield,
       loại bỏ ngoại lai ngoại suy HNX (-4.4% / +51.7%), và chống tạo ngày nghỉ giả bằng ffill/bfill.
"""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.bond_pricing import (
    VN_PAR_VALUE,
    BondSpec,
    analyze_bond,
    build_benchmark_risk_ladder,
    compute_carry_rolldown,
    price_from_spot_curve,
    price_from_ytm,
    scenario_price_pnl,
    solve_z_spread_bps,
    ytm_from_price,
)
from analysis.spread_analysis import (
    classify_curve_regime,
    compute_spreads_from_curve,
    compute_yield_curve_pca,
)
from analysis.yield_curve_fitting import (
    YieldCurve,
    bootstrap_spot_from_par,
    discount_factor,
    extract_clean_spot_panel,
    fit_day,
    implied_forward_rate,
    interpolate_nonparametric,
    reject_fitted_curve_outliers,
    spot_annual_to_continuous,
    spot_continuous_to_annual,
    spot_to_par_yield,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache"
EXPORTS_DATA_DIR = REPO_ROOT / "exports" / "data"


class TestVietnamSovereignBondPricing(unittest.TestCase):
    """Kiểm chứng định giá, lợi suất và độ nhạy rủi ro TPCP theo chuẩn HNX (Thông tư 30/2023/TT-BTC)."""

    def test_hnx_exact_settlement_reopened_10y_2026(self) -> None:
        """Đối chiếu 8 phiên phát hành & phát hành bổ sung (reopening) mã TD2636025 (10Y, c=4.4%)."""
        maturity = date(2036, 10, 1)
        hnx_sessions = [
            (date(2026, 10, 1), 4.43, 99761),
            (date(2026, 10, 8), 4.43, 99844),
            (date(2026, 10, 15), 4.43, 99927),
            (date(2026, 10, 22), 4.43, 100010),
            (date(2026, 10, 29), 4.43, 100094),
            (date(2026, 11, 5), 4.43, 100177),
            (date(2026, 11, 12), 4.43, 100260),
            (date(2026, 11, 19), 4.41, 100501),
        ]
        for settle, ytm_win, expected_hnx_int in hnx_sessions:
            spec = BondSpec(
                coupon_rate_pct=4.4,
                settlement_date=settle,
                maturity_date=maturity,
                face_value=VN_PAR_VALUE,
                coupon_freq=1,
                day_count="ACT/ACT",
                bond_code="TD2636025",
            )
            res = analyze_bond(spec, ytm_pct=ytm_win)
            self.assertEqual(
                res.hnx_settlement_price_int,
                expected_hnx_int,
                msg=f"Sai lệch giá thanh toán HNX tại ngày {settle}",
            )
            # Kiểm tra giải ngược YTM từ Clean Price và Dirty Price khớp chính xác tuyệt đối
            ytm_back_dirty = ytm_from_price(spec, res.dirty_price, is_clean_price=False)
            ytm_back_clean = ytm_from_price(spec, res.clean_price, is_clean_price=True)
            self.assertAlmostEqual(ytm_back_dirty, ytm_win, places=8)
            self.assertAlmostEqual(ytm_back_clean, ytm_win, places=8)

    def test_hnx_leap_year_act_act_icma_2024(self) -> None:
        """Kiểm chứng kỳ tính lãi đi qua ngày 29/02/2024 (E=366 ngày) trên mã TD2429007 (5Y)."""
        # Phát hành lần đầu 11/01/2024 -> đáo hạn 11/01/2029; kỳ lãi đầu có ngày 29/02/2024 => E = 366
        auctions = pd.read_parquet(CACHE_DIR / "hnx_auctions_daily.parquet")
        rows = auctions[
            (auctions["Mã trái phiếu"] == "TD2429007") & (auctions["auction_successful"] == True)  # noqa: E712
        ].head(8)
        self.assertGreaterEqual(len(rows), 5)

        for _, r in rows.iterrows():
            settle = r["Ngày phát hành"]
            cp = float(str(r["Lãi suất danh nghĩa (%/Năm)"]).replace(",", "."))
            ytm = float(str(r["Lãi suất trúng thầu (%/Năm)"]).replace(",", "."))
            face_tot = float(str(r["GT trúng thầu"]).replace(".", "").replace(",", "."))
            cash_tot = float(str(r["Số tiền thanh toán trái phiếu trúng thầu"]).replace(".", "").replace(",", "."))
            expected_int = int(round(cash_tot / (face_tot / VN_PAR_VALUE)))

            spec = BondSpec(
                coupon_rate_pct=cp,
                settlement_date=settle,
                maturity_date="11/01/2029",
                day_count="ACT/ACT",
                bond_code="TD2429007",
            )
            res = analyze_bond(spec, ytm_pct=ytm)
            self.assertEqual(res.hnx_settlement_price_int, expected_int)

    def test_duration_dv01_convexity_taylor_accuracy(self) -> None:
        """Kiểm tra đạo hàm bậc 1 (Modified Duration / DV01) và bậc 2 (Convexity) cho TPCP 10Y."""
        spec = BondSpec(coupon_rate_pct=4.50, remaining_years=10.0, bond_code="VN10Y_BENCH")
        res = analyze_bond(spec, ytm_pct=4.50)

        # Tại par (coupon == YTM, vào đúng ngày phát hành): Dirty == Clean == 100,000 VNĐ
        self.assertAlmostEqual(res.dirty_price, 100_000.0, places=6)
        self.assertAlmostEqual(res.clean_price, 100_000.0, places=6)
        self.assertAlmostEqual(res.accrued_interest, 0.0, places=8)

        # DV01 giải tích và DV01 sai phân trung tâm phải khớp chặt (< 1e-4 VNĐ trên 100k)
        self.assertAlmostEqual(res.dv01_per_bond, res.dv01_analytic_per_bond, places=4)
        self.assertTrue(7.5 < res.modified_duration_yr < 8.5)
        self.assertTrue(70.0 < res.convexity_yr2 < 85.0)

        # Kiểm tra sai số khai triển Taylor bậc 2 với cú sốc +50 bps nhỏ hơn < 0.02% mệnh giá
        scen = scenario_price_pnl(spec, base_ytm_pct=4.50, shock_bps_list=[-100, -50, 50, 100])
        row_50 = scen[scen["shock_bps"] == 50.0].iloc[0]
        rel_err = abs(row_50["exact_pnl_vnd"] - row_50["convexity_adjusted_pnl_vnd"]) / 100e9
        self.assertLess(rel_err, 2e-4)

    def test_near_maturity_and_spot_curve_zspread(self) -> None:
        """Kiểm tra edge case trái phiếu còn 5 ngày tới đáo hạn và giải ngược Z-Spread."""
        spec_short = BondSpec(
            coupon_rate_pct=4.2,
            settlement_date="2026-11-25",
            maturity_date="2026-11-30",
            day_count="ACT/ACT",
        )
        res_short = analyze_bond(spec_short, ytm_pct=3.80)
        self.assertGreater(res_short.dirty_price, 104_000.0)
        self.assertLess(res_short.modified_duration_yr, 0.02)

        # Kiểm tra Z-Spread trên đường cong thực tế
        yc = YieldCurve.from_cache(CACHE_DIR, method="NS")
        spec_5y = BondSpec(coupon_rate_pct=4.30, remaining_years=5.0)
        spot_fn = lambda tau: yc.spot("2026-11-20", tau)
        val_25bps = price_from_spot_curve(spec_5y, spot_fn, z_spread_bps=25.0)
        solved_z = solve_z_spread_bps(spec_5y, val_25bps["dirty_price"], spot_fn, is_clean_price=False)
        self.assertAlmostEqual(solved_z, 25.0, places=5)


class TestYieldCurveAndTermStructure(unittest.TestCase):
    """Kiểm chứng nội suy đường cong lợi suất, tách bạch Spot/Par/Forward và PCA."""

    def test_hnx_spot_continuous_and_par_identities(self) -> None:
        """Đối chiếu công thức toán học Spot Annual -> Spot Continuous & Par Yield trên HNX 20/11/2026."""
        yc_raw = pd.read_parquet(CACHE_DIR / "hnx_yield_curve_daily.parquet")
        day = yc_raw[yc_raw["date"] == "2026-11-20"].pivot(
            index="tenor", columns="rate_type", values="value"
        )
        calc_cont = spot_annual_to_continuous(day["spot_annual"].to_numpy())
        np.testing.assert_allclose(calc_cont, day["spot_continuous"].to_numpy(), atol=1e-6)
        np.testing.assert_allclose(
            spot_continuous_to_annual(calc_cont), day["spot_annual"].to_numpy(), atol=1e-6
        )

        # Đối chiếu Par Yield 1Y, 2Y, 3Y từ Spot Annual thực tế HNX
        spot_map = {
            1.0: float(day.loc["1Y", "spot_annual"]),
            2.0: float(day.loc["2Y", "spot_annual"]),
            3.0: float(day.loc["3Y", "spot_annual"]),
        }
        for yr, lbl in [(1, "1Y"), (2, "2Y"), (3, "3Y")]:
            calc_par = spot_to_par_yield(lambda t: spot_map[float(t)], yr, coupon_freq=1)
            self.assertAlmostEqual(calc_par, float(day.loc[lbl, "par_yield"]), places=5)

        # Kiểm tra vòng khép kín Bootstrap Spot từ Par Yield
        boot = bootstrap_spot_from_par(
            np.array([1.0, 2.0, 3.0]),
            np.array([day.loc["1Y", "par_yield"], day.loc["2Y", "par_yield"], day.loc["3Y", "par_yield"]]),
        )
        for yr in (1.0, 2.0, 3.0):
            s_boot = float(boot.loc[boot["tenor_yr"] == yr, "spot_annual_pct"].iloc[0])
            self.assertAlmostEqual(s_boot, spot_map[yr], places=5)

    def test_no_rate_type_mixing_and_outlier_sanitization(self) -> None:
        """Đảm bảo extract_clean_spot_panel không trộn spot_continuous/primary_winning_yield vào spot_annual."""
        comb = pd.read_parquet(CACHE_DIR / "combined_tpcp_yields.parquet")
        panel = extract_clean_spot_panel(comb)
        # Vào ngày 18/11/2026, spot_annual 10Y của HNX là 4.498452% (còn primary_winning_yield là 4.41%)
        self.assertAlmostEqual(float(panel.loc[pd.Timestamp("2026-11-18"), 10.0]), 4.498452, places=5)
        # Không còn giá trị âm (-4.437%) hay vọt lố (> 20%) do lỗi ngoại suy HNX 2013
        self.assertGreaterEqual(float(panel.min().min()), 0.05)
        self.assertLessEqual(float(panel.max().max()), 20.0)

    def test_interpolation_ns_nss_spline_and_forward_no_arbitrage(self) -> None:
        """Kiểm tra NS, NSS, Cubic Spline, Implied Forward và Carry/Roll-down."""
        t_obs = np.array([0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0])
        y_obs = np.array([3.77, 3.81, 3.85, 3.89, 4.03, 4.15, 4.33, 4.45, 4.54, 4.58, 4.60])

        ns_fit = fit_day(t_obs, y_obs, method="NS")
        nss_fit = fit_day(t_obs, y_obs, method="NSS")
        self.assertIsNotNone(ns_fit)
        self.assertIsNotNone(nss_fit)
        self.assertLess(ns_fit["rmse"], 0.03)
        self.assertLessEqual(nss_fit["rmse"], ns_fit["rmse"] + 1e-4)

        # Cubic spline đi qua chính xác điểm nút 5Y và ngoại suy phẳng an toàn ở 30Y
        y_5y_cs = interpolate_nonparametric(t_obs, y_obs, 5.0, method="cubic_spline")
        y_30y_cs = interpolate_nonparametric(t_obs, y_obs, 30.0, method="cubic_spline")
        self.assertAlmostEqual(float(y_5y_cs), 4.33, places=8)
        self.assertAlmostEqual(float(y_30y_cs), 4.60, places=8)

        # Kiểm tra đẳng thức không kinh doanh chênh lệch giá (No-Arbitrage Forward Identity):
        # DF(2Y) * (1 + f_{2,5})^{-3} == DF(5Y)
        f_2_5 = implied_forward_rate(4.03, 2.0, 4.33, 5.0)
        lhs = discount_factor(4.03, 2.0) * ((1.0 + f_2_5 / 100.0) ** (-3.0))
        rhs = discount_factor(4.33, 5.0)
        self.assertAlmostEqual(lhs, rhs, places=10)

        # Kiểm tra Carry & Roll-down trên đường cong dốc lên: Roll-down > 0
        yc = YieldCurve.from_cache(CACHE_DIR, method="NS")
        crd_5y = compute_carry_rolldown(lambda tau: yc.spot("2026-11-20", tau), tenor_yr=5.0, horizon_yr=1.0)
        self.assertIsNotNone(crd_5y)
        self.assertGreater(crd_5y["rolldown_return_pct"], 0.0)
        self.assertGreater(crd_5y["total_horizon_return_pct"], crd_5y["carry_return_pct"])

    def test_exported_jsons_strict_valid_and_pca_variance(self) -> None:
        """Đảm bảo toàn bộ file JSON xuất cho Web Dashboard hợp lệ (không chứa NaN) và PCA chuẩn."""
        expected_jsons = [
            "fitted_curve_ns.json",
            "fitted_curve_nss.json",
            "fitted_params_ns.json",
            "fitted_params_nss.json",
            "spread_analysis.json",
            "bond_risk_ladder.json",
            "bond_rolldown_history.json",
            "curve_pca.json",
            "auction_stats.json",
            "foreign_flow.json",
            "metadata.json",
        ]
        for fname in expected_jsons:
            fpath = EXPORTS_DATA_DIR / fname
            self.assertTrue(fpath.exists(), f"Thiếu file export {fname}")
            raw_text = fpath.read_text(encoding="utf-8")
            self.assertNotIn("NaN", raw_text, f"File {fname} chứa literal NaN không hợp lệ")
            parsed = json.loads(raw_text)
            self.assertTrue(len(parsed) > 0)

        pca_data = json.loads((EXPORTS_DATA_DIR / "curve_pca.json").read_text(encoding="utf-8"))
        # Ngưỡng 0.80 (hồi chuẩn trên DỮ LIỆU ĐÃ LÀM SẠCH): trước khi dọn, số nhiễu
        # 2013–2016 (răng cưa 500+/ngày) thổi phồng Cumulative_3PC lên ~0.93 giả tạo;
        # dữ liệu sạch cho 0.8575 — vẫn khẳng định 3 thành phần đầu nắm phần lớn
        # phương sai biến động đường cong.
        self.assertGreater(pca_data["summary"]["Cumulative_3PC"], 0.80)


class TestDataQualityFirewall(unittest.TestCase):
    """Regression cho bộ lọc chống ngoại lai — xử lý răng cưa Term Spread 2013–2014.

    Nguồn HNX giai đoạn 2013–2016 có ngày đường cong bị lỗi ngoại suy (20Y par về
    âm, 15Y sập 400bps, các ngày "twist" nhiều ô lệch nhẹ cùng lúc) làm đường cong
    fit nhảy 200–600bps/ngày. Bộ phỏng chứng minh 3 lớp phòng vệ:
      (1) lọc chuỗi thời gian trên điểm đầu vào,
      (2) đối chiếu chéo spot ↔ par,
      (3) lớp QA trên đường cong ĐÃ FIT (chặn suy ngoại suất vọt lố).
    """

    def _make_raw_with_zigzag(self) -> pd.DataFrame:
        dates = pd.date_range("2013-06-03", periods=25, freq="B")
        rows = []
        for i, d in enumerate(dates):
            drift = 0.002 * i  # xu hướng thật rất nhẹ (~2bps/ngày)
            base = {
                "3M": 5.50 + drift, "1Y": 6.30 + drift, "2Y": 7.00 + drift,
                "5Y": 8.50 + drift, "7Y": 9.00 + drift, "10Y": 9.20 + drift,
                "15Y": 9.50 + drift, "20Y": 9.70 + drift,
            }
            if i in (9, 18):  # 2 phiên nguồn lỗi kiểu twist/vọt lố
                base["10Y"] += 2.8
                base["15Y"] += 3.2
                base["20Y"] += 3.5
                base["2Y"] -= 1.1
                base["3M"] -= 0.9
            for tenor, v in base.items():
                rows.append({"date": d, "tenor": tenor, "rate_type": "spot_annual", "value": v})
        return pd.DataFrame(rows)

    def test_input_firewall_removes_zigzag_keeps_smooth_series(self) -> None:
        """Điểm vọt lố bị loại, điểm trơn tru (kể cả biên window) được giữ."""
        panel = extract_clean_spot_panel(self._make_raw_with_zigzag())
        bad_i = [9, 18]
        good_i = [0, 3, 14, 24]
        for i in bad_i:
            d = panel.index[i]
            for tenor in (0.25, 2.0, 10.0, 15.0, 20.0):
                self.assertTrue(pd.isna(panel.loc[d, tenor]),
                                f"Phải loại {tenor}Y tại {d.date()} (ngày lỗi twist)")
        for i in good_i:
            d = panel.index[i]
            for tenor in (10.0, 20.0):
                self.assertFalse(pd.isna(panel.loc[d, tenor]),
                                 f"Không được loại {tenor}Y tại {d.date()} (ngày dữ liệu thật)")

    def test_spot_par_disagreement_downgrades_to_par(self) -> None:
        """Ngày spot_annual 20Y vọt lố xa par_yield ⇒ panel hạ cấp về par thay vì nhận spot."""
        dates = pd.date_range("2015-03-02", periods=15, freq="B")
        rows = []
        for i, d in enumerate(dates):
            par10, par20 = 9.0 + 0.01 * i, 9.6 + 0.01 * i
            rows.append({"date": d, "tenor": "10Y", "rate_type": "par_yield", "value": par10})
            rows.append({"date": d, "tenor": "20Y", "rate_type": "par_yield", "value": par20})
            rows.append({"date": d, "tenor": "10Y", "rate_type": "spot_annual", "value": par10 + 0.03})
            spot20 = par20 + (6.7 if i == 7 else 0.05)  # ngày i=7: spot20 = ~16.3% (lỗi)
            rows.append({"date": d, "tenor": "20Y", "rate_type": "spot_annual", "value": spot20})
        panel = extract_clean_spot_panel(pd.DataFrame(rows))
        d7 = dates[7]
        val = float(panel.loc[d7, 20.0])
        self.assertLess(val, 11.0, "Phải dùng par (~9.7%), không nhận spot 16.3%")
        self.assertAlmostEqual(val, 9.6 + 0.01 * 7, places=6)
        # Các ngày bình thường không đổi (spot cách par 5bps < ngưỡng 75bps)
        self.assertAlmostEqual(float(panel.loc[dates[2], 20.0]), 9.6 + 0.01 * 2 + 0.05, places=6)

    def test_fitted_curve_filter_kills_spike_keeps_real_drift(self) -> None:
        """Lớp QA đường cong fit: điểm suy ngoại suất +180bps bị loại, drift thật 1bps/ngày giữ nguyên."""
        dates = pd.date_range("2015-06-01", periods=21, freq="B")
        rows = []
        for i, d in enumerate(dates):
            for t, base in ((2.0, 6.8), (10.0, 9.0), (30.0, 9.6)):
                v = base + 0.001 * i
                if i == 12 and t == 10.0:
                    v += 1.8  # ngày lỗi: 10Y fit vọt +180bps
                rows.append({"date": d, "tenor_yr": t, "yield_pct": v, "method": "NS"})
        curve = pd.DataFrame(rows)
        out = reject_fitted_curve_outliers(curve)
        self.assertEqual(len(out), len(curve) - 1, "Chỉ đúng 1 điểm lỗi bị loại")
        spike = out[(out["date"] == dates[12]) & (out["tenor_yr"] == 10.0)]
        self.assertTrue(spike.empty, "Điểm 10Y vọt lố phải bị loại")
        self.assertEqual(list(out.columns), ["date", "tenor_yr", "yield_pct", "method"])
        self.assertTrue((out["method"] == "NS").all())

    def test_fitted_curve_filter_on_real_data_scope(self) -> None:
        """Dữ liệu pipeline: đường cong đã mượt, bộ lọc hội tụ, không mất điểm 2Y/5Y/10Y thời kỳ sạch."""
        path = CACHE_DIR / "fitted_curve_ns.parquet"
        if not path.exists():
            self.skipTest("Chưa có cache/fitted_curve_ns.parquet")
        curve = pd.read_parquet(path)
        c = curve.copy()
        c["date"] = pd.to_datetime(c["date"])

        # (a) Bộ lọc đã được áp ở bước fit và HỘI TỤ: chạy lại chỉ loại thêm rất ít.
        out = reject_fitted_curve_outliers(curve)
        lost = len(curve) - len(out)
        self.assertLess(lost / len(curve), 0.02,
                        "Bộ lọc phải đã hội tụ trên đường cong do pipeline tạo")

        # (b) Mục tiêu chính: đường cong 2013 hết răng cưa — không còn ngày
        # nhảy quá 100bps ở kỳ hạn chuẩn 10Y (trước fix: hàng trăm bps/ngày).
        p = c.pivot_table(index="date", columns="tenor_yr", values="yield_pct").sort_index()
        d10 = p[10.0].diff().abs()
        d10_2013 = d10[d10.index.year == 2013].dropna()
        self.assertTrue(len(d10_2013) > 100, "Phải đủ dữ liệu 2013 để kiểm tra")
        self.assertLess(float(d10_2013.max()), 1.0,
                        "Biên động ngày 10Y năm 2013 phải < 100bps sau fix")

        # (c) Thời kỳ dữ liệu sạch không bị mất điểm ở các kỳ hạn chuẩn:
        # 2021+ phải có đủ điểm 2Y/5Y/10Y trên ≥95% ngày có dữ liệu đường cong.
        mod = p[p.index >= "2021-01-01"]
        days = mod.notna().any(axis=1).sum()
        for t in (2.0, 5.0, 10.0):
            cover = mod[t].notna().sum() / days
            self.assertGreaterEqual(cover, 0.95,
                                    f"Phủ điểm {t}Y thời kỳ 2021+ phải ≥95%, got {cover:.1%}")


if __name__ == "__main__":
    unittest.main()
