# Contract BookFin FCData Gateway (REST + WebSocket)

**Nguồn:** repo `huynhanh48/bookfin`, branch `release-v4.1.0`, thư mục `bookfin-data/` (clone đọc-only tại `/tmp/bookfin-ref`).
**Base URL:** `https://fcdata.bookfin.vn` (dev: `http://localhost:3020`, cổng `PORT`, `main.js:17`) · **WS:** `wss://fcdata.bookfin.vn/ws`
**Ngày kiểm chứng live:** 2026-11-18 (giờ VN). Mọi số liệu "live" dưới đây là response thật lấy từ production trong ngày đó.

Quy ước bằng chứng: `file:dòng` = dòng trong branch `release-v4.1.0`. `[live]` = đã gọi thật và quan sát. `[code]` = chỉ suy ra từ code, chưa kiểm chứng bằng gọi mạng.

---

## 0. Quy ước chung (áp dụng cho mọi endpoint)

| Hạng mục | Giá trị thật | Bằng chứng |
|---|---|---|
| Auth | Gateway **không** yêu cầu token/API key từ client. ConsumerId/Secret chỉ nằm server-side và được dùng để lấy SSI `accessToken` | `main.js:601-628`, `config.js:1-8` `[live: gọi không kèm Authorization vẫn 200]` |
| CORS | `Access-Control-Allow-Origin: *`, header cho phép `Origin, X-Requested-With, Content-Type, Accept, Authorization`; mọi `OPTIONS` trả **200** | `main.js:312-317` `[live]` |
| Body parser | `express.json()` + `express.urlencoded({extended:true})` | `main.js:309-310` |
| Cache nội bộ | In-memory, TTL mặc định 15s; evict khi >2000 entry; key = URL upstream | `main.js:194-213` |
| TTL riêng | `/Securities` 60s, `/SecuritiesDetails` 60s, `/IndexComponents` 30s, `/IndexList` 60s, `/DailyOhlc` 30s, `/IntradayOhlc` 10s, `/DailyIndex` 15s, `/DailyStockPrice` 15s, `/BatchStockPrices` 15s | `main.js:349,354,359,364,369,374,379,384,421` |
| Prefix query lên SSI | Mọi tham số client gửi được đổi thành `lookupRequest.<tên>=<value>`; bỏ qua giá trị `undefined/null/""` | `main.js:276-279`, `fcdata.client.js:49-52` |
| Chuẩn hoá ngày | Client gửi `DD/MM/YYYY`; server tính giờ VN (UTC+7). Trước 08:30 sáng → ngày an toàn lớn nhất là **hôm qua**; `toDate` tương lai bị kẹp về ngày an toàn; với các endpoint **không phải** `/DailyOhlc`, khoảng > 28 ngày bị kẹp `fromDate = toDate - 25`; nếu `fromDate > toDate` → `fromDate = toDate - 5` | `main.js:215-272`, `date.util.js:12-65` `[live]` |
| Timeout upstream | proxy REST 15s (`/BatchStockPrices` 8s/mã); fcdata.client 10s | `main.js:288,418`, `fcdata.client.js:47` |
| Token SSI | Hết hạn ~8h; refresh chủ động mỗi **6h**; gặp 401 (HTTP hoặc `status:401/'Unauthorized'` trong body) → refresh + retry **1 lần** | `main.js:639-652,767-811`, `fcdata.client.js:59-68` |
| Phân trang | `pageIndex` (bắt đầu từ **1**) + `pageSize`. **Không** có `offset`/`limit` kiểu REST. SSI chặn `pageSize` không thuộc {10,20,50,100,1000} ở: `/Securities`, `/SecuritiesDetails`, `/IndexComponents`, `/IndexList`, `/DailyIndex`; các endpoint `/DailyOhlc`, `/IntradayOhlc`, `/DailyStockPrice` chấp nhận số nguyên dương bất kỳ | `main.js:347-385` `[live]` |
| `totalRecord` | Tổng số bản ghi khớp điều kiện (không phải số bản ghi trong page) | `[live: IndexList pageSize=10 → 10 rows, totalRecord=28]` |
| Tham số lạ | Query lạ vẫn được forward lên SSI dưới dạng `lookupRequest.<tên>` (SSI tự bỏ qua) | `main.js:349` — live: tham số `page=2` bị bỏ qua, chỉ `pageIndex` có tác dụng |

### Shape lỗi thật (rất quan trọng)

1. **Lỗi từ SSI vẫn trả HTTP 200**, body: `{"data": null, "message": "<lý do>", "status": "Error", "totalRecord": 0}` `[live]`
   - Ví dụ thật: `pageSize=2` → `{"data":null,"message":"Size of a page must 10, 20, 50, 100 or 1000, default 10","status":"Error","totalRecord":0}`
   - `pageIndex=0` → `status:"Error"`, message `Number or page must start from 1 , default 1`
   - Không có dữ liệu → `status: "NoDataFound"`, message `There is no data` `[live: /Securities?market=HNX]`
   - Sai mã thị trường → `status:"Error"`, `Market code is not valid, market : HOSE, HNX, HNXBOND, UPCOM ,DER or (EMPTY)`
