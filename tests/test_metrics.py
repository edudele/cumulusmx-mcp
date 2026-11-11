"""Endpoint /metrics (formato de exposición de Prometheus)."""
import re

import pytest

from cumulusmx_mcp import __version__, metrics
from tests.simulator import FakeCumulus
import server


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset_metrics()
    yield
    metrics.reset_metrics()


def sample(text, name, **labels):
    """Valor de una serie del texto de exposición (None si no existe)."""
    wanted = {f'{k}="{v}"' for k, v in labels.items()}
    for line in text.splitlines():
        m = re.fullmatch(rf"{re.escape(name)}(?:\{{(.*)\}})? (\S+)", line)
        if m and wanted <= set(filter(None, (m.group(1) or "").split(","))):
            return float(m.group(2))
    return None


def test_render_has_help_type_info_and_ends_with_newline():
    text = metrics.render_metrics()
    assert text.endswith("\n") and "# HELP cumulusmx_mcp_tool_calls_total" in text
    assert "# TYPE cumulusmx_mcp_tool_duration_seconds histogram" in text
    assert sample(text, "cumulusmx_mcp_info", version=__version__) == 1.0


def test_counters_accumulate_and_labels_are_escaped():
    metrics.metric_inc("cumulusmx_mcp_tool_calls_total", tool="a", resultado="ok")
    metrics.metric_inc("cumulusmx_mcp_tool_calls_total", tool="a", resultado="ok")
    metrics.metric_inc("cumulusmx_mcp_tool_calls_total", tool='we"ird\\\n', resultado="error")
    text = metrics.render_metrics()
    assert sample(text, "cumulusmx_mcp_tool_calls_total", tool="a", resultado="ok") == 2.0
    assert 'tool="we\\"ird\\\\\\n"' in text


def test_histogram_is_cumulative_and_consistent():
    for seconds in (0.01, 0.2, 7.0, 100.0):
        metrics.metric_observe("cumulusmx_mcp_tool_duration_seconds", seconds, tool="t")
    text = metrics.render_metrics()
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_bucket", tool="t", le="0.05") == 1.0
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_bucket", tool="t", le="0.25") == 2.0
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_bucket", tool="t", le="10") == 3.0
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_bucket", tool="t", le="+Inf") == 4.0
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_count", tool="t") == 4.0
    assert sample(text, "cumulusmx_mcp_tool_duration_seconds_sum", tool="t") == pytest.approx(107.21)


@pytest.mark.asyncio
async def test_cumulus_requests_and_up_gauge_follow_the_station(monkeypatch, tmp_path):
    from unittest.mock import patch
    cumulus = FakeCumulus()
    with patch.object(server, "_cli", return_value=cumulus.client()):
        await server._get("/api/info/version.json")
        await server._get("/api/settings/nope.json")                    # 404: la estación responde, pero con error
    text = metrics.render_metrics()
    assert sample(text, "cumulusmx_mcp_cumulus_requests_total", method="GET", resultado="ok") == 1.0
    assert sample(text, "cumulusmx_mcp_cumulus_requests_total", method="GET", resultado="http_error") == 1.0
    assert sample(text, "cumulusmx_mcp_cumulus_up") == 1.0 and sample(
        text, "cumulusmx_mcp_cumulus_last_success_timestamp_seconds") > 0


@pytest.mark.asyncio
async def test_unreachable_station_sets_up_to_zero():
    from unittest.mock import patch
    import httpx
    failing = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    with patch.object(server, "_cli", return_value=failing), patch.object(server.config, "CUMULUS_RETRIES", 0):
        assert "ERROR de conexión" in await server._get("/api/info/version.json")
    text = metrics.render_metrics()
    assert sample(text, "cumulusmx_mcp_cumulus_up") == 0.0
    assert sample(text, "cumulusmx_mcp_cumulus_requests_total", method="GET", resultado="conexion") == 1.0


@pytest.mark.asyncio
async def test_audited_writes_are_counted(tmp_path):
    from unittest.mock import patch
    cumulus = FakeCumulus()
    with patch.object(server, "_cli", return_value=cumulus.client()):
        await server.edit_dayfile("Edit", 1, ["24/08/19", "1"])
    assert sample(metrics.render_metrics(), "cumulusmx_mcp_writes_total", tool="edit_dayfile", resultado="ok") == 1.0
