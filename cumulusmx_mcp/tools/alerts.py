"""Tool de alertas meteorológicas sobre las condiciones actuales."""
import json
from typing import Any

from .. import alerts, client, utils
from ..app import READ_ONLY, mcp

ALERT_TAGS = ["temp", "dew", "hum", "heatindex", "wgust", "wlatest", "rrate", "UV", "presstrend", "tempTL", "DataStopped",
              "tempunit", "windunit", "rainunit", "pressunit", "time", "date"]
_NUMERIC = ("temp", "dew", "hum", "heatindex", "wgust", "wlatest", "rrate", "UV", "presstrend", "tempTL")


@mcp.tool(annotations=READ_ONLY)
async def weather_alerts(thresholds: dict[str, float] | None = None) -> str:
    """Alertas activas ahora mismo con las condiciones actuales de la estación: helada (hoy o en curso, y riesgo nocturno),
    viento fuerte, lluvia intensa, calor, UV alto, bajada rápida de presión, ambiente muy seco y estación sin datos.
    thresholds (opcional) sustituye umbrales por defecto: frost_temp (3), wind_gust (60), rain_rate (20), heat_index (35),
    uv (8), pressure_drop_3h (-3), humidity_low (20), en las unidades de la estación (los valores por defecto asumen °C,
    km/h, mm/h y hPa; si la estación usa otras, la respuesta lo avisa). Cada alerta trae nivel (alerta > aviso > info)."""
    th = alerts.merge_thresholds(thresholds)  # valida los umbrales antes de consultar la estación
    text = await client._get(client._tags_path(ALERT_TAGS))
    try:
        raw = json.loads(text)
    except ValueError:
        raise ValueError(f"No se pudieron leer las condiciones actuales: {text[:200]}")
    now: dict[str, Any] = {k: utils._num(raw.get(k)) for k in _NUMERIC}
    now["data_stopped"] = str(raw.get("DataStopped", "")).strip().lower() in ("1", "true", "yes")
    units = {k: utils._clean(str(raw.get(k, ""))) for k in ("tempunit", "windunit", "rainunit", "pressunit")}
    notes = []
    expected = {"tempunit": "°C", "windunit": "km/h", "rainunit": "mm", "pressunit": "hPa"}
    off = {k: v for k, v in units.items() if v and v != expected[k]}
    if off:
        notes.append(f"Los umbrales por defecto asumen {', '.join(expected.values())}; tu estación usa {off}. "
                     "Pasa `thresholds` en tus unidades.")
    active = alerts.evaluate_alerts(now, thresholds)
    return json.dumps({"hay_alertas": any(a["nivel"] != "info" for a in active), "alertas": active, "momento": raw.get("time"),
                       "fecha": raw.get("date"), "condiciones": {k: v for k, v in now.items() if v is not None},
                       "unidades": units, "umbrales": th, "avisos": notes}, ensure_ascii=False)
