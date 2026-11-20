"""CLI: parse params linh hoạt cho executor."""

import pytest

from app.workers.cli import _parse_params, _symbols


def test_parse_params_merges_json_and_key_value():
    params = _parse_params(
        '{"symbols": ["fpt"], "days": 3}',
        ["limit=20", "timeframe=1D", "include_all=true"],
    )

    assert params == {
        "symbols": ["fpt"],
        "days": 3,
        "limit": 20,
        "timeframe": "1D",
        "include_all": True,
    }


def test_parse_params_supports_comma_symbol_list():
    assert _parse_params("", ["symbols=FPT,HPG"]) == {"symbols": "FPT,HPG"}


def test_parse_params_rejects_invalid_input():
    with pytest.raises(SystemExit):
        _parse_params("[]", [])
    with pytest.raises(SystemExit):
        _parse_params("", ["missing-separator"])


def test_symbols_normalizes_and_drops_empty_items():
    assert _symbols(" fpt, ,hpg ") == ["FPT", "HPG"]
