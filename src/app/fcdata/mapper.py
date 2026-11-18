"""Chuẩn hoá payload của BookFin FCData Gateway thành DTO nội bộ.

Gateway forward nguyên văn dữ liệu SSI nên nhãn field không đồng nhất:
proxy SSI dùng PascalCase và trả số dạng chuỗi, API nội bộ dùng camelCase. Mapper
là nơi duy nhất biết các biến thể đó, đồng thời tách bản ghi lỗi để quarantine
thay vì tự suy diễn giá trị.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SOURCE = "bookfin-fcdata"

_DATE_PATTERN = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


@dataclass(frozen=True, slots=True)
class CandleRecord:
    """Nến đã chuẩn hoá, sẵn sàng để upsert.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        open_time: Thời điểm mở nến theo UTC.
        open: Giá mở cửa.
        high: Giá cao nhất.
        low: Giá thấp nhất.
        close: Giá đóng cửa.
        volume: Khối lượng khớp.
        turnover: Giá trị khớp nếu nguồn có.
        price_basis: RAW hoặc ADJUSTED.
        source: Nguồn dữ liệu.
        source_updated_at: Thời điểm nguồn cập nhật, nếu biết.
    """

    symbol: str
    timeframe: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float | None = None
    price_basis: str = "RAW"
    source: str = SOURCE
    source_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RejectedCandle:
    """Bản ghi bị loại vì không đạt kiểm tra chất lượng.

    Attributes:
        reason: Mã lý do, ví dụ invalid_range hay missing_field:close.
        raw: Bản ghi gốc để audit.
    """

    reason: str
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CandleBatch:
    """Kết quả chuẩn hoá một lô nến.

    Attributes:
        candles: Nến hợp lệ, sắp xếp tăng dần và đã loại trùng.
        rejected: Bản ghi bị loại kèm lý do.
    """

    candles: list[CandleRecord] = field(default_factory=list)
    rejected: list[RejectedCandle] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    """Metadata tĩnh của một mã chứng khoán."""

    symbol: str
    exchange: str | None = None
    company_name: str | None = None
    sector: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class QuoteRecord:
    """Snapshot giá đã chuẩn hoá."""

    symbol: str
    price: float | None
    change_percent: float | None
    volume: int | None
    as_of: datetime | None
    source: str = SOURCE


def parse_vn_date(value: Any, *, tz: ZoneInfo = DEFAULT_TZ) -> datetime | None:
    """Chuyển ngày DD/MM/YYYY của SSI thành datetime UTC đầu phiên.

    Args:
        value: Chuỗi ngày, datetime hoặc giá trị ISO.
        tz: Múi giờ thị trường dùng để quy đổi đầu ngày sang UTC.

    Returns:
        Datetime UTC tại 00:00 theo múi giờ thị trường, hoặc None nếu không đọc được.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed_date = value.date()
    elif isinstance(value, date):
        parsed_date = value
    else:
        text = str(value).strip()
        if not text:
            return None
        match = _DATE_PATTERN.match(text)
        if match:
            day, month, year = (int(part) for part in match.groups())
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                return None
        else:
            try:
                parsed_date = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                return None
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=tz).astimezone(UTC)


def format_vn_date(value: date | datetime) -> str:
    """Định dạng ngày theo DD/MM/YYYY mà gateway yêu cầu.

    Args:
        value: Ngày hoặc datetime.

    Returns:
        Chuỗi ngày dạng DD/MM/YYYY.
    """
    day = value.date() if isinstance(value, datetime) else value
    return f"{day.day:02d}/{day.month:02d}/{day.year:04d}"


