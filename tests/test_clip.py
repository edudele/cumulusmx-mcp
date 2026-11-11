"""Recorte estructurado de respuestas demasiado grandes (client._clip)."""
import json
from unittest.mock import patch

from cumulusmx_mcp import config
import server


def test_small_responses_are_untouched():
    assert server._clip('{"a": 1}') == '{"a": 1}'


def test_oversize_json_rows_stay_valid_json_with_truncation_metadata():
    rows = [[i, "x" * 50] for i in range(5000)]
    raw = json.dumps({"draw": 1, "data": rows})
    with patch.object(config, "MAX_CHARS", 3000):
        out = json.loads(server._clip(raw))
    assert out["truncado"] is True and out["caracteres_totales"] == len(raw)
    assert out["filas_totales"] == 5000 and 0 < out["filas_devueltas"] < 5000
    assert out["contenido"]["draw"] == 1 and len(out["contenido"]["data"]) == out["filas_devueltas"]


def test_oversize_top_level_list():
    raw = json.dumps([{"i": i, "pad": "y" * 40} for i in range(2000)])
    with patch.object(config, "MAX_CHARS", 2000):
        clipped = server._clip(raw)
    out = json.loads(clipped)
    assert len(clipped) <= 2000 and out["filas_totales"] == 2000 and out["contenido"][0]["i"] == 0


def test_json_without_rows_is_clipped_inside_a_valid_envelope():
    raw = json.dumps({"blob": "z" * 10000})
    with patch.object(config, "MAX_CHARS", 1000):
        clipped = server._clip(raw)
    assert len(clipped) <= 1400 and json.loads(clipped)["truncado"] is True


def test_plain_text_keeps_the_legacy_notice():
    with patch.object(config, "MAX_CHARS", 100):
        out = server._clip("a" * 500)
    assert out.startswith("a" * 100) and "truncado: 500 caracteres" in out
