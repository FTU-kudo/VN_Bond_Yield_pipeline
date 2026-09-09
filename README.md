# 📊 VN Bond Yield Pipeline

**Phân tích toàn diện thị trường Trái Phiếu Chính Phủ Việt Nam**

Pipeline thu thập dữ liệu tự động từ HNX & VBMA, phân tích định lượng bằng phương pháp Nelson-Siegel, và trực quan hóa qua web dashboard tương tác.

---

## 🏛 Tổng Quan Kiến Trúc

Dự án đã được thiết kế lại hoàn toàn từ một script cào dữ liệu đơn giản thành một hệ thống 5 lớp chuyên nghiệp:

```text
VN_Bond_Yield_pipeline/
├── 📁 pipeline/          # Thu thập dữ liệu (HNX + VBMA)
│   ├── common.py         # Tiện ích chung (parse HTML, số VN, cache)
│   ├── hnx_auctions_daily.py     # Kết quả đấu thầu sơ cấp (từ 2012)
│   ├── hnx_yield_curve_daily.py  # Đường cong lợi suất HNX chính thức
│   ├── vbma_activities.py        # Đối chiếu từ VBMA (không cần đăng nhập)
│   └── reconcile.py              # Gộp 3 nguồn → 1 bảng chuẩn hóa
│
├── 📁 analysis/          # Phân tích định lượng
│   ├── yield_curve_fitting.py    # Nelson-Siegel & Svensson fitting
│   ├── spread_analysis.py        # Term spread, butterfly, inversion alerts
│   ├── auction_statistics.py     # BCR, stop-out rate, success rate
│   ├── foreign_flow.py           # Dòng vốn khối ngoại (PDF HNX)
│   └── macro_correlation.py      # Tương quan SBV/CPI/USD
│
├── 📁 charts/            # Trực quan hóa Python/Plotly
│   ├── yield_curve_3d.py         # Surface 3D theo thời gian
│   ├── heatmap_yield.py          # Heatmap tenor × time
│   └── auction_dashboard.py      # Dashboard 4 panel đấu thầu
│
├── 📁 dashboard/         # Web Dashboard (HTML/JS, không cần server)
│   ├── index.html                # Tổng quan thị trường
│   ├── yield_curve.html          # Phân tích đường cong
│   ├── auctions.html             # Phân tích đấu thầu
│   ├── foreign_flows.html        # Dòng vốn ngoại
│   └── assets/style.css, app.js  # Design system + D3.js charts
│
├── run_pipeline.py       # Entry point toàn bộ pipeline
├── requirements.txt      # Thư viện Python
└── .env.example          # Template biến môi trường
```

---

## ⚙️ Cài Đặt & Chạy Nhanh

```bash
# 1. Clone repo
git clone https://github.com/FTU-kudo/VN_Bond_Yield_pipeline.git
cd VN_Bond_Yield_pipeline

# 2. Tạo môi trường ảo (khuyến nghị)
python -m venv .venv
# .venv\Scripts\activate      # Trên Windows
# source .venv/bin/activate   # Trên Linux/Mac

# 3. Cài dependencies
pip install -r requirements.txt
```

### Cách chạy Pipeline (`run_pipeline.py`)

Đây là script tổng (entry point) để gọi tất cả các module.

```bash
# Backfill toàn bộ lịch sử (chạy lần đầu, mất ~30-60 phút)
python run_pipeline.py --mode full --start 2012-01-01

# Backfill từ 2020 (nhanh hơn, đủ bao quát các chu kỳ lãi suất gần đây)
python run_pipeline.py --mode full --start 2020-01-01

# Cập nhật hàng ngày (chỉ tải 7 ngày gần nhất, mất ~15 giây)
python run_pipeline.py --mode daily

# Chỉ chạy phân tích định lượng (từ file cache đã tải về trước đó)
python run_pipeline.py --mode analyze

# Mở Web Dashboard để xem biểu đồ (không cần chạy server)
# Mở file dashboard/index.html bằng bất kỳ trình duyệt nào.
```

---

## 🤖 Tự Động Hóa (GitHub Actions)

Dự án được tích hợp **GitHub Actions** để tự động cập nhật dữ liệu và lưu vào nhánh `main`. Không cần treo máy chủ cá nhân!

- `.github/workflows/backfill.yml`: Dùng để kích hoạt cào toàn bộ dữ liệu lịch sử (Manual Trigger).
- `.github/workflows/daily_update.yml`: Tự động chạy chế độ `--mode daily` và đẩy dữ liệu mới (thư mục `cache/` và `exports/`) lên Github mỗi ngày.

> **💡 Lưu ý:** Đã config bỏ chặn thư mục `cache/` và `exports/` trong `.gitignore` để bot có thể lưu file `.parquet` và `.json` cộng dồn sau mỗi ngày.

---

## 📉 Phương Pháp Phân Tích

1. **Nelson-Siegel Yield Curve Fitting**: Chuẩn quốc tế sử dụng bởi BIS và ECB để mô hình hóa đường cong lợi suất liên tục từ các kỳ hạn rời rạc.
2. **Term Spread & Butterfly Spread**: Đo lường chênh lệch lãi suất dài hạn - ngắn hạn (10Y-2Y) nhằm dự đoán sức khỏe nền kinh tế. Khi spread âm (đảo ngược), đây có thể là dấu hiệu cảnh báo kinh tế.
3. **Đấu Thầu Sơ Cấp**: Phân tích Tỷ lệ trúng thầu (Success Rate), Tỷ lệ đặt/trúng (Bid-to-Cover Ratio - BCR) và Lãi suất trần (Stop-out Rate) để đo lường thanh khoản của hệ thống ngân hàng.

---

## ⚠️ Các Bẫy Dữ Liệu Đã Được Xử Lý

Dự án này đã vượt qua nhiều hạn chế thực tế khi cào dữ liệu:

1. **HNX Pagination lỗi:** Endpoint trả tối đa 10-50 dòng và sai số trang. Giải pháp: Quét lặp từng ngày (daily loop) thay vì theo tháng.
2. **Ngày tháng kiểu Việt Nam:** Pandas đọc sai `03/09/2026` thành `09/03/2026`. Giải pháp: Luôn parse với chuỗi `format="%d/%m/%Y"`.
3. **Định dạng số thập phân VN:** Dấu chấm làm hàng nghìn, dấu phẩy làm thập phân (vd: `1.234,5`). Giải pháp: Dùng hàm custom `vn_number()`.
4. **Cache rỗng (Empty records):** Quá trình cào ghi nhận cả những ngày nghỉ lễ để bỏ qua trong lần chạy sau (dùng `checked_dates.parquet`), tiết kiệm hàng ngàn request lặp lại.

---

## ⚖️ Tuyên Bố Miễn Trừ Trách Nhiệm

Dữ liệu được bóc tách tự động từ website của HNX và VBMA. Tác giả dự án không chịu trách nhiệm cho các quyết định đầu tư được đưa ra dựa trên số liệu của phần mềm này. 

Dự án kế thừa và phát triển từ nguồn mở: [KhoaSampleTown/vnbond](https://github.com/KhoaSampleTown/vnbond).