def to_float(value: Any) -> float | None:
    """Chuyển giá trị số của SSI (có thể là chuỗi) thành float.

    Args:
        value: Số, chuỗi số hoặc None.

    Returns:
        Float tương ứng, hoặc None khi giá trị rỗng/không hợp lệ.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    """Chuyển giá trị số thành int, bỏ phần thập phân nếu có.

    Args:
        value: Số, chuỗi số hoặc None.

    Returns:
        Int tương ứng, hoặc None khi không đọc được.
    """
    number = to_float(value)
    return None if number is None else int(number)


def _pick(raw: Mapping[str, Any], *keys: str) -> Any:
    """Lấy giá trị đầu tiên tồn tại trong bản ghi theo danh sách nhãn.

    Args:
        raw: Bản ghi gốc.
        *keys: Các nhãn có thể gặp, theo thứ tự ưu tiên.

    Returns:
        Giá trị đầu tiên khác None, hoặc None nếu không có nhãn nào.
    """
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _build_candle(
    raw: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    price_basis: str,
    tz: ZoneInfo,
) -> CandleRecord | RejectedCandle:
    """Chuẩn hoá một bản ghi thành nến hoặc lý do loại.

    Args:
        raw: Bản ghi gốc từ gateway.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        price_basis: RAW hoặc ADJUSTED.
        tz: Múi giờ thị trường.

    Returns:
        `CandleRecord` khi hợp lệ, `RejectedCandle` khi thiếu hoặc sai dữ liệu.
    """
    open_time = parse_vn_date(
        _pick(raw, "TradingDate", "tradingDate", "date", "time", "dateObj"), tz=tz
    )
    if open_time is None:
        return RejectedCandle("missing_field:date", raw)

    values: dict[str, float] = {}
    for label, keys in (
        ("open", ("open", "Open", "OpenPrice")),
        ("high", ("high", "High", "HighestPrice")),
        ("low", ("low", "Low", "LowestPrice")),
        ("close", ("close", "Close", "ClosePrice")),
    ):
        number = to_float(_pick(raw, *keys))
        if number is None:
            return RejectedCandle(f"missing_field:{label}", raw)
        values[label] = number

    volume = to_int(_pick(raw, "volume", "Volume", "TotalMatchVol", "TotalTradedVol"))
    if volume is None or volume < 0:
        return RejectedCandle("invalid_volume", raw)

    if values["high"] < values["low"]:
        return RejectedCandle("invalid_range:high_below_low", raw)
    if not (values["low"] <= values["open"] <= values["high"]):
        return RejectedCandle("invalid_range:open_outside", raw)
    if not (values["low"] <= values["close"] <= values["high"]):
        return RejectedCandle("invalid_range:close_outside", raw)

    turnover = to_float(_pick(raw, "value", "Value", "TotalMatchVal", "TotalTradedValue"))

    return CandleRecord(
        symbol=symbol.upper(),
        timeframe=timeframe,
        open_time=open_time,
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=volume,
        turnover=turnover,
        price_basis=price_basis,
    )


def map_candles(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str = "1D",
    price_basis: str = "RAW",
    tz: ZoneInfo = DEFAULT_TZ,
) -> CandleBatch:
    """Chuẩn hoá một lô bản ghi nến.

    Bản ghi lỗi được tách riêng để quarantine; nến hợp lệ được sắp xếp tăng dần
    và loại trùng theo thời điểm mở nến (bản ghi sau thắng).

    Args:
        rows: Danh sách bản ghi từ gateway.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        price_basis: RAW hoặc ADJUSTED.
        tz: Múi giờ thị trường.

    Returns:
        `CandleBatch` gồm nến hợp lệ và bản ghi bị loại.
    """
    batch = CandleBatch()
    by_time: dict[datetime, CandleRecord] = {}

    for raw in rows:
        result = _build_candle(
            raw, symbol=symbol, timeframe=timeframe, price_basis=price_basis, tz=tz
        )
        if isinstance(result, RejectedCandle):
            batch.rejected.append(result)
        else:
            by_time[result.open_time] = result

    batch.candles.extend(by_time[key] for key in sorted(by_time))
    return batch


def map_history_payload(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str = "1D",
    price_basis: str = "RAW",
    tz: ZoneInfo = DEFAULT_TZ,
) -> CandleBatch:
    """Chuẩn hoá payload của `GET /api/market/history/:symbol`.

    Args:
        payload: Body JSON trả về từ gateway.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        price_basis: RAW hoặc ADJUSTED.
        tz: Múi giờ thị trường.

    Returns:
        `CandleBatch` của các nến trong `data.candles`.
    """
    data = payload.get("data") or {}
    rows = data.get("candles") if isinstance(data, Mapping) else None
    return map_candles(
        rows or [],
        symbol=symbol,
        timeframe=str(data.get("timeframe") or timeframe)
        if isinstance(data, Mapping)
        else timeframe,
        price_basis=price_basis,
        tz=tz,
    )


def map_daily_ohlc_payload(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str = "1D",
    price_basis: str = "RAW",
    tz: ZoneInfo = DEFAULT_TZ,
) -> CandleBatch:
    """Chuẩn hoá payload proxy `GET /DailyOhlc`.

    Args:
        payload: Body JSON trả về từ gateway.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        price_basis: RAW hoặc ADJUSTED.
        tz: Múi giờ thị trường.

    Returns:
        `CandleBatch` của các row trong `data`.
    """
    rows = payload.get("data") or []
    return map_candles(rows, symbol=symbol, timeframe=timeframe, price_basis=price_basis, tz=tz)


def map_catalog_payload(payload: Mapping[str, Any]) -> list[SymbolRecord]:
    """Chuẩn hoá payload của `GET /api/v1/catalogs/master`.

    Args:
        payload: Body JSON trả về từ gateway.

    Returns:
        Danh sách `SymbolRecord` theo thứ tự gateway trả về.
    """
    records: list[SymbolRecord] = []
    for row in payload.get("data") or []:
        symbol = str(_pick(row, "symbol", "Symbol") or "").strip().upper()
        if not symbol:
            continue
        records.append(
            SymbolRecord(
                symbol=symbol,
                exchange=(str(_pick(row, "market", "Market")) or None),
                company_name=(str(_pick(row, "name", "StockName")) or None),
                sector=(str(_pick(row, "sector")) or None),
                active=True,
            )
        )
    return records


def map_quote_row(row: Mapping[str, Any], *, symbol: str) -> QuoteRecord:
    """Chuẩn hoá một row `DailyStockPrice` thành snapshot giá.

    Args:
        row: Row PascalCase từ proxy SSI.
        symbol: Mã chứng khoán.

    Returns:
        `QuoteRecord` với giá, phần trăm thay đổi và khối lượng khớp.
    """
    return QuoteRecord(
        symbol=symbol.upper(),
        price=to_float(_pick(row, "ClosePrice", "close", "LastPrice", "price")),
        change_percent=to_float(_pick(row, "PerPriceChange", "changePercent", "RatioChange")),
        volume=to_int(_pick(row, "TotalMatchVol", "volume", "TotalTradedVol")),
        as_of=parse_vn_date(_pick(row, "TradingDate", "tradingDate", "date", "time")),
    )
