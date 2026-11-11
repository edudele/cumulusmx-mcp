"""Arranque: argumentos, transporte HTTP/stdio y comprobación de conectividad."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402,F401
from cumulusmx_mcp import __version__, cli, config  # noqa: E402


def test_parser_defaults_and_options():
    args = cli.build_parser().parse_args([])
    assert (args.transport, args.host, args.port) == ("http", "0.0.0.0", None)
    args = cli.build_parser().parse_args(["--transport", "stdio", "--host", "127.0.0.1", "--port", "9000"])
    assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 9000)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--transport", "carrier-pigeon"])


def test_stdio_mode_runs_the_mcp_server_without_http():
    with patch.object(cli.mcp, "run") as run, patch.object(cli, "check_cumulus_connection", return_value=(True, "ok")), \
            patch("uvicorn.run") as uv:
        cli.main(["--transport", "stdio"])
    run.assert_called_once_with(transport="stdio", show_banner=False)
    uv.assert_not_called()


def test_http_mode_runs_uvicorn_with_the_asgi_app_and_port():
    sentinel = object()
    with patch.object(cli, "build_http_app", return_value=sentinel), \
            patch.object(cli, "check_cumulus_connection", return_value=(False, "caído")), patch("uvicorn.run") as uv:
        cli.main(["--port", "9123", "--host", "127.0.0.1"])
    assert uv.call_args.args == (sentinel,) and uv.call_args.kwargs["port"] == 9123 and uv.call_args.kwargs["host"] == "127.0.0.1"


def test_http_port_defaults_to_the_environment_value():
    with patch.object(config, "PORT", 8765), patch.object(cli, "build_http_app", return_value=1), \
            patch.object(cli, "check_cumulus_connection", return_value=(True, "ok")), patch("uvicorn.run") as uv:
        cli.main([])
    assert uv.call_args.kwargs["port"] == 8765


def test_logging_goes_to_stderr_so_stdio_stays_clean():
    import logging
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        cli.setup_logging()
        assert any(getattr(h, "stream", None) is sys.stderr for h in root.handlers)
    finally:
        root.handlers = saved


@pytest.mark.parametrize("module_args", [["-m", "cumulusmx_mcp"], [str(ROOT / "server.py")]])
def test_version_flag_as_module_and_as_script(module_args):
    out = subprocess.run([sys.executable, *module_args, "--version"], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip() == f"cumulusmx-mcp {__version__}"


class TestConnectionCheck:
    def make(self, status=200, payload=None, exc=None):
        client = MagicMock()
        if exc:
            client.get.side_effect = exc
        else:
            resp = MagicMock(status_code=status)
            resp.json.return_value = payload if payload is not None else {}
            client.get.return_value = resp
        ctx = MagicMock()
        ctx.__enter__.return_value = client
        return patch("httpx.Client", return_value=ctx)

    def test_ok_and_version(self):
        with self.make(payload={"Version": "5.2.0", "Build": "5026"}):
            ok, msg = cli.check_cumulus_connection()
        assert ok and "5.2.0" in msg and "5026" in msg

    @pytest.mark.parametrize("status,needle", [(401, "autenticacion"), (403, "autenticacion"), (500, "HTTP 500")])
    def test_failures(self, status, needle):
        with self.make(status=status):
            ok, msg = cli.check_cumulus_connection()
        assert not ok and needle in msg

    @pytest.mark.parametrize("exc,needle", [(httpx.ConnectError("x"), "No se pudo conectar"),
                                            (httpx.ReadTimeout("x"), "Tiempo de espera"), (RuntimeError("boom"), "boom")])
    def test_exceptions(self, exc, needle):
        with self.make(exc=exc):
            ok, msg = cli.check_cumulus_connection()
        assert not ok and needle in msg
