"""Test cho lớp chuẩn hoá payload gateway."""

from datetime import UTC, date, datetime

import pytest

from app.fcdata.mapper import (
    RejectedCandle,
    format_vn_date,
    map_candles,
    map_catalog_payload,
    map_daily_ohlc_payload,
    map_history_payload,
    map_quote_row,
    parse_vn_date,
    to_float,
    to_int,
)

VIETNAM_TZ_OFFSET_HOURS = 7


def test_parse_ngay_dd_mm_yyyy_theo_mui_gio_thi_truong() -> None:
    parsed = parse_vn_date("17/11/2026")

    assert parsed is not None
    assert parsed.utcoffset() is not None
    assert parsed.astimezone(UTC).hour == (24 - VIETNAM_TZ_OFFSET_HOURS) % 24
    assert format_vn_date(date(2026, 11, 17)) == "17/11/2026"


def test_parse_ngay_iso_va_gia_tri_khong_hop_le() -> None:
    assert parse_vn_date("2026-11-17") is not None
    assert parse_vn_date("32/13/2026") is None
    assert parse_vn_date("") is None
    assert parse_vn_date(None) is None


def test_to_float_va_to_int_chap_nhan_chuoi_cua_ssi() -> None:
    assert to_float("19250") == 19250.0
    assert to_float("1,234.5") == 1234.5
    assert to_float(None) is None
    assert to_float("abc") is None
    assert to_int("27277400") == 27277400
    assert to_int("1.9") == 1


def test_map_candles_chap_nhan_nhieu_dang_nhan() -> None:
    rows = [
        {
            "TradingDate": "17/11/2026",
            "Open": "19250",
            "High": "19350",
            "Low": "18500",
            "Close": "18850",
            "Volume": "27277400",
            "Value": "516265055000",
        },
        {
            "time": "18/11/2026",
            "open": 18850,
            "high": 19000,
            "low": 18700,
            "close": 18950,
            "volume": 1000,
            "value": 18950000,
        },
    ]
    batch = map_candles(rows, symbol="ssi")

    assert batch.rejected == []
    assert [candle.symbol for candle in batch.candles] == ["SSI", "SSI"]
    assert batch.candles[0].open == 19250.0
    assert batch.candles[0].turnover == 516265055000.0
    assert batch.candles[1].open_time > batch.candles[0].open_time


def test_map_candles_loai_ban_ghi_thieu_hoac_sai() -> None:
    rows = [
        {"TradingDate": "17/11/2026", "Open": "1", "High": "2", "Low": "0.5", "Volume": "10"},
        {
            "TradingDate": "18/11/2026",
            "Open": "1",
            "High": "0.5",
            "Low": "2",
            "Close": "1",
            "Volume": "10",
        },
        {
            "TradingDate": "19/11/2026",
            "Open": "1",
            "High": "2",
            "Low": "0.5",
            "Close": "1",
            "Volume": "-5",
        },
        {
            "TradingDate": "20/11/2026",
            "Open": "1",
            "High": "1.2",
            "Low": "0.5",
            "Close": "1.5",
            "Volume": "10",
        },
    ]
    batch = map_candles(rows, symbol="FPT")

    reasons = [item.reason for item in batch.rejected]
    assert reasons == [
        "missing_field:close",
        "invalid_range:high_below_low",
        "invalid_volume",
        "invalid_range:close_outside",
    ]
    assert all(isinstance(item, RejectedCandle) for item in batch.rejected)
    assert batch.candles == []


def test_map_candles_loai_trung_theo_thoi_diem_va_sap_xep() -> None:
    rows = [
        {"TradingDate": "18/11/2026", "Open": 2, "High": 3, "Low": 1, "Close": 2, "Volume": 10},
        {"TradingDate": "17/11/2026", "Open": 1, "High": 2, "Low": 0.5, "Close": 1, "Volume": 10},
        {"TradingDate": "18/11/2026", "Open": 5, "High": 6, "Low": 4, "Close": 5, "Volume": 20},
    ]
    batch = map_candles(rows, symbol="FPT")

    assert len(batch.candles) == 2
    assert batch.candles[0].close == 1.0
    assert batch.candles[1].close == 5.0


def test_map_history_payload_boc_dung_data_candles() -> None:
    payload = {
        "status": 200,
        "data": {
            "symbol": "FPT",
            "timeframe": "1D",
            "candles": [
                {
                    "time": "17/11/2026",
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 10,
                    "value": 15,
                },
            ],
        },
    }
    batch = map_history_payload(payload, symbol="FPT")

    assert len(batch.candles) == 1
    assert batch.candles[0].timeframe == "1D"


def test_map_daily_ohlc_payload_doc_mang_data() -> None:
    payload = {
        "data": [
            {
                "Symbol": "SSI",
                "TradingDate": "17/11/2026",
                "Open": "19250",
                "High": "19350",
                "Low": "18500",
                "Close": "18850",
                "Volume": "27277400",
                "Value": "1",
            },
        ]
    }
    batch = map_daily_ohlc_payload(payload, symbol="SSI")

    assert len(batch.candles) == 1
    assert batch.candles[0].symbol == "SSI"


def test_map_catalog_payload_chuan_hoa_ma() -> None:
    payload = {
        "data": [
            {"symbol": "fpt", "name": "CTCP FPT", "market": "HOSE", "sector": "Công nghệ"},
            {"symbol": "", "name": "khong co ma"},
        ]
    }
    records = map_catalog_payload(payload)

    assert len(records) == 1
    assert records[0].symbol == "FPT"
    assert records[0].exchange == "HOSE"
    assert records[0].company_name == "CTCP FPT"


def test_map_quote_row_doc_pascal_case() -> None:
    row = {
        "TradingDate": "17/11/2026",
        "ClosePrice": "19850",
        "PerPriceChange": "0.75",
        "TotalMatchVol": "15271200",
        "Symbol": "SSI",
    }
    quote = map_quote_row(row, symbol="ssi")

    assert quote.symbol == "SSI"
    assert quote.price == 19850.0
    assert quote.change_percent == 0.75
    assert quote.volume == 15271200
    assert quote.as_of is not None


@pytest.mark.parametrize("value", [datetime(2026, 11, 17, tzinfo=UTC), date(2026, 11, 17)])
def test_format_vn_date_chap_nhan_date_va_datetime(value) -> None:
    assert format_vn_date(value) == "17/11/2026"
