# 📈 Data Pipeline lợi suất Trái phiếu Chính phủ Việt Nam

**Data Pipeline** thu thập, hợp nhất và kiểm chứng dữ liệu lợi suất Trái phiếu Chính phủ (TPCP) dài hạn — thay thế quy trình **trước đây hoàn toàn thủ công**.  

> 🧭 **Nguyên tắc xuyên suốt**: không bịa đặt hay suy đoán số liệu. Mỗi điểm dữ liệu đều gắn nguồn gốc và mức độ tin cậy cụ thể. Các khoảng trống dữ liệu thực tế (đặc biệt giai đoạn 2012–2019) được giữ nguyên là **trống**, **không nội suy ngầm** để “làm đẹp bảng”. Mọi quyết định kiến trúc dưới đây đều bắt nguồn từ kiểm chứng thực nghiệm trực tiếp trên HNX/VBMA — không phải giả định. Chi tiết đầy đủ có trong [`kien-truc-pipeline-tpcp-dai-han.md`](./kien-truc-pipeline-tpcp-dai-han.md).

---

## 🔬 Kết quả nghiên cứu chính

Trước khi viết bất kỳ dòng code production nào, tất cả nguồn dữ liệu đã được tự tay kiểm chứng độ sâu lịch sử thực tế — không tin vào tài liệu hay README có sẵn. Kết quả:

| Nguồn | Độ sâu THỰC TẾ đã xác nhận | Đặc điểm |
|--------|---------------------------|----------|
| **HNX — Kết quả đấu thầu sơ cấp** | Dữ liệu thực từ ít nhất **2013** (đang chạy backfill để xác định chính xác năm bắt đầu) | Giới hạn cứng khoảng 10 dòng mỗi lần gọi API, **không có phân trang thực sự** — buộc phải gọi theo từng ngày |
| **HNX — Đường cong lợi suất chính thức** | Dữ liệu thực từ khoảng **2012–2014**, thưa và không đều cho đến 2018–2019, dày đặc từ **2020** | Không có mốc cắt theo năm rõ ràng — phụ thuộc vào thanh khoản thị trường thứ cấp từng ngày |
| **VBMA — Hoạt động gần đây** (công khai) | Các bài “Kết quả đấu thầu…” có ngày thực ổn định từ ít nhất **tháng 10/2022** | Chỉ dùng để đối chiếu — **không** phải nguồn chính |
| VBMA CSV thành viên | ❌ Không truy cập được | Không có tài khoản thành viên |
| Trading Economics, vnstock (bản miễn phí), ADB AsianBondsOnline, KBNN | ❌ Không dùng được / chưa kiểm chứng | Xem mục “Việc tiếp theo” bên dưới |

**Lời khẳng định “có dữ liệu từ năm 2009”** trong thư viện mã nguồn mở `vnbond` (được dùng làm nền tảng mạng) **đã được chứng minh là sai** — đó chỉ là dòng docstring chưa qua kiểm chứng, không có test nào trong chính repo đó xác nhận. Thực nghiệm trực tiếp cho thấy **hoàn toàn không có dữ liệu cho năm 2011**.

---

### 🐞 Các lỗi phát hiện trong quá trình nghiên cứu

1. **`vnbond.hnx.auctions()` có giới hạn cứng khoảng 10 dòng mỗi lần gọi, không có phân trang thực sự** — được xác nhận bằng cách gọi riêng cho 12 tháng của năm 2015 (trả về đúng 120 dòng), trong khi gọi một lần cho cả năm chỉ ra 10 dòng; trang thứ 2 luôn trả về 0 dòng ngay cả với một tuần có dữ liệu dày đặc.
2. **Bộ nhớ đệm (cache) tích hợp sẵn của `vnbond.hnx.auctions()` không bao giờ khử trùng lặp** — khoá trùng lặp `["Ngày đấu thầu", "Mã TP"]` không khớp với tên cột thực tế trả về (`"Ngày TCPH"`, `"Mã trái phiếu"`). Nếu dùng cache mặc định, dữ liệu sẽ bị trùng lặp vô hạn qua các lần chạy chồng lấn ngày.
3. **Bẫy phân tích ngày kiểu Mỹ của pandas**: gọi `pd.to_datetime()` mà không kèm `format`/`dayfirst` sẽ đọc nhầm chuỗi “03/09/2026” thành **ngày 9 tháng 3** thay vì **ngày 3 tháng 9** đối với mọi ngày trong tháng ≤ 12.
4. **Phiên đấu thầu thất bại hoặc không có ai đặt thầu dễ bị hiểu nhầm thành lãi suất trúng thầu 0%** — HNX trả về chuỗi rỗng hoặc `"0"` cho hai trường hợp khác nhau này; cần dùng khối lượng trúng thầu thực tế để phân biệt, không bao giờ tin vào cột lãi suất một cách trực tiếp.
5. **VBMA gắn ngày đăng giả (17/06/2021) cho mọi bài đăng trước mốc đó** — dấu vết của việc di chuyển trang web vào ngày đó, **không phải** ngày thực tế của bài viết.

