# QUY CHUẨN THỰC THI BACKTEST & QUẢN LÝ RỦI RO ĐỒNG NHẤT (BACKTEST PROTOCOL)

Phiên bản: 1.0.0 | Ngày hiệu lực: 2026-10-31 | Trạng thái: BẮT BUỘC TUÂN THỦ (MANDATORY)

---

## 1. NGUYÊN TẮC BẮT BUỘC VỀ DỮ LIỆU & MÔI TRƯỜNG THỰC THI

1. **Môi trường Thực thi Độc quyền (Compute Node .152):**
   - Mọi tác vụ kiểm định định lượng, quét tham số (grid search), stress-test và backtest **BẮT BUỘC** phải được thực hiện trên **Compute Node X99 (`192.168.1.152`)** (Dual Intel Xeon E5-2680 v4, 56 vCPUs, 30 GB RAM).
   - Dữ liệu nến phục vụ backtest **BẮT BUỘC** là dữ liệu thực tế 100% trích xuất từ terminal MetaTrader 5 sàn Exness (lưu trữ tại `~/fx-data/parquet/` trên `.152`).
   - Tuyệt đối **CẤM** tự ý tạo dữ liệu nến giả lập, nến ngẫu nhiên hoặc chạy backtest ước lượng bằng script rút gọn mà không có xác nhận.

2. **Giao thức Xử lý Sự cố Kết nối:**
   - Trước khi bắt đầu bất kỳ tác vụ backtest nào, agent phải thực hiện kiểm tra ping/SSH tới `dinhtc@192.168.1.152`.
   - Nếu **KHÔNG THỂ TRUY CẬP .152** vì bất kỳ lý do gì (máy trạm tắt nguồn, rớt mạng, lỗi xác thực SSH, phân vùng đĩa bị khóa, v.v.):
     $\implies$ **DỪNG LẠI NGAY LẬP TỨC VÀ BÁO ANH XỬ LÝ.**
     Tuyệt đối không tự ý chuyển sang chạy tạm bợ trên môi trường khác khi chưa có chỉ đạo của Anh.

---

## 2. QUY CHUẨN VỐN KHỞI TẠO CƠ SỞ (BASE BACKTEST CAPITAL)

- **Vốn khởi tạo chuẩn ($V_0$):** Cố định là **`1,000 USD`** (Một ngàn dollar Mỹ) cho mọi bài toán và kịch bản backtest.
- **Khởi điểm Level:** $V_0 = 1,000\$ \iff \text{Level } 0$.
- **Ngân sách rủi ro chuẩn tại Level 0 (3.0%):**
  $$\text{Risk Budget} = 1,000\$ \times 3.0\% = \mathbf{30.00\$\text{ / lệnh}}$$

---

## 3. NGUYÊN TẮC ĐỒNG NHẤT SIZING & LEVEL (TIERED COMPOUND RISK ENGINE)

Hệ thống tuân thủ **LUẬT ĐỒNG NHẤT TUYỆT ĐỐI**: Quy tắc tính khối lượng lot và rủi ro áp dụng khi vào lệnh giữa **BACKTEST** và **LIVE TRADING** phải giống hệt nhau 100%. Không có ngoại lệ.

### A. Công Thức Tính Khối Lượng Lot Động
Khối lượng lệnh được tính toán chính xác dựa trên khoảng cách Stop Loss thực tế của từng setup:
$$\text{Volume (lot)} = \frac{\text{Equity} \times \text{Risk\_Pct}}{\text{SL\_Distance} \times \text{Contract\_Size}}$$

*Quy tắc chuẩn hóa tài sản:*
- **XAUUSD (Spot Gold):** `Contract Size = 100 oz`. Khoảng cách SL tính bằng USD giá Vàng.
- **BTCUSD (Bitcoin):** `Contract Size = 1 BTC`. Khoảng cách SL tính bằng USD giá Bitcoin.
- **Forex (EURUSD, USDJPY):** `Contract Size = 100,000 units`.

### B. Cơ Chế Thăng / Giáng Cấp Level & Lãi Kép (Compounding)
- **Tăng trưởng ($E \ge V_0$):**
  $$L(E) = \left\lfloor \frac{\ln(E / V_0)}{\ln(1.1)} + 10^{-9} \right\rfloor$$
  *Cứ mỗi khi vốn tăng thêm $+10\%$, tài khoản thăng lên 1 Level. Ngân sách rủi ro tăng lũy tiến theo quy mô vốn.*
- **Sụt giảm ($E < V_0$):**
  $$L(E) = -\left\lceil \frac{\ln(E / V_0)}{\ln(0.9)} - 10^{-9} \right\rceil$$

### C. Bậc Rủi Ro & Cơ Chế Phòng Thủ Drawdown (De-escalation)
- **Level $0 \le L \le 17$:** Rủi ro **`3.0%`** mỗi lệnh.
- **Level $18 \le L \le 50$:** Giảm rủi ro về **`2.0%`** (De-escalation khi quy mô vốn đã lớn).
- **Level $51 \le L \le 99$:** Giảm rủi ro về **`1.0%`**.
- **Level $L \ge 100$:** Rủi ro **`0.5%`**.
- **Chế độ Sụt Giảm Bảo Toàn Vốn ($L < 0$):**
  - $-2 \le L < 0$: Giảm rủi ro về **`2.0%`**.
  - $-5 \le L < -2$: Giảm rủi ro về **`1.0%`**.
  - $L < -5$: Chuyển sang chế độ bảo toàn kiệt cùng **`0.5%`** (hoặc lot tối thiểu 0.01).

### D. Các Ràng Buộc Bất Khả Xâm Phạm
1. **`max_active_orders: 3`**: Tối đa 3 vị thế mở đồng thời trên toàn danh mục.
2. **`max_total_open_risk_pct: 9.0%`**: Tổng rủi ro gộp của toàn bộ các lệnh đang mở không bao giờ được vượt quá 9% vốn.
3. **`DRAWDOWN_LIMIT: 10%`**: Chốt chặn bảo vệ đóng băng mở vị thế mới nếu drawdown vượt quá ngưỡng quy định.
4. **CẤM TIỆT:** Cấm chạy backtest với khối lượng cố định 0.01 lot (Canary flat sizing) khi đánh giá hiệu năng chiến lược, vì sẽ làm tê liệt hoàn toàn cỗ máy lãi kép và bóp méo kết quả thực tế.

---

*Tài liệu này là quy chuẩn kiến trúc bắt buộc cho toàn bộ các phiên làm việc của Agent trong hệ thống Forex-AI.*
