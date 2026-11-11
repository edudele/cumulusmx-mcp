"""Evaluación de alertas meteorológicas sobre las condiciones actuales (lógica pura, sin red).

Los umbrales por defecto asumen unidades métricas (°C, km/h, mm/h, hPa); se pueden sustituir con `thresholds`.
"""
from typing import Any

DEFAULT_THRESHOLDS: dict[str, float] = {
    "frost_temp": 3.0,         # temperatura actual a partir de la cual hay riesgo de helada nocturna
    "wind_gust": 60.0,         # racha (alerta a 1,5 veces el umbral)
    "rain_rate": 20.0,         # intensidad de lluvia (alerta al doble)
    "heat_index": 35.0,        # sensación de calor (alerta a +7)
    "uv": 8.0,                 # índice UV (alerta a partir de 11)
    "pressure_drop_3h": -3.0,  # tendencia de presión; valores iguales o menores = bajada rápida
    "humidity_low": 20.0,      # humedad relativa baja (con calor, riesgo de incendio)
}
SEVERITY = {"alerta": 0, "aviso": 1, "info": 2}


def _alert(kind: str, level: str, message: str, value: Any = None, threshold: Any = None) -> dict[str, Any]:
    return {"tipo": kind, "nivel": level, "mensaje": message, "valor": value, "umbral": threshold}


def merge_thresholds(custom: dict[str, float] | None) -> dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in (custom or {}).items():
        if key not in DEFAULT_THRESHOLDS:
            raise ValueError(f"umbral desconocido '{key}'. Válidos: {sorted(DEFAULT_THRESHOLDS)}")
        merged[key] = float(value)
    return merged


def evaluate_alerts(now: dict[str, Any], thresholds: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Alertas activas a partir de las condiciones actuales.

    `now` admite (todas opcionales, números o None): temp, dew, hum, heatindex, wgust, wlatest, rrate, UV, presstrend,
    tempTL (mínima de hoy) y data_stopped (bool)."""
    th = merge_thresholds(thresholds)
    out: list[dict[str, Any]] = []
    temp, dew, hum = now.get("temp"), now.get("dew"), now.get("hum")

    low_today = now.get("tempTL")
    if low_today is not None and low_today <= 0:
        out.append(_alert("helada", "alerta", f"Hoy ya se ha alcanzado {low_today:g} (≤ 0): helada registrada.", low_today, 0))
    elif temp is not None and temp <= 0:
        out.append(_alert("helada", "alerta", f"Temperatura actual {temp:g} (≤ 0): helada en curso.", temp, 0))
    elif temp is not None and temp <= th["frost_temp"]:
        extra = " El punto de rocío es bajo: helada blanca probable." if dew is not None and dew <= 1 else ""
        out.append(_alert("helada", "aviso", f"Temperatura actual {temp:g} (≤ {th['frost_temp']:g}): riesgo de helada si el "
                          f"cielo está despejado y hay poco viento.{extra}", temp, th["frost_temp"]))

    gust = max((v for v in (now.get("wgust"), now.get("wlatest")) if v is not None), default=None)
    if gust is not None and gust >= th["wind_gust"]:
        level = "alerta" if gust >= 1.5 * th["wind_gust"] else "aviso"
        out.append(_alert("viento", level, f"Racha de {gust:g} (umbral {th['wind_gust']:g}).", gust, th["wind_gust"]))

    rate = now.get("rrate")
    if rate is not None and rate >= th["rain_rate"]:
        level = "alerta" if rate >= 2 * th["rain_rate"] else "aviso"
        out.append(_alert("lluvia intensa", level, f"Intensidad de lluvia {rate:g} (umbral {th['rain_rate']:g}).", rate, th["rain_rate"]))

    heat = now.get("heatindex") if now.get("heatindex") is not None else temp
    if heat is not None and heat >= th["heat_index"]:
        level = "alerta" if heat >= th["heat_index"] + 7 else "aviso"
        out.append(_alert("calor", level, f"Sensación de calor {heat:g} (umbral {th['heat_index']:g}).", heat, th["heat_index"]))

    uv = now.get("UV")
    if uv is not None and uv >= th["uv"]:
        level = "alerta" if uv >= 11 else "aviso"
        out.append(_alert("UV", level, f"Índice UV {uv:g} (umbral {th['uv']:g}).", uv, th["uv"]))

    trend = now.get("presstrend")
    if trend is not None and trend <= th["pressure_drop_3h"]:
        out.append(_alert("presión", "aviso", f"Bajada rápida de presión ({trend:g} en 3 h): posible empeoramiento.",
                          trend, th["pressure_drop_3h"]))

    if hum is not None and temp is not None and hum <= th["humidity_low"] and temp >= 25:
        out.append(_alert("sequedad", "info", f"Humedad {hum:g} % con {temp:g} de temperatura: ambiente muy seco y caluroso "
                          "(riesgo de incendio).", hum, th["humidity_low"]))

    if now.get("data_stopped"):
        out.append(_alert("estación", "alerta", "CumulusMX indica que la estación no está recibiendo datos."))

    return sorted(out, key=lambda a: SEVERITY[a["nivel"]])
