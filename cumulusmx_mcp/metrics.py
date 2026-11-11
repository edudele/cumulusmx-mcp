"""Métricas en el formato de exposición de texto de Prometheus (sin dependencias), servidas en GET /metrics.

Contadores, histogramas y gauges en memoria (un solo proceso, un solo bucle de eventos: no hacen falta cerrojos).
Las etiquetas que se usan tienen cardinalidad acotada (nombre de herramienta, resultado, método HTTP): nunca llevan
argumentos, rutas ni datos de la estación.
"""
import time

from . import __version__

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

# nombre -> (tipo, ayuda)
_META: dict[str, tuple[str, str]] = {
    "cumulusmx_mcp_info": ("gauge", "Versión del servidor MCP (siempre 1)."),
    "cumulusmx_mcp_start_time_seconds": ("gauge", "Instante de arranque (segundos Unix)."),
    "cumulusmx_mcp_tool_calls_total": ("counter", "Llamadas a herramientas MCP por herramienta y resultado (ok|error)."),
    "cumulusmx_mcp_tool_duration_seconds": ("histogram", "Duración de las llamadas a herramientas MCP."),
    "cumulusmx_mcp_writes_total": ("counter", "Escrituras auditadas por herramienta y resultado (ok|fallo|denegado|error)."),
    "cumulusmx_mcp_cumulus_requests_total": ("counter", "Peticiones a CumulusMX por método y resultado (ok|http_error|conexion)."),
    "cumulusmx_mcp_cumulus_request_duration_seconds": ("histogram", "Duración de las peticiones a CumulusMX."),
    "cumulusmx_mcp_cumulus_up": ("gauge", "1 si la última petición a CumulusMX tuvo éxito, 0 si falló."),
    "cumulusmx_mcp_cumulus_last_success_timestamp_seconds": ("gauge", "Última petición correcta a CumulusMX (segundos Unix)."),
    "cumulusmx_mcp_backups_stored": ("gauge", "Copias de seguridad previas a escrituras conservadas."),
    "cumulusmx_mcp_write_tools_enabled": ("gauge", "1 si las herramientas de escritura están habilitadas."),
}

_Labels = tuple[tuple[str, str], ...]
_counters: dict[str, dict[_Labels, float]] = {}
_gauges: dict[str, dict[_Labels, float]] = {}
_histograms: dict[str, dict[_Labels, list]] = {}   # etiquetas -> [recuento por bucket..., suma, total]
_STARTED = time.time()


def _key(labels: dict[str, str]) -> _Labels:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def metric_inc(name: str, value: float = 1.0, **labels: str) -> None:
    series = _counters.setdefault(name, {})
    key = _key(labels)
    series[key] = series.get(key, 0.0) + value


def metric_gauge(name: str, value: float, **labels: str) -> None:
    _gauges.setdefault(name, {})[_key(labels)] = float(value)


def metric_observe(name: str, seconds: float, **labels: str) -> None:
    row = _histograms.setdefault(name, {}).setdefault(_key(labels), [0] * len(BUCKETS) + [0.0, 0])
    for i, bound in enumerate(BUCKETS):
        if seconds <= bound:
            row[i] += 1
    row[-2] += seconds
    row[-1] += 1


def reset_metrics() -> None:
    """Vacía todas las series (para los tests)."""
    _counters.clear()
    _gauges.clear()
    _histograms.clear()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_labels(key: _Labels, extra: tuple[tuple[str, str], ...] = ()) -> str:
    pairs = key + extra
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs) + "}" if pairs else ""


def _sample_value(value: float) -> str:
    return repr(int(value)) if float(value).is_integer() else repr(float(value))


def render_metrics() -> str:
    """Texto de exposición de Prometheus con todas las series conocidas."""
    metric_gauge("cumulusmx_mcp_info", 1, version=__version__)
    metric_gauge("cumulusmx_mcp_start_time_seconds", _STARTED)
    lines: list[str] = []
    for name, (kind, text) in _META.items():
        lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"]
        if kind == "counter":
            for key, value in sorted(_counters.get(name, {}).items()):
                lines.append(f"{name}{_fmt_labels(key)} {_sample_value(value)}")
        elif kind == "gauge":
            for key, value in sorted(_gauges.get(name, {}).items()):
                lines.append(f"{name}{_fmt_labels(key)} {_sample_value(value)}")
        else:
            for key, row in sorted(_histograms.get(name, {}).items()):
                for i, bound in enumerate(BUCKETS):
                    lines.append(f"{name}_bucket{_fmt_labels(key, (('le', _sample_value(bound)),))} {row[i]}")
                lines.append(f"{name}_bucket{_fmt_labels(key, (('le', '+Inf'),))} {row[-1]}")
                lines.append(f"{name}_sum{_fmt_labels(key)} {_sample_value(round(row[-2], 6))}")
                lines.append(f"{name}_count{_fmt_labels(key)} {row[-1]}")
    return "\n".join(lines) + "\n"
