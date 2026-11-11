"""Prompts MCP guiados."""


from .app import mcp


@mcp.prompt("resumen_diario")
def resumen_diario_prompt(date: str = "") -> str:
    """Genera un análisis y resumen detallado del tiempo para una fecha dada (o de hoy)."""
    target = date if date else "el día de hoy"
    return (
        f"Por favor analiza los datos meteorológicos de la estación para {target}.\n"
        f"Consulta las condiciones actuales o el dayfile para esa fecha y resume:\n"
        f"- Temperaturas mínima, máxima y media\n"
        f"- Precipitación acumulada e intensidad\n"
        f"- Viento predominante y rachas máximas registradas\n"
        f"- Evolución de humedad y presión atmosférica\n"
        f"- Cualquier récord o valor anómalo destacable"
    )


@mcp.prompt("analisis_extremos")
def analisis_extremos_prompt(variable: str = "temp_max", top: int = 10) -> str:
    """Genera una comparativa histórica de eventos meteorológicos extremos."""
    return (
        f"Analiza los valores extremos históricos de la variable '{variable}' (top {top}).\n"
        f"Utiliza la herramienta 'rankings' y examina cuándo se produjeron estos extremos,\n"
        f"la distribución temporal de las efemérides y si se concentran en periodos u olas específicas."
    )


@mcp.prompt("comparativa_periodos")
def comparativa_periodos_prompt(start_a: str, end_a: str, start_b: str, end_b: str) -> str:
    """Compara climatológicamente dos periodos distintos."""
    return (
        f"Realiza una comparativa meteorológica detallada entre el periodo A ({start_a} a {end_a}) "
        f"y el periodo B ({start_b} a {end_b}).\n"
        f"Usa 'compare_periods' y resalta las diferencias en temperaturas medias y extremas, "
        f"precipitación total acumulada, días de lluvia y rachas de viento."
    )


@mcp.prompt("resumen_anual")
def resumen_anual_prompt(year: int) -> str:
    """Genera un informe climatológico anual completo de la estación."""
    return (
        f"Elabora un resumen climatológico detallado del año {year}.\n"
        f"Usa 'year_records' y 'monthly_summary' para revisar los hitos del año: "
        f"temperaturas extremas absolutas, días de helada, días cálidos (>=25°C y >=30°C), "
        f"lluvia acumulada y meses más lluviosos o secos."
    )


@mcp.prompt("resumen_mensual")
def resumen_mensual_prompt(year: int, month: int) -> str:
    """Informe de un mes concreto frente a la climatología propia de la estación."""
    return (
        f"Elabora el informe climático de {year}-{month:02d}.\n"
        f"1) Usa 'anomaly' (temp_media, temp_max, temp_min y lluvia_total) para comparar el mes con los mismos meses de otros años.\n"
        f"2) Usa 'climate_summary' para los extremos del mes y 'find_streaks' para la racha seca y la de calor más largas.\n"
        f"3) Usa 'count_days' para los días de lluvia (>= 0,2 mm) y de helada.\n"
        f"Concluye si fue un mes cálido/frío y seco/húmedo respecto a lo normal, con cifras y percentiles."
    )


@mcp.prompt("heladas_y_cultivo")
def heladas_y_cultivo_prompt(base_temp: float = 10.0) -> str:
    """Ayuda a planificar siembras: fechas de heladas y grados-día acumulados."""
    return (
        f"Ayúdame a planificar el huerto con los datos de mi estación.\n"
        f"Usa 'frost_dates' para decirme la fecha media de la última helada de primavera y de la primera de otoño, "
        f"y cuánto varían de un año a otro (sé prudente: recomienda una fecha segura, no la media).\n"
        f"Usa 'degree_days' con base {base_temp} desde el 1 de enero para ver cuántos grados-día llevamos este año "
        f"frente a años anteriores, y qué implica para el desarrollo de los cultivos."
    )


@mcp.prompt("auditoria_datos")
def auditoria_datos_prompt() -> str:
    """Revisa si el histórico de la estación es fiable antes de analizarlo."""
    return (
        "Audita la calidad de los datos de la estación.\n"
        "Usa 'data_quality' sobre todo el histórico y 'get_error_log' para ver errores recientes. "
        "Lista los días faltantes (agrupa los consecutivos en rangos), los valores incoherentes y la humedad 0 % "
        "(posible fallo del sensor), y dime qué conclusiones del histórico no deberían fiarse hasta corregirlo."
    )
