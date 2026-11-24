# Stock Screener & Watchlist

## Chiến lược AFL gốc

Nguồn: `docs/loc_dong_tien.afl`

```afl
DK1 = V > 100000;
DK2 = C > 10;
DK3 = V > 0.8 * MA(Ref(V, -1), 10);
DK4 = C > 1.02 * Ref(C, -1);
DK6 = C > 1.02 * MA(C, 5);
Buy = DK1 AND DK2 AND DK3 AND DK4 AND DK6;
```

Chuyển sang Python/FCData:

- FCData trả giá theo VND, nên `C > 10` của AFL tương đương `close > 10.000 VND`.
- `DK3` dùng đúng 10 volume phiên trước, không gồm phiên hiện tại.
- `DK6` dùng MA5 gồm cả close hiện tại.
- SMA được tính bằng TA-Lib thông qua indicator engine, không viết lại công thức.

Mặc định cả 5 điều kiện AFL đều bật. Người dùng có thể đổi ngưỡng và tắt từng điều kiện.

### Bộ lọc Divergence

Ngoài 5 điều kiện AFL, screener có filter phân kỳ tùy chọn (mặc định tắt):

- Hướng: `bullish`, `bearish` hoặc `any`.
- Loại: `regular`, `hidden` hoặc `any`.
- Cửa sổ:
  - `latest`: `days` phiên đóng gần nhất; `days=1` là phiên gần nhất. Cách này
    tránh lỗi cuối tuần/ngày lễ vì không phụ thuộc ngày lịch.
  - `range`: khoảng `date_from` → `date_to` tùy chỉnh.
- Mốc thời gian:
  - `pivot`: ngày đỉnh/đáy thứ hai của phân kỳ (đúng ngày nhìn thấy trên chart).
  - `confirmed`: ngày nến xác nhận tín hiệu.
- Preset riêng **Divergence**: tắt toàn bộ điều kiện AFL, mặc định quét phân
  kỳ **đã xác nhận** trong phiên gần nhất theo mốc `confirmed`; người dùng chỉnh
  số phiên, mốc thời gian hoặc chọn khoảng ngày trong modal.
- Chỉ phát phân kỳ **đã xác nhận** (`divergence_history`), đúng với các đường
  đang vẽ trên chart. Không sinh tín hiệu "candidate" cho nến cuối vì chart
  cũng chưa có đường nối tương ứng.
- Một pivot cần thêm `pivot_right + tolerance_bars` nến mới xác nhận (mặc định
  5 + 3 = 8 nến). Vì vậy:
  - Mốc `confirmed`: tín hiệu xuất hiện đúng ngày xác nhận.
  - Mốc `pivot`: tín hiệu chỉ xuất hiện sau ngày xác nhận, nhưng được lọc theo
    ngày pivot. Với `days` nhỏ hơn 8, kết quả `pivot` thường rỗng vì pivot chưa
    đủ nến để xác nhận.
- Dùng engine divergence hiện có (MACD/RSI + swing pivot), không tính lại bằng LLM.
- Nến đang chạy không được dùng để xác nhận divergence.

## API

```http
GET  /api/v1/screener/filters/default
POST /api/v1/screener/scan
GET  /api/v1/screener/results?profile_id=...
```

`POST /screener/scan` nhận `mode=historical|realtime`, universe `all|symbols`,
danh sách symbol và cấu hình điều kiện. Kết quả gồm giá, % thay đổi, volume,
SMA10 volume, MA5, trạng thái `LIVE|CONFIRMED|STALE`.

## Realtime

Luồng:

```text
FCData ticks / batch quotes
    -> Realtime worker
    -> Redis screener:events:{profile_id}
    -> API ScreenerRealtimeBridge
    -> WebSocket channel screener:{profile_id}
    -> /screener
```

- Tick chỉ đánh dấu symbol dirty; producer đánh giá batch quote cho symbol thay đổi.
- Reconcile định kỳ để bù tick thiếu khi FCData `ssiReady=false`.
- `SCREENER_ENTERED`/`SCREENER_EXITED` chỉ phát khi trạng thái đổi, có cooldown.
- Khi mất dữ liệu, trạng thái chuyển `STALE`; không phát EXITED giả.
- Không tạo container mới: producer chạy trong realtime worker hiện tại.
- `target=all` của FCData đã được thử nhưng hiện chưa xác minh được dữ liệu
  (`ssiReady=false`). Vì vậy mặc định dùng universe/watchlist và REST batch fallback.

## Watchlist

Bảng `watchlists` và `watchlist_items` lưu trong PostgreSQL.

- Owner hiện tại là `local` vì chưa có authentication; repository đã lọc theo
  `owner_id` để sau này thay bằng principal.
- Lần đầu sử dụng tự tạo 10 list `List 1` … `List 10`.
- Default list được rename/clear nhưng không được xoá.
- Một symbol không trùng trong cùng list, nhưng có thể nằm nhiều list.
- Bulk add dùng transaction và bỏ qua symbol đã có.

API:

```http
GET    /api/v1/watchlists
POST   /api/v1/watchlists
PATCH  /api/v1/watchlists/{id}
DELETE /api/v1/watchlists/{id}

GET    /api/v1/watchlists/{id}/items
POST   /api/v1/watchlists/{id}/items
POST   /api/v1/watchlists/{id}/items/bulk
DELETE /api/v1/watchlists/{id}/items/{symbol}
DELETE /api/v1/watchlists/{id}/items
```

## Frontend

Trang `/screener` nằm trong `bookfin-analysis-web`:

- Filter panel: giá, volume, Vol/SMA10, % tăng, % so MA5, toggle từng điều kiện.
- Bảng kết quả: sort, search, chọn từng/all, thêm vào watchlist.
- Click symbol mở chart KLineCharts Pro tại `/?symbol=...&timeframe=1D`.
- Watchlist sidebar: rename, clear, delete list người dùng, thêm/xoá symbol.
- Realtime ON/OFF; khi WebSocket mất kết nối, UI giữ dữ liệu và hiển thị trạng thái.
