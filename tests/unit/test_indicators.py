"""Test cho registry chỉ báo và các hàm tính thuần."""

import numpy as np
import pytest

from app.indicators import Ohlcv
from app.indicators.momentum import compute_macd, compute_rsi
from app.indicators.registry import (
    DEFAULT_INDICATOR_REQUESTS,
    INDICATORS,
    compute_default_set,
    compute_indicator,
    get_spec,
    list_indicators,
    parameters_hash,
    parse_request,
)
from app.indicators.volatility import compute_atr, compute_bollinger

SAMPLE_SIZE = 260


def make_ohlcv(size: int = SAMPLE_SIZE, seed: int = 7) -> Ohlcv:
    """Sinh chuỗi OHLCV giả lập, đủ dài cho mọi warmup."""
    rng = np.random.default_rng(seed)
    close = 20000 + np.cumsum(rng.normal(0, 150, size))
    high = close + np.abs(rng.normal(0, 100, size))
    low = close - np.abs(rng.normal(0, 100, size))
    open_ = close + rng.normal(0, 50, size)
    high = np.maximum(high, open_)
    low = np.minimum(low, open_)
    volume = np.abs(rng.normal(1_000_000, 200_000, size))
    return Ohlcv(open=open_, high=high, low=low, close=close, volume=volume)


def test_registry_dang_ky_du_bo_chi_bao_mac_dinh() -> None:
    outputs = compute_default_set(make_ohlcv())

    assert len(outputs) == len(DEFAULT_INDICATOR_REQUESTS)
    assert {output.indicator for output in outputs} <= set(INDICATORS)


def test_list_indicators_tra_metadata() -> None:
    entries = list_indicators()

    assert len(entries) == len(INDICATORS)
    names = {entry["name"] for entry in entries}
    assert {"sma", "rsi", "macd", "atr", "relative_strength", "divergence"} <= names


def test_get_spec_bao_loi_khi_khong_ton_tai() -> None:
    with pytest.raises(KeyError):
        get_spec("khong-ton-tai")


def test_parameters_hash_on_dinh_theo_thu_tu_khoa() -> None:
    assert parameters_hash({"period": 20, "stddev": 2.0}) == parameters_hash(
        {"stddev": 2.0, "period": 20}
    )
    assert parameters_hash({"period": 20}) != parameters_hash({"period": 50})


def test_parse_request_gan_tham_so_theo_thu_tu_khai_bao() -> None:
    assert parse_request("sma:20") == ("sma", {"period": 20})
    assert parse_request("macd:12:26:9") == (
        "macd",
        {"fast_period": 12, "slow_period": 26, "signal_period": 9},
    )
    assert parse_request("rsi") == ("rsi", {})


def test_parse_request_bao_loi_khi_tham_so_khong_hop_le() -> None:
    with pytest.raises(ValueError):
        parse_request("sma:20:50")
    with pytest.raises(ValueError):
        parse_request("sma:abc")
    with pytest.raises(KeyError):
        parse_request("khong-ton-tai")


def test_chuoi_ngan_tra_insufficient_history() -> None:
    short = make_ohlcv(size=30)
    output = compute_indicator("sma", short, {"period": 200})

    assert output.status == "insufficient_history"
    assert output.values["sma"] is None


def test_rsi_luon_trong_khoang_0_100() -> None:
    ohlcv = make_ohlcv()
    output = compute_rsi(ohlcv.close, period=14)

    assert output.values["rsi"] is not None
    assert 0 <= output.values["rsi"] <= 100
    assert output.signal in {"overbought", "oversold", "neutral"}


def test_macd_histogram_bang_macd_tru_signal() -> None:
    ohlcv = make_ohlcv()
    output = compute_macd(ohlcv.close)

    macd = output.values["macd"]
    signal = output.values["signal_line"]
    histogram = output.values["histogram"]
    assert macd is not None and signal is not None and histogram is not None
    assert histogram == pytest.approx(macd - signal, rel=1e-9, abs=1e-6)


def test_atr_duong_va_bollinger_percent_b_trong_khoang() -> None:
    ohlcv = make_ohlcv()
    atr = compute_atr(ohlcv.high, ohlcv.low, ohlcv.close)
    bollinger = compute_bollinger(ohlcv.close)

    assert atr.values["atr"] is not None and atr.values["atr"] > 0
    percent_b = bollinger.values["percent_b"]
    assert percent_b is not None
    assert 0 <= percent_b <= 1
    assert bollinger.values["upper"] > bollinger.values["middle"] > bollinger.values["lower"]


def test_relative_strength_can_benchmark() -> None:
    ohlcv = make_ohlcv()

    without_benchmark = compute_indicator("relative_strength", ohlcv)
    assert without_benchmark.status == "insufficient_history"

    benchmark = np.linspace(1200, 1250, len(ohlcv))
    with_benchmark = compute_indicator("relative_strength", ohlcv, benchmark=benchmark)
    assert with_benchmark.status == "ok"
    assert with_benchmark.values["outperformance_pct"] is not None


def test_divergence_tra_bon_co_phan_ky() -> None:
    ohlcv = make_ohlcv()
    output = compute_indicator("divergence", ohlcv)

    assert set(output.values) == {
        "bearish_regular",
        "bullish_regular",
        "bearish_hidden",
        "bullish_hidden",
    }


def test_input_kind_khong_hop_le_bao_loi() -> None:
    spec = get_spec("sma")
    assert spec.input_kind == "close"
    assert spec.warmup == 20