2. **Lỗi mạng/HTTP từ SSI** → HTTP = `error.response.status` hoặc **502**; body = body lỗi của SSI, nếu không có thì `{"status":"Error","message":"<err>","totalRecord":0,"data":null}` `[code: main.js:296-306]`
3. **Route không tồn tại** → 404 HTML của Express: `<pre>Cannot GET /nope</pre>` (content-type `text/html`) `[live]`
4. **`/api/v1/catalogs/security/:symbol` không có mã** → 404 JSON `{"status":"Error","message":"Security X not found in catalog","data":null}` `[live]`
5. **`/api/market/history/:symbol` lỗi** → 400 `{"status":"Error","message":...}` `[code: market.controller.js:30]`
6. **`/api/market/batch` lỗi nội bộ** → 500 `{"status":"Error","message":...,"data":{}}` `[code: market.controller.js:16-20]`
7. **`/BatchStockPrices` lỗi** → 500 `{"status":"Error","message":...,"data":[]}` `[code: main.js:443]`

---

## A. Danh sách endpoint REST

Tổng cộng **39 route path**: 11 proxy SSI + 28 API nội bộ. Trong đó **25 path đã gọi thật** `[live]` (11 proxy, 2 `/api/market/*`, 6 `/api/v1/catalogs/*`, 5 `/api/scanner/*`, 1 `/api/v1/screener/*`). 14 path chỉ đọc từ code (scanner/scan, baskets/:key, stock/:symbol, trend-score/*, money-flow/*, screener/filter, screener/signals, indicators/*).

### A.1 Proxy SSI (pass-through, tất cả `GET`)

| Method | Path | Query (tên thật · default) | TTL cache | Nguồn SSI |
|---|---|---|---|---|
| GET | `/health` | – | – | – |
| GET | `/metrics` | – | – | – |
| GET | `/Securities` | `market`=HOSE, `pageIndex`=1, `pageSize`=100 | 60s | `api/v2/Market/Securities` |
| GET | `/SecuritiesDetails` | `market`=**DER**, `symbol`="", `pageIndex`=1, `pageSize`=1000 | 60s | `api/v2/Market/SecuritiesDetails` |
| GET | `/IndexComponents` | `indexCode`="", `pageIndex`=1, `pageSize`=1000 | 30s | `api/v2/Market/IndexComponents` |
| GET | `/IndexList` | `exchange`=HOSE, `pageIndex`=1, `pageSize`=1000 | 60s | `api/v2/Market/IndexList` |
| GET | `/DailyOhlc` | `symbol`=VN30F2112, `fromDate`="", `toDate`="", `pageIndex`=1, `pageSize`=1000, `ascending`=true | 30s | `api/v2/Market/DailyOhlc` |
| GET | `/IntradayOhlc` | `symbol`=VN30F1M, `fromDate`="", `toDate`="", `pageIndex`=1, `pageSize`=1000, `ascending`=false | 10s | `api/v2/Market/IntradayOhlc` |
| GET | `/DailyIndex` | `indexId`=VNINDEX, `fromDate`="", `toDate`="", `pageIndex`=1, `pageSize`=1000, `ascending`=true | 15s | `api/v2/Market/DailyIndex` |
| GET | `/DailyStockPrice` | `symbol`=FPT, `market`="", `fromDate`="", `toDate`="", `pageIndex`=1, `pageSize`=1000 | 15s | `api/v2/Market/DailyStockPrice` |
| GET | `/BatchStockPrices` | `symbols` (CSV) **hoặc** `req_data` (CSV), `market`=HOSE, `fromDate`, `toDate` | 15s/mã | `api/v2/Market/DailyStockPrice` |

Bằng chứng defaults: `main.js:347-385` (proxy), `main.js:389-445` (batch stock prices), tên endpoint: `ssi-fcdata/index.js:9-20`.

`/health` `[live]`:
```json
{"ok":true,"service":"bookfin-fcdata","ssiStreamReady":false,"hasBearerToken":true,
 "wsPath":"/ws","protocol":"subscribe_v1","wsClients":0,"subscriptionsAll":0,
 "subscriptionsSelective":0,"inboundTicks":0,"outboundTicks":117,"droppedTicks":0,
 "subscribeCommands":7,"memory":{"rssMb":228,"heapUsedMb":147,"heapTotalMb":154},"ts":1794994164219}
```
`/metrics` = đúng object metrics ở trên, **không** có `ok/service/ssiStreamReady/hasBearerToken/wsPath/protocol` (`main.js:154-176,343-345`) `[live]`.

### A.2 API nội bộ BookFin

| Method | Path | Query/Body |
|---|---|---|
| POST/GET | `/api/market/batch` | body JSON (POST) hoặc query (GET): `symbols` (array hoặc CSV), `universe` (object), `timeframe`, `limit` (hoặc `lookback`) |
| GET | `/api/market/history/:symbol` | `limit`, `timeframe` |
| POST/GET | `/api/scanner/scan` | JSON DSL: `universe`, `logic`, `conditions`, `sort`, `pagination` |
| GET | `/api/scanner/indicators`, `/universes`, `/baskets`, `/baskets/:key`, `/sectors`, `/groups`, `/stock/:symbol`, `/trend-score/:symbol`, `/trend-score/scan`, `/money-flow/:symbol`, `/money-flow/scan` | – |
| POST/GET | `/api/v1/screener/filter` | body JSON / query |
| GET | `/api/v1/screener/criteria`, `/signals` | – |
| GET | `/api/v1/catalogs/baskets`, `/baskets/:basketKey`, `/sectors`, `/cap-groups`, `/master`, `/security/:symbol` | `master` nhận `market`, `sector`, `capGroup` |
| GET | `/api/v1/indicators/summary`, `/ma`, `/rsi`, `/ichimoku`, `/absorption` | `symbol` (bắt buộc), `rsi` thêm `period`=14 |

Bằng chứng mount: `main.js:320-328`; routes: `market.routes.js:11-15`, `scanner.routes.js:11-33`, `screenerRoutes.js:32-86`, `catalogRoutes.js:14-112`, `indicatorRoutes.js:36-170`.
`/api/v1/catalogs/sectors` trả `totalSectors: 25` (gồm cả mục `"Tất cả"`), không phải 24 `[live]`.

---

## B. Spec chi tiết các endpoint được yêu cầu

Tất cả response dưới đây là **tên field thật** quan sát được từ production (PascalCase cho proxy SSI, camelCase cho API nội bộ). Giá trị số của SSI trả về dạng **string** (ví dụ `"Open":"19250"`).

### B.1 `GET /health`, `GET /metrics`
Xem mục A.1. `[live]`

### B.2 `GET /Securities`
Query: `market` (HOSE|HNX|UPCOM|DER|HNXBOND, rỗng = tất cả), `pageIndex` (≥1), `pageSize` ∈ {10,20,50,100,1000}.
`[live] /Securities?market=HOSE&pageIndex=1&pageSize=10`:
```json
{"data":[{"Market":"HOSE","Symbol":"CLPB2602","StockName":"Chứng quyền LPB/VPBANKS/C/EU/8M/CASH/25-02","StockEnName":null}],
 "message":"Success","status":"Success","totalRecord":6}
```
Field mỗi row: `Market`, `Symbol`, `StockName`, `StockEnName`.

### B.3 `GET /SecuritiesDetails`
Default `market=DER` → nếu chỉ truyền `symbol` mà không truyền `market`, kết quả thường là `NoDataFound` `[live: ?symbol=SSI → status NoDataFound]`.
`[live] ?market=HOSE&symbol=SSI&pageSize=10`:
```json
{"data":[{"RType":"y","ReportDate":"26/08/2026","TotalNoSym":"1","RepeatedInfo":[
  {"Isin":null,"Symbol":"SSI","SymbolName":"CTCP CHUNG KHOAN SSI","SymbolEngName":"SSI Securities Corporation",
   "SecType":"S","MarketId":"HOSE","Exchange":"HOSE","Issuer":null,"LotSize":"100","IssueDate":"","MaturityDate":"",
   "FirstTradingDate":"","LastTradingDate":"","ContractMultiplier":"0","SettlMethod":"C","Underlying":null,
   "PutOrCall":null,"ExercisePrice":"0","ExerciseStyle":"","ExcerciseRatio":"0","ListedShare":"2075914794.0",
   "TickPrice1":"1","TickIncrement1":"10","TickPrice2":"10000","TickIncrement2":"50","TickPrice3":"50000",
   "TickIncrement3":"100","TickPrice4":null,"TickIncrement4":null}]}],
 "message":"Success","status":"Success","totalRecord":1}
```
Lưu ý: tên field là `Isin`, `ExcerciseRatio` (lỗi chính tả có thật trong API SSI), `SecType` trả `"S"`.

### B.4 `GET /DailyOhlc`
Query: `symbol`, `fromDate`, `toDate` (DD/MM/YYYY), `pageIndex`, `pageSize`, `ascending`.
`[live] ?symbol=SSI&fromDate=01/11/2026&toDate=17/11/2026&pageIndex=1&pageSize=10&ascending=true`:
```json
{"data":[{"Symbol":"SSI","Market":"HOSE","TradingDate":"02/11/2026","Time":null,
          "Open":"19250","High":"19350","Low":"18500","Close":"18850",
          "Volume":"27277400","Value":"516265055000"}],
 "message":"Success","status":"Success","totalRecord":12}
```
Thứ tự phụ thuộc `ascending` (`true` = cũ→mới) `[live]`. Endpoint này **không** bị kẹp 28 ngày (`isDailyOhlc=true`, `main.js:369`).

### B.5 `GET /IntradayOhlc`
`[live] ?symbol=SSI&fromDate=17/11/2026&toDate=17/11/2026&pageIndex=1&pageSize=10&ascending=false`:
```json
{"data":[{"Symbol":"SSI","Value":"19850","TradingDate":"17/11/2026","Time":"14:45:00",
          "Open":"19850","High":"19850","Low":"19850","Close":"19850","Volume":"1426800"}],
 "message":"Success","status":"Success","totalRecord":226}
```
`Time` là `HH:mm:ss`. Có `Value` nhưng **không** có `Market`.

### B.6 `GET /DailyStockPrice`
`[live] ?symbol=SSI&market=HOSE&fromDate=17/11/2026&toDate=17/11/2026&pageIndex=1&pageSize=10` — row có 31 field:
`TradingDate, PriceChange, PerPriceChange, CeilingPrice, FloorPrice, RefPrice, OpenPrice, HighestPrice, LowestPrice, ClosePrice, AveragePrice, ClosePriceAdjusted, TotalMatchVol, TotalMatchVal, TotalDealVal, TotalDealVol, ForeignBuyVolTotal, ForeignCurrentRoom, ForeignSellVolTotal, ForeignBuyValTotal, ForeignSellValTotal, TotalBuyTrade, TotalBuyTradeVol, TotalSellTrade, TotalSellTradeVol, NetBuySellVol, NetBuySellVal, TotalTradedVol, TotalTradedValue, Symbol, Time`.
Ví dụ: `{"TradingDate":"17/11/2026","PriceChange":"0","PerPriceChange":"0","CeilingPrice":"21200","FloorPrice":"18500","RefPrice":"19850","OpenPrice":"19750","HighestPrice":"20100","LowestPrice":"19650","ClosePrice":"19850","AveragePrice":"20100","ClosePriceAdjusted":"19850","TotalMatchVol":"15271200","TotalMatchVal":"303410450000",...,"Symbol":"SSI","Time":null}`.
Gọi không có `symbol` vẫn chạy (trả về nhiều mã): `?market=HOSE&pageSize=1000` → 1000 rows, `totalRecord=21931` `[live]`.

### B.7 `GET /BatchStockPrices`
Query: `symbols` (CSV, hoặc alias `req_data`), `market` (default HOSE), `fromDate`, `toDate`.
Hành vi thật `[live]`:
- **Bắt buộc có `fromDate` + `toDate`**: không truyền → `{"status":200,"message":"Success","totalRecord":0,"data":[]}` (không lỗi, chỉ rỗng) vì upstream trả lỗi và code bỏ qua (`main.js:420-426`).
- `market` mặc định HOSE → mã HNX sẽ ra rỗng.
- `symbols` rỗng/không truyền → `{"status":200,"message":"Success","data":[]}` (**không có** `totalRecord`).
- Mỗi mã: lấy bản ghi **đầu tiên** của `pageSize=1` (`main.js:409-433`), tải song song tối đa **6 mã/lô**, cache 15s.
- Thành công: `{"status":200,"message":"Success","totalRecord":3,"data":[<row DailyStockPrice>, ...]}`; `status` ở đây là **số 200**, khác `"Success"` của các proxy khác.

### B.8 `GET|POST /api/market/batch`
Params: `symbols` (array | CSV) **hoặc** `universe`; `timeframe` (default `1D`); `limit` (alias `lookback`, default 250, **kẹp 1..1000**) — `market.service.js:54`.
Với POST: `{...req.body, ...req.query}` → **query ghi đè body** `[live]`.
`universe.type` nhận: `BASKET` (ưu tiên gọi SSI live `/IndexComponents`, cache 1h, fallback JSON tĩnh), `SECTOR`, `GROUP`/`CAP_GROUP`, `EXCHANGE`/`MARKET`, `SYMBOL_LIST`/`SYMBOLS`; không có `universe` → dùng toàn bộ master catalog (`universe.resolver.js:217-297`).
Cảnh báo vận hành: gọi không tham số = toàn bộ ~618 mã → **timeout** `[live: read timeout sau 40s]`.

Response `[live] /api/market/batch?symbols=SSI&limit=1`:
```json
{"status":200,"message":"Success","timeframe":"1D","total":1,
 "data":{"SSI":{"symbol":"SSI","companyName":"Công ty Cổ phần Chứng khoán SSI","exchange":"HOSE","sector":"Chứng khoán",
   "price":20400,"refPrice":20400,"ceilingPrice":0,"floorPrice":0,"change":0,"changePercent":0,
   "volume":47036100,"value":940199515000,
   "foreign":{"buyVal":0,"sellVal":0,"netVal":0,"buyVol":0,"sellVol":0,"netVol":0,"room":0},
   "candlesCount":1,
   "candles":[{"time":"04/11/2026","open":19250,"high":20450,"low":19200,"close":20400,
               "volume":47036100,"value":940199515000}]}},
 "metrics":{"requestedSymbols":1,"returnedSymbols":1,"cacheHits":0,"cacheMisses":1,"upstreamCalls":1,"durationMs":5993}}
```
Quan sát thật: `ceilingPrice`/`floorPrice` = 0 và toàn bộ `foreign` = 0 trong mọi lần gọi thử; `price`/`refPrice`/`volume`/`value` khi đó lấy từ nến (code: `market.service.js:74-91`). Nguyên nhân là `quoteProvider.getQuotesForSymbols(...)` bị `.catch(() => new Map())` nuốt lỗi (`market.service.js:63`) — **chưa xác định được vì sao upstream quote rỗng**, xem mục E.
Mã không có dữ liệu → `total:0`, `data:{}` (vẫn HTTP 200, metrics `requestedSymbols:0` nếu universe rỗng) `[live]`.

### B.9 `GET /api/market/history/:symbol`
Query: `limit` (default 250 — **không kẹp trần**, `market.service.js:136`), `timeframe` (default `1D`).
Response `[live] ?limit=3`:
```json
{"status":200,"message":"Success",
 "data":{"symbol":"SSI","companyName":"Công ty Cổ phần Chứng khoán SSI","exchange":"HOSE","sector":"Chứng khoán",
   "timeframe":"1D","candlesCount":3,
   "candles":[{"time":"02/11/2026","dateObj":"2026-11-02T12:00:00.000Z","open":19250,"high":19350,"low":18500,
               "close":18850,"volume":27277400,"value":516265055000}]}}
```
Mã sai → HTTP 200 với `candlesCount:0`, `candles:[]`, `exchange:"HOSE"`, `sector:""` `[live]`.
`limit` lớn (2000) chạy được và trả đủ 2000 nến `[live]`.
**Bẫy dữ liệu:** nến được lấy từ `GET_DAILY_OHLC` với cửa sổ mặc định `max(380, lookback*1.6)` ngày và `pageSize = max(lookback,250)`, sau đó `slice(-lookback)` (`ohlc.provider.js:143-181`). Nếu cửa sổ dài hơn pageSize, SSI chỉ trả **page 1** → `limit` nhỏ sẽ cắt từ *đầu page* chứ không phải nến mới nhất. `[live: limit=3 → 02-04/11/2026 trong khi dữ liệu mới nhất là 18/11/2026]`

### B.10 `GET /api/v1/catalogs/master`
Query lọc: `market`, `sector`, `capGroup` (bỏ qua khi giá trị là `Tất cả` / `all`).
`[live]`:
```json
{"status":200,"message":"Success","totalRecord":618,
 "data":[{"symbol":"CEO","name":"Công ty Cổ phần Tập đoàn C.E.O","market":"HNX","sector":"Bất động sản",
          "capGroup":"small_cap","isVn30":false,"isHnx30":true}]}
```
Field mỗi row: `symbol`, `name`, `market`, `sector`, `capGroup`, `isVn30`, `isHnx30`. `[live: ?market=HNX → 36; ?sector=Thép → 13]`

### B.11 Các endpoint API nội bộ khác (đã kiểm tra tồn tại `[live]`)
`/api/v1/catalogs/baskets` → `{status,message,totalBaskets:24,data}`; `/baskets/VN30` → `{...,basket:"VN30",totalSymbols:30,data:[symbols]}`; `/sectors` → `{...,totalSectors:25,data:[tên ngành]}`; `/cap-groups` → `{...,data:[{id,name,desc}]}`; `/security/FPT` → `{...,data:{symbol,name,market,sector,capGroup,isVn30,isHnx30}}`.
`/api/scanner/{indicators,universes,baskets,sectors,groups}` và `/api/v1/screener/criteria` đều trả `{status,message,data}` (200) `[live]`.

---

## C. WebSocket `wss://fcdata.bookfin.vn/ws`

Tất cả mục dưới đây **đã kiểm chứng live** ngày 2026-11-18, trừ khi ghi `[code]`.

### C.1 Handshake
- Path phải **chính xác** `/ws`; path khác → handshake **HTTP 404** (`main.js:456-462`) `[live: /ws-bad → 404 từ nginx]`.
- Không cần auth/header gì thêm. Frame tối đa 1 MiB (`main.js:449-454`).
- Ngay sau khi kết nối, server gửi:
```json
{"type":"welcome","service":"bookfin-fcdata","ssiReady":false,"protocol":"subscribe_v1","ts":1794994032788}
```
Chú ý tên field là `ssiReady` (không phải `ssiStreamReady`), và **không có** `type:"hello"`.

### C.2 Message client → server (chỉ nhận **JSON text**)
| Gửi | Server trả |
|---|---|
| `{"action":"ping"}` | `{"type":"pong","ts":<ms>}` |
| `{"action":"subscribe","target":"all"}` | `{"type":"subscribed","target":"all","count":"all"}` |
| `{"action":"subscribe","target":"symbol","codes":["FPT","HPG"]}` | `{"type":"subscribed","target":"symbol","count":2,"symbols":["FPT","HPG"]}` |
| `{"action":"subscribe","target":"index","codes":["VN30","VNINDEX"]}` | `{"type":"subscribed","target":"index","count":2,"indices":["VN30","VNINDEX"]}` |
| `{"action":"subscribe","target":"index"}` (thiếu `codes`) | `{"type":"subscribed","target":"index","count":0,"indices":[]}` |
| `{"action":"subscribe","target":"symbol","codes":["fpt"," hpg "]}` | `{"type":"subscribed","target":"symbol","count":2,"symbols":["FPT","HPG"]}` (trim + uppercase) |
| > 200 codes | `{"type":"error","code":"TOO_MANY_CODES","message":"Max 200 codes per subscribe"}` |
| Gửi > 30 subscribe trong 10s | `{"type":"error","code":"RATE_LIMITED","message":"Too many subscribe requests"}` |
| Payload > 10000 byte | server đóng connection (close code 1009, lý do `Message too large`) |
| Message không phải JSON / frame binary | **im lặng, bỏ qua**, connection vẫn sống |
| `{"action":"subscribe"}` thiếu `target` (hoặc `target` lạ) | **im lặng, không ack** — nhưng code đã set `all=false` → connection ngừng nhận dữ liệu |
| `{"type":"subscribe","symbols":[...]}` (kiểu tài liệu) | **im lặng, không có tác dụng** |
| `{"action":"unsubscribe"...}`, `{"action":"resubscribe"...}` | **không được cài đặt**, im lặng |

Giới hạn & hằng số thật (`main.js:20-25,477-557`):
`MAX_CONTROL_BYTES = 10000`, `MAX_CODES_PER_SUB = 200`, `SUBSCRIBE_RATE_WINDOW_MS = 10000`, `SUBSCRIBE_RATE_MAX = 30`, `IDLE_DISCONNECT_MS = 90000`.
`subscribe` là **thay thế** (không cộng dồn): `target:"symbol"` thay toàn bộ set symbol và tắt `all`; `target:"all"` xoá cả set index/symbol.
Mặc định khi mới kết nối: `all = true` → client nhận **mọi** tick cho tới khi gửi `subscribe` (`main.js:55-63`).

### C.3 Ping/pong & idle
- Server **không** chủ động ping. Mọi message từ client đều cập nhật `lastActivity`.
- Không có message nào trong **90s** → server đóng `code 1000, reason "Idle timeout"` (quét mỗi 30s) `[live: bị đóng sau ~94.4s]`.
- Vì vậy client phải tự gửi `{"action":"ping"}` định kỳ (khuyến nghị < 60s).

### C.4 Dữ liệu realtime (data plane)
- Gateway forward **nguyên văn object SSI SignalR**, **không bọc envelope** (`main.js:123` `sendJson(connection, raw)`). Không có `{"type":"quote","data":...}`.
- Dạng thật (theo SSI spec, `docs/SSI_FastConnectData_Specs_v2.2.pdf` §3.2-3.4): `{"DataType":"X-TRADE"|"X-QUOTE"|"MI"|"F"|"R"|"B","Content":"<JSON string>"}` — `Content` là **chuỗi JSON** (code chấp nhận cả string lẫn object).
- Field trong `Content` khi là quote/trade: `RType, TradingDate, Time, ISIN, Symbol, Ceiling, Floor, RefPrice, Open, Close, High, Low, Avg|AvgPrice, PriorVal, LastPrice, Change, RatioChange, EstMatchedPrice, LastVol, TotalVal, TotalVol, BidPrice1..10, BidVol1..10, AskPrice1..10, AskVol1..10, MarketId, Exchange, TradingSession, TradingStatus`.
- Khi là index (`MI`): `IndexId|IndexID, IndexValEst, IndexValue, PriorIndexValue, TradingDate, Time, TotalTrade, TotalQtty, TotalValue, IndexName, Advances, NoChanges, Declines, Ceilings, Floors, Change, RatioChange, TotalQttyPt, TotalValuePt, TotalQttyOd/TotalValueOd, AllQty, AllValue, IndexType, TradingSession, MarketId, RType`.
- Cách gateway lọc (`main.js:86-144`): lấy symbol từ `Content.Symbol|symbol` hoặc `raw.Symbol|Code`; lấy index id từ `Content.IndexId|IndexCode` hoặc `raw.IndexId`; coi là index nếu có indexId **hoặc** `DataType==='MI'` **hoặc** `Channel` bắt đầu `MI:`.
- Message do server tự sinh: `{"type":"ssi","status":"connected"|"reconnected"|"disconnected"|"token_refreshed"|"stall_detected","ts":<ms>}` (+ `silentMs`, `attempt` cho `stall_detected`) — `main.js:626,692,702-706,710-714,730-736` `[code]`.
- Nguồn tick bên trong: gateway chỉ subscribe **2 kênh** SSI: `X-TRADE:ALL` và `MI:ALL` (`main.js:656-659`).
- Watchdog stream: im lặng > 2 phút → re-subscribe, sau 3 lần → `process.exit(1)` (`main.js:717-751`) `[code]`.

### C.5 Cách tự kiểm chứng
```bash
curl -s https://fcdata.bookfin.vn/health
curl -s "https://fcdata.bookfin.vn/DailyOhlc?symbol=SSI&fromDate=10/11/2026&toDate=17/11/2026&pageIndex=1&pageSize=10&ascending=true"
# WS (python): pip install websocket-client
# ws=websocket.create_connection("wss://fcdata.bookfin.vn/ws"); print(ws.recv())
# ws.send('{"action":"subscribe","target":"symbol","codes":["FPT"]}'); print(ws.recv())
```

---

## D. Tài liệu nói gì vs code/live làm gì

| # | Chủ đề | Tài liệu nói | Thực tế (code + live) | Bằng chứng |
|---|---|---|---|---|
| 1 | Subscribe WS | `{"type":"subscribe","indices":[...],"symbols":[...],"reqId":"..."}` | Chỉ có `{"action":"subscribe","target":"all"|"index"|"symbol","codes":[...]}`. Kiểu tài liệu bị **bỏ qua im lặng** | `AGENTS.md:101-106` vs `main.js:490-556` `[live]` |
| 2 | Ack WS | `{"type":"subscribed","reqId","ok":true,"all","indices","symbols","ts"}` | `{"type":"subscribed","target":"symbol","count":2,"symbols":[...]}` — không có `reqId/ok/all/ts` | `AGENTS.md:111-113` vs `main.js:538-554` `[live]` |
| 3 | Message chào | `{"type":"hello","channel",...,"ssiStreamReady":bool,...}` | `{"type":"welcome","service","ssiReady":bool,"protocol":"subscribe_v1","ts"}` | `AGENTS.md:118` vs `main.js:469-475` `[live]` |
| 4 | Tick WS | `{"type":"quote","data":{...}}` | Forward **nguyên văn** object SSI (`DataType` + `Content`), không envelope | `AGENTS.md:120`, `API_DOCUMENTATION.md:454-470` vs `main.js:123` |
| 5 | Ping | "Client gửi `ping` (text) → pong" | Chỉ nhận JSON `{"action":"ping"}`; text `ping` không phải JSON → bỏ qua | `AGENTS.md:121` vs `main.js:490-493` `[live]` |
| 6 | `unsubscribe`/`resubscribe` | Có mô tả kèm ack | Không tồn tại trong code; gửi vào không phản hồi | `AGENTS.md:104-105,112` vs `main.js:477-557` `[live]` |
| 7 | Kênh SSI nội bộ | `X-QUOTE:ALL` + `X-TRADE:ALL` + `MI:ALL` | Chỉ `X-TRADE:ALL` + `MI:ALL` | `AGENTS.md:88`, `ssi-fcdata/README.md:99` vs `main.js:656-659` |
| 8 | `/api/market/batch` meta | `"meta":{totalSymbols,successCount,cacheHits,upstreamCalls,executionTimeMs}` | `"metrics":{requestedSymbols,returnedSymbols,cacheHits,cacheMisses,upstreamCalls,durationMs}` | `API_DOCUMENTATION.md:104-110` vs `market.service.js:120-127` `[live]` |
| 9 | `/api/market/batch` field | Không có `refPrice`,`ceilingPrice`,`floorPrice`,`foreign` | Có đủ 4 field này (thực tế ceiling/floor/foreign = 0) | `API_DOCUMENTATION.md:80-101` vs `market.service.js:84-100` `[live]` |
| 10 | `/api/market/history` shape | Phẳng: `{status,message,symbol,timeframe,total,data:[candles]}` | Lồng: `{status,message,data:{symbol,companyName,exchange,sector,timeframe,candlesCount,candles[]}}`; mỗi nến có thêm `dateObj` (ISO) | `API_DOCUMENTATION.md:126-143` vs `market.controller.js:28`, `market.service.js:141-149` `[live]` |
| 11 | `history` limit | "mặc định 250, tối đa 500" | Không có trần trong code; `limit=2000` trả 2000 nến | `API_DOCUMENTATION.md:121` vs `market.service.js:136` `[live]` |
| 12 | `batch` limit | Không nêu trần | Kẹp `1..1000` | `market.service.js:54` |
| 13 | `/Securities` tham số | `?market=HOSE&page=1&pageSize=100` | `page` bị bỏ qua; chỉ `pageIndex` có tác dụng | `API_DOCUMENTATION.md:426` vs `main.js:347-350` `[live]` |
| 14 | Bảng mã lỗi | HTTP 400/401/404/500 cho lỗi tham số/token/mã | Lỗi SSI trả **HTTP 200** với `status:"Error"` hoặc `"NoDataFound"`; chỉ route sai mới 404 (HTML) | `API_DOCUMENTATION.md:476-482` vs `main.js:294,305` `[live]` |
| 15 | `pageSize` | Không nêu ràng buộc | Bắt buộc ∈ {10,20,50,100,1000} ở 5 endpoint; các endpoint OHLC/giá nhận số bất kỳ | `[live]` |
| 16 | Master catalog | "486+ cổ phiếu" | `totalRecord = 618` | `AGENTS.md:76`, `API_DOCUMENTATION.md:338` vs `[live]` |
| 17 | Sectors | "24 phân ngành" | `totalSectors = 25` (gồm `"Tất cả"`) | `API_DOCUMENTATION.md:336` vs `[live]` |
| 18 | `/BatchStockPrices` | "Batch giá nhiều cổ phiếu" | Cần `fromDate`+`toDate`, nếu thiếu trả rỗng HTTP 200; `market` mặc định HOSE; mỗi mã lấy row đầu của `pageSize=1`; `status` là **200 (number)** | `API_DOCUMENTATION.md:434` vs `main.js:389-441` `[live]` |
| 19 | Health `ssiReady` | `ssiStreamReady` | Trong WS là `ssiReady`; trong `/health` là `ssiStreamReady` | `AGENTS.md:118` vs `main.js:335,472` `[live]` |
| 20 | `/api/market/batch` không tham số | Không cảnh báo | Resolve ra toàn bộ master (~618 mã) → timeout | `[live]` |

---

## E. Phần KHÔNG kiểm chứng được (không đoán)

1. **Nội dung tick realtime thật**: trong toàn bộ thời gian kiểm chứng, `/health` cho `ssiStreamReady:false`, `inboundTicks:0` → **không có tick nào chảy qua**. Do đó shape cụ thể của message tick (field nào có mặt, `Content` là string hay object, `DataType` thực tế là `X-QUOTE`/`X-TRADE`/`MI`) **chưa được xác nhận trực tiếp**; chỉ suy ra từ code (`main.js:86-144`) + spec SSI PDF.
2. **Vì sao phần quote/khối ngoại trong `/api/market/batch` luôn bằng 0**: đã xác nhận hiện tượng `[live]`, nhưng lỗi gốc bị `.catch(() => new Map())` nuốt mất (`market.service.js:63`) nên **không xác định được nguyên nhân** (không đọc được log production). Cần bật `FC_LOG_VERBOSE=1`/`FC_LOG_STREAM=1` mới kết luận được.
3. **Đường lỗi HTTP của proxy**: nhánh 502 khi axios lỗi và body lỗi `{status:"Error",message,totalRecord:0,data:null}` **chỉ đọc từ code** (`main.js:296-306`), chưa tạo được tình huống lỗi thật.
4. **Nhánh 500** của `/api/market/batch`, `/api/v1/catalogs/*`, `/api/scanner/*` và nhánh 400 của `/api/market/history`: chỉ từ code, chưa tái hiện.
5. **Message `{"type":"ssi", ...}`**: chỉ phát khi stream đổi trạng thái hoặc refresh token (6h/lần) — chưa quan sát được.
6. **Giới hạn `pageIndex ≤ 10`** và một số ràng buộc khác: chỉ có trong spec SSI PDF (`docs/SSI_FastConnectData_Specs_v2.2.pdf`), chưa kiểm chứng live.
7. **Hành vi tầng nginx** (timeout, giới hạn body, buffer) và `hasBearerToken:true` có nghĩa token còn hiệu lực hay không: không kiểm chứng.
8. **Không có rate limit cho REST**: không thấy code giới hạn theo client; chưa kiểm chứng bằng tải thật nên chỉ ghi nhận "không thấy trong code".
9. **Bộ PDF trong repo** (`docs/*.pdf`) mô tả SSI v2.1/2.2 với key **chữ thường** (`dataList`, `dailyohlcresponse`) và envelope khác (`responseCode/message/token`): thực tế API trả key **PascalCase** và envelope `{data,message,status,totalRecord}`. Đây là lệch giữa spec PDF và API thật — đã xác nhận live, nhưng **không** kết luận được đâu là "đúng" theo SSI.
