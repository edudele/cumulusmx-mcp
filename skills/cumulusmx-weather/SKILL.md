---
name: cumulusmx-weather
description: Answer questions about a home weather station running CumulusMX through the cumulusmx-mcp server. Use when the user asks about current conditions, today vs yesterday, records, rankings of hottest/coldest/wettest days, monthly or yearly climate summaries, comparisons between periods, NOAA reports, or the station's health, alarms and settings.
license: MIT
compatibility: Requires the cumulusmx-mcp MCP server (github.com/edudele/cumulusmx-mcp) connected to a reachable CumulusMX instance.
metadata:
  version: "1.4.0"
  server: cumulusmx-mcp
---

# CumulusMX weather station

Use this skill with the **cumulusmx-mcp** MCP server. It exposes a CumulusMX station
(current data, history, records, reports, settings) as MCP tools. If the tools are not
available, tell the user to connect the server first (see the repository README) instead of guessing data.

Never invent weather values. Every number you report must come from a tool result.

## Pick the right tool

| The user asks about | Use |
|---|---|
| "What is it like right now?" | `snapshot` (compact) or `current_data` (everything) |
| Today vs yesterday | `today_all` (all variables) or `today_yesterday` (one variable) |
| Hottest / coldest / wettest / windiest days ever | `rankings` |
| A year's extremes, frost days, warm days | `year_records` |
| Month-by-month overview of a year | `monthly_summary` |
| Stats for any date range | `climate_summary` |
| "How many days above/below X?" | `count_days` |
| Longest dry spell, heatwave, frost run | `find_streaks` |
| "Was this month warmer/wetter than normal?" | `anomaly` (compares with the station's own other years) |
| Can I trust the history? Gaps, sensor faults | `data_quality` |
| This period vs that period | `compare_periods` |
| Raw daily rows or specific columns | `dayfile_range` (use `summary_only=true` first) |
| All-time / this-month / this-year records | `records_all` (or `records` for one category) |
| Records inside a custom date range | `records_period` |
| Min/max/avg of last 24 h or an interval | `graph_summary` (avoid `graph_data`, it returns thousands of points) |
| Official-style monthly/annual report | `noaa_report` |
| Whole-history summary tables (temperature, rainfall, wind run, sunshine, ET, dry/wet days) | `simple_report` |
| Soil, leaf, air-quality (AirLink), lightning, CO2 or other extra sensors | `extra_sensors` |
| Is the station / MCP healthy? | `ping`, `server_status`, `get_error_log` |

Full parameter notes, column names and units are in [reference.md](reference.md).
Read it before calling `dayfile_range`, `rankings`, `graph_summary` or any write tool.

## Working rules

1. **Start broad, then narrow.** Prefer aggregate tools (`climate_summary`, `rankings`,
   `graph_summary`, `dayfile_range` with `summary_only=true`) over row dumps. Responses are
   clipped at about 60,000 characters, so large ranges get truncated.
2. **Dates are `yyyy-mm-dd`**; some graph tools also accept `yyyy-MM-dd HH:mm` in station local time.
   `graph_summary` returns timestamps in **UTC** (ISO), so convert before quoting local times.
3. **Units come from the station.** Defaults in the history tools are °C, mm, km/h, hPa.
   Check `station_info` with `kind="units"` if the user's station may be configured differently.
   `graph_summary` adds a `unidad` field per series when it can.
   **Decimal separators:** `get_tags` and `snapshot` return plain numbers with a decimal point.
   Text that CumulusMX formats itself (`records*`, `today_*`, `noaa_report`, `simple_report`, `diary`,
   `extra_sensors`, `logfile`) follows the station's regional settings and may use decimal commas
   (`"10,0"` means 10.0). Read them as decimals, never as thousands.
4. **Field and column names are in Spanish** (`temp_max`, `lluvia_total`, `racha_max`, `horas_sol`).
   English aliases such as `maxtemp`, `rain`, `gust` are accepted by `dayfile_range`.
   Translate to the user's language when reporting.
5. **Rankings use competition ranking** (1, 2, 2, 4): ties share a position.
6. **History starts at `CUMULUS_DATA_START_YEAR`** (default 2023). If a ranking looks too short,
   pass `start_year` explicitly, and say which years the answer covers.
7. **Distrust impossible values.** CumulusMX uses -9999 / 9999 for "no data" (the server already
   drops them) and a day with 0 % humidity is almost always a sensor fault. Run `data_quality` before
   quoting records or extremes, and mention suspicious days instead of presenting them as weather.
8. **Ties:** extreme values report up to 10 tied dates plus `n_fechas` (the total count) when there are more.
9. **Say what you could not find.** If a tool errors or returns empty data (station offline,
   year without data), report that instead of estimating.

## Write tools: ask first

The server also has 15 tools that change the station or its files. They are marked
destructive and can be switched off with `ENABLE_WRITE_TOOLS=false`
(if so they reply that writes are disabled; do not try to work around it).

- `reloaddayfile`, `clear_alarms`, `clear_error_log`, `purgemysql`, `trigger_ftp`,
  `generate_reports`, `upload_reports`, `test_email`
- `edit_current_conditions`, `edit_rain_today`, `edit_diary`, `delete_diary`
- `update_station_settings`, `update_calibration_settings`, `update_alarm_settings`

Before using any of them:

1. Say exactly what will change and on which values, and wait for an explicit "yes".
2. For settings, call the matching `get_*_settings` tool first and show the current value.
   Updates are partial merges, so send only the fields that must change.
3. Never change calibration, alarms or rain totals just to make data look better; explain
   the consequence for historical records when the user asks.
4. Settings payloads can contain credentials (FTP, SMTP, API keys). Never echo them back
   in full; mask secrets when summarizing.

## Ready-made prompts and resources

If the client supports them, the server also offers prompts `resumen_diario`,
`analisis_extremos`, `comparativa_periodos`, `resumen_anual` and resources
`weather://current`, `weather://today`, `weather://station`, `weather://records/alltime`.

## Answer style

- Lead with the answer (value, date, unit), then one line of context (for example "3rd warmest
  day since 2023").
- When comparing periods, give both values and the difference.
- For trends or long series, summarize and offer to go deeper instead of pasting tables.