---

## 📦 Đầu ra — Cấu trúc dữ liệu cuối cùng

### Bảng hợp nhất chính: `cache/combined_tpcp_yields.parquet`

**Định dạng “dài” (long)** — mỗi dòng là một điểm dữ liệu duy nhất (ngày × kỳ hạn × loại lãi suất):

| Cột | Ý nghĩa | Ví dụ |
|-----|---------|-------|
| `date` | Ngày tham chiếu | `2026-09-03` |
| `tenor` | Kỳ hạn đã chuẩn hoá | `30Y` |
| `rate_type` | Loại lãi suất | `primary_winning_yield` |
| `value` | Giá trị, đơn vị % | `4.61` |
| `source` | Nguồn gốc dữ liệu | `hnx_auctions` |
| `confidence` | Mức độ tin cậy | `official` |
| `fetched_at` | Thời điểm pipeline lấy dữ liệu | `2026-09-05T16:52:06` |

`rate_type` có thể nhận các giá trị: `spot_continuous` / `par_yield` / `spot_annual` (từ đường cong lợi suất chính thức), `primary_winning_yield` (từ kết quả đấu thầu sơ cấp), hoặc `vbma_*` (đối chiếu, luôn đi kèm `confidence="cross_check"`).

---

### ✅ Ví dụ dữ liệu THỰC — Đấu thầu sơ cấp ngày 03/09/2026

```
Mã trái phiếu   Kỳ hạn   Lãi suất trúng thầu (%)   auction_successful
TD2656067       30Y      4.61                       True
TD2636024       10Y      4.42                       True
TD2641037       15Y      (rỗng – có đặt thầu nhưng không ai trúng)   False
TD2631010       5Y       (rỗng – cùng lý do)        False
TD2629001       3Y       “0” (không ai đặt thầu)    False
```

Sau khi chạy `reconcile.py`, chỉ hai dòng đầu (`auction_successful=True`) được đưa vào bảng hợp nhất, lưu đúng định dạng:  
`(2026-09-03, 30Y, primary_winning_yield, 4.61, hnx_auctions, official, …)`

---

### ✅ Ví dụ dữ liệu THỰC — Đường cong lợi suất ngày 04/09/2020

Bảng HNX thô (đã được tự tay kiểm chứng; script production dùng cùng logic phân tích nhưng chưa gọi mạng lần nào — xem “Trạng thái từng thành phần” bên dưới):

```
Kỳ hạn còn lại   Spot rate liên tục (%)   Par yield (%)   Spot rate năm (%)
3 tháng          0,4183623935               –               0,419238750681283
1 năm            0,6631866335               0,665390585462  0,665390585462
10 năm           2,7624687405               2,70908131888   2,8009786989
20 năm           3,7044858787               3,52489279627   3,7739571532
```

Sau khi phân tích, mỗi kỳ hạn tạo ra một dòng dạng dài cho mỗi cột **không rỗng** — các ô rỗng (ví dụ Par yield cho kỳ hạn 3 tháng) bị **loại bỏ hoàn toàn**, không bao giờ được suy diễn thành 0%.

---

### VBMA (Đối chiếu) — Chưa có ví dụ thực tế

Phần trích xuất bảng có cấu trúc từ các bài viết VBMA (`structured=True`) **chưa từng** chạy trên dữ liệu sống. Có khả năng VBMA chỉ dùng văn xuôi thay vì bảng HTML — nếu vậy, nội dung thô sẽ nằm ở cột `raw_text` và **không** tự động vào bảng hợp nhất. Xem “Trạng thái từng thành phần” bên dưới.

---

## 🧩 Trạng thái từng thành phần

