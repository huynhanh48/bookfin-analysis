# BookFin FC Data Proxy & Stock Scanner API

Node.js (Express) Proxy Gateway và Động cơ Định Lượng Phân Tích Kỹ Thuật (Stock Scanner) kết nối trực tiếp với **SSI FastConnect Data**.

- **Live Production:** `https://fcdata.bookfin.vn`
- **Realtime WebSocket:** `wss://fcdata.bookfin.vn/ws`
- **Tài Liệu Chi Tiết Đầu Vào / Đầu Ra API:** Xem [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md)
- **Tài Liệu Agent & Mobile Contract:** Xem [AGENTS.md](AGENTS.md)

---

## 1. TÍNH NĂNG CHÍNH

1. **Batch Market Data API (`/api/market/*`)**:
   - Tải dữ liệu nến OHLCV theo lô (`POST /api/market/batch`) theo danh sách `symbols` hoặc `universe` (rổ chỉ số, phân ngành, nhóm vốn hóa, sàn).
   - Tích hợp *In-Flight Request Deduplication* và *In-Memory Caching*, triệt tiêu hoàn toàn Request Storm từ thiết bị di động.
2. **Modular Stock Scanner API (`/api/scanner/*`)**:
   - Quét cổ phiếu theo biểu thức JSON DSL đệ quy (`AND`, `OR`, `NESTED`).
   - Hỗ trợ các chỉ báo chuẩn qua `technicalindicators`: MA (SMA, EMA, WMA), RSI, MACD, ATR, Bollinger Bands, Ichimoku Cloud.
   - Kiểm tra chính xác giao cắt MA Cross (chống False Positive), vị thế Mây Kumo, vùng RSI.
   - Tích hợp thuật toán định lượng độc quyền BookFin: Điểm hấp thụ cung **Supply Absorption**, **BookFin Score (0-100)** và **7 Chu kỳ Stages** (Breakout Ready, Accumulation, Absorption, Early Bottom, Falling Knife, Watch, Normal).
3. **Catalogs & Indicators Metadata (`/api/v1/*`)**:
   - 24 Rổ chỉ số chính thức (`VN30`, `VN100`, `VNFIN`, `VNREAL`, `VNMAT`,...).
   - 24 Phân ngành & 5 Nhóm vốn hóa.
   - Toàn bộ danh mục 486+ cổ phiếu thị trường Việt Nam.
4. **SSI FastConnect Data Proxy & Fan-out Realtime Stream**:
   - Quản lý Access Token an toàn phía Server (tự động refresh mỗi 6h, auto-retry khi gặp 401).
   - WebSocket Fan-out hiệu năng cao với giao thức `subscribe_v1` (hỗ trợ lọc theo mã/rổ hoặc toàn thị trường).

---

## 2. CÀI ĐẶT & CHẠY DỰ ÁN

### 2.1. Cài đặt Dependencies
```bash
npm install
```

### 2.2. Chạy Server
```bash
# Mặc định lắng nghe tại cổng 3020
npm start
```

### 2.3. Chạy Toàn Bộ Test Suite & Benchmark
```bash
# Chạy 4 bộ kiểm thử đơn vị & tích hợp
npm test

# Chạy benchmark hiệu năng (VN30, VN100, 200, 500, 1000 mã)
npm run benchmark
```

---

## 3. TÓM TẮT DANH MỤC API

| Nhóm | Method | Path | Mô Tả |
|:---|:---|:---|:---|
| **Batch Market** | `POST/GET` | `/api/market/batch` | Lấy dữ liệu nến OHLCV theo lô theo symbols hoặc universe |
| | `GET` | `/api/market/history/:symbol` | Lấy lịch sử nến cho 1 mã đơn lẻ |
| **Stock Scanner** | `POST/GET` | `/api/scanner/scan` | Quét cổ phiếu theo JSON DSL (Universe, Logic, Conditions, Sort, Page) |
| | `GET` | `/api/scanner/indicators` | Danh mục toàn bộ indicators & toán tử hỗ trợ |
| | `GET` | `/api/scanner/universes` | Danh mục Universe (Baskets, Sectors, Markets, CapGroups) |
| | `GET` | `/api/scanner/baskets` | Toàn bộ 24 rổ chỉ số |
| | `GET` | `/api/scanner/baskets/:key` | Danh sách mã thuộc 1 rổ cụ thể |
| | `GET` | `/api/scanner/sectors` | 24 Phân ngành cổ phiếu |
| | `GET` | `/api/scanner/groups` | 5 Nhóm vốn hóa |
| | `GET` | `/api/scanner/stock/:symbol` | Phân tích định lượng chi tiết cho 1 mã |
| **Catalogs** | `GET` | `/api/v1/catalogs/master` | Danh mục 486+ cổ phiếu |
| | `GET` | `/api/v1/catalogs/baskets` | Danh sách 24 rổ chỉ số |
| | `GET` | `/api/v1/catalogs/sectors` | Danh sách 24 phân ngành |
| | `GET` | `/api/v1/catalogs/cap-groups` | 5 nhóm vốn hóa |
| **Indicators** | `GET` | `/api/v1/indicators/summary` | Tổng hợp chỉ báo & tín hiệu kỹ thuật 1 mã |
| | `GET` | `/api/v1/indicators/ma` | Chi tiết MA & Golden Cross |
| | `GET` | `/api/v1/indicators/rsi` | Chi tiết RSI14 |
| | `GET` | `/api/v1/indicators/ichimoku` | Chi tiết Ichimoku Cloud |
| | `GET` | `/api/v1/indicators/absorption` | Chi tiết điểm Supply Absorption |
| **SSI Proxy** | `GET` | `/health`, `/metrics` | Trạng thái dịch vụ & WS metrics |
| | `GET` | `/Securities`, `/SecuritiesDetails` | Danh sách chứng khoán từ SSI |
| | `GET` | `/DailyOhlc`, `/IntradayOhlc` | Lịch sử nến từ SSI |
| | `GET` | `/DailyStockPrice`, `/BatchStockPrices` | Giá khớp lệnh từ SSI |
| **Realtime** | `WS` | `/ws` | WebSocket Stream giao thức `subscribe_v1` |

👉 *Xem chi tiết payload request/response của từng API tại [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md).*