| File | Đã chạy trên dữ liệu sống chưa? | Ghi chú |
|------|----------------------------|---------|
| `hnx_auctions_daily.py` | ✅ Có | Đã xác nhận đúng trên dữ liệu tháng 9/2026; backfill từ 2012 → nay đang / đã chạy |
| `hnx_yield_curve_daily.py` | ❌ Chưa | Logic đã được kiểm tra với dữ liệu mô phỏng đúng cấu trúc thực quan sát được, nhưng script này **chưa từng** gọi mạng thực tế |
| `vbma_activities.py` — danh sách bài | ⚠️ Một phần | Cấu trúc trang đã được xem qua công cụ bên ngoài, **không** phải qua regex trong script này — regex trích link **chưa** được kiểm chứng trên HTML thô thực tế |
| `vbma_activities.py` — bảng trong bài | ❌ Chưa | Ẩn số lớn nhất còn lại — chưa biết VBMA có bảng HTML thực hay chỉ có văn xuôi |
| `reconcile.py` | ⚠️ Một phần | Logic hợp nhất đã test với dữ liệu giả lập đúng schema; chưa từng chạy trên Parquet thực của cả 3 nguồn cùng lúc |
| `backfill.yml` / `daily_update.yml` | ❌ Chưa | Chưa từng thực thi trong GitHub Actions — IP của runner Actions có thể bị HNX xử lý khác với IP Colab đã dùng để kiểm tra |

---

## 🏗️ Kiến trúc và luồng dữ liệu

```
hnx_auctions_daily.py    ─┐
hnx_yield_curve_daily.py  ├──▶ reconcile.py ──▶ combined_tpcp_yields.parquet
vbma_activities.py       ─┘
```

Mỗi script nguồn tự quản lý cache riêng (`cache/<tên>.parquet` cùng một file “đã quét” tương ứng), ghi dữ liệu tăng dần, và chịu đựng được việc bị ngắt giữa chừng. Chi tiết đầy đủ về các lựa chọn kiến trúc và bằng chứng thực nghiệm cho từng quyết định được trình bày trong [`kien-truc-pipeline-tpcp-dai-han.md`](./kien-truc-pipeline-tpcp-dai-han.md).

---

## 🚀 Cài đặt và chạy

```bash
pip install -r requirements.txt

# Backfill lần đầu, theo thứ tự khuyến nghị
python hnx_auctions_daily.py 2012-01-01
python hnx_yield_curve_daily.py 2012-01-01
python vbma_activities.py --max-pages 120
python reconcile.py
```

Backfill từ 2012 → nay ước tính mất **30–90 phút cho mỗi script nguồn**. Bạn có thể dừng (Ctrl+C) và chạy lại cùng lệnh bất kỳ lúc nào — các script sẽ **tiếp tục** từ nơi đã dừng nhờ cơ chế theo dõi ngày đã quét.

---

## ⏰ CI/CD

- `.github/workflows/backfill.yml` — kích hoạt thủ công (một lần)
- `.github/workflows/daily_update.yml` — kích hoạt hàng ngày qua **cron‑job.org** gửi sự kiện `repository_dispatch` (cùng mô hình đã dùng cho `daily-news-pipeline` và `vn-sector-indices-bot`; **không** dùng cron `schedule:` gốc của GitHub vì đã xác nhận không đáng tin cậy trên các repo ít hoạt động).

**Chưa kết nối cron tự động** — cần chạy thủ công `workflow_dispatch` vài lần trước để xác nhận runner Actions **không** bị HNX chặn.

---

## ⚠️ Giới hạn đã biết

- Chưa có ngày nghỉ Tết Âm lịch trong lịch nghỉ (chỉ có các ngày lễ dương lịch cố định) — hệ quả duy nhất là vài request thừa vô hại trong tuần đó mỗi năm.
- `hnx_yield_curve_daily.py`: dữ liệu giai đoạn 2012–2019 **thực sự thưa và không đều** — các phản hồi rỗng trong giai đoạn đó **không phải** là lỗi.
- Nguồn KBNN (`vst.mof.gov.vn`, đơn vị phát hành thực tế, hoàn toàn độc lập với HNX) và ADB AsianBondsOnline đáng được khảo sát thêm — cả hai đều render bằng JavaScript, cần Playwright, nằm **ngoài phạm vi hiện tại**.

---

## 📋 Việc tiếp theo trước khi tin tưởng 100% cho production

Ba công việc cản trở lớn nhất, theo thứ tự ưu tiên:

1. **Xác nhận regex trong `vbma_activities.py`** khớp đúng với HTML thô thực tế của VBMA (chưa từng kiểm tra ngoài một công cụ trung gian).  
2. **Chạy `hnx_yield_curve_daily.py` lần đầu tiên** với gọi mạng thực tế.  
3. **Kích hoạt thủ công `daily_update.yml` qua `workflow_dispatch`** vài lần trước khi kết nối cron‑job.org — để loại trừ khả năng dải IP của GitHub Actions bị HNX xử lý khác với IP Colab.
