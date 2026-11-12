---
name: cumulusmx-weather
description: Answer questions about a home weather station running CumulusMX through the cumulusmx-mcp server. Use when the user asks about current conditions, today vs yesterday, records, rankings of hottest/coldest/wettest days, monthly or yearly climate summaries, comparisons between periods, NOAA reports, or the station's health, alarms and settings.
license: MIT
compatibility: Requires the cumulusmx-mcp MCP server (github.com/edudele/cumulusmx-mcp) connected to a reachable CumulusMX instance.
metadata:
  version: "1.8.2"
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
| A full daily overview in one call | `daily_briefing` (now, yesterday in context, month vs normal, streaks in progress) |
| Today vs yesterday | `today_all` (all variables) or `today_yesterday` (one variable) |
| Hottest / coldest / wettest / windiest days ever | `rankings` |
| A year's extremes, frost days, warm days | `year_records` |
| Month-by-month overview of a year | `monthly_summary` |
| Stats for any date range | `climate_summary` |
| "How many days above/below X?" | `count_days` |
| Longest dry spell, heatwave, frost run | `find_streaks` |
| "Was this month warmer/wetter than normal?" | `anomaly` (compares with the station's own other years) |
| Can I trust the history? Gaps, sensor faults | `data_quality` |
| "What is normal for April?" Monthly normals and record years | `climatology` |
| Last spring frost / first autumn frost, frost-free period (planting dates) | `frost_dates` |
| Growing degree days for a crop (base 10 etc.) | `degree_days` |
| Prevailing wind directions, rose of the winds | `wind_rose` |
| "Is there any alert right now?" (frost tonight, wind, heavy rain, heat, UV, pressure drop) | `weather_alerts` |
| A shareable weekly / monthly / yearly report | `period_report` (Markdown) |
| What changed, when, by whom; undo a change | `get_audit_log`, `list_backups`, `get_backup`, `restore_backup` |
| Any configuration section (FTP, MySQL, MQTT, alarms, display...) | `get_settings` (or `get_mysql_settings`, `get_mqtt_settings`) |
| Raw interval logs with only some fields (no paging), or official day-file fields | `interval_data`, `daily_data` |
| Server-side day-file query ("max / count of ... this year") | `records_query` |
| Custom chart over a date range | `graph_select` |
| The few next seconds of live data | `live_sample` |
| Pending MySQL commands, diary CSV, current-conditions text | `mysql_cache`, `diary_export`, `get_current_conditions_text` |
| Record-editing screens (candidate values) | `records_edit_data` |
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

The server also has 29 tools that change the station, its configuration or its files. They are marked
destructive. When the server runs with `ENABLE_WRITE_TOOLS=false` they are not published at all
(and would reply that writes are disabled if called; do not try to work around it).

- Maintenance and reports: `reloaddayfile`, `clear_alarms`, `clear_error_log`, `purgemysql`, `trigger_ftp`,
  `generate_reports`, `upload_reports`, `test_email`
- Live values and diary: `edit_current_conditions`, `edit_rain_today`, `edit_diary`, `delete_diary`,
  `diary_upload`, `diary_automate`
- Configuration (any section): `update_settings`, plus `update_station_settings`, `update_calibration_settings`,
  `update_alarm_settings`, `update_noaa_settings`, `update_mysql_settings`, `update_mqtt_settings`
- MySQL tables: `mysql_table` (create or update the monthly, dayfile or realtime table)
- History corrections: `edit_dayfile`, `edit_datalog`, `edit_recent_data`, `edit_mysql_cache`, `edit_records`
- Device input: `station_input` (sends a reading to the HTTP station endpoint; it is stored as a real observation)
- Undo: `restore_backup` (restores a settings section, a day-file / interval-log line, a recent-data record, a MySQL-cache statement or a record from its backup; deletions are not restorable)

Before using any of them:

1. Say exactly what will change and on which values, and wait for an explicit "yes".
2. For configuration, read it first (`get_settings` or the specific getter), then call the update with
   `dry_run=true` and show the user the `campos_modificados` diff. Apply only after they confirm.
   Updates are partial merges: send only the fields that must change. If the current configuration cannot be
   read, nothing is written. Some changes need a CumulusMX restart.
3. **MySQL setup**: `get_mysql_settings` -> `update_mysql_settings` (host, port, user, pass, database, then enable
   monthly/dayfile/realtime) -> `mysql_table(action="create", ...)` for each enabled table -> check `mysql_cache`
   for pending commands. **MQTT**: `get_mqtt_settings` -> `update_mqtt_settings`.
4. **History edits** (`edit_dayfile`, `edit_datalog`, `edit_recent_data`, `edit_records`) rewrite files. Tell the user to
   back up first, show the original row (from `dayfile` / `logfile`) next to the new one, and edit one row at a time.
   `edit_dayfile` takes the fields of the `dayfile.txt` line *without* the leading line number that `dayfile`
   returns; the line number goes in `line`.
5. Never change calibration, alarms or rain totals just to make data look better; explain
   the consequence for historical records when the user asks.
6. **After every write, tell the user the `copia_id`** (when there is one) and that `restore_backup(copia_id)` undoes it.
   Every write is audited; `get_audit_log` shows what was done. If a write answers that the token is read-only or that
   writes are disabled, say so and stop: do not look for another way to do it.
7. Settings payloads contain credentials (FTP, MySQL, MQTT, SMTP, API keys). Do not repeat them in your answer;
   mask secrets when summarizing. With `CUMULUS_MASK_SECRETS=true` the server masks them for you.

## Ready-made prompts and resources

If the client supports them, the server also offers prompts `resumen_diario`,
`analisis_extremos`, `comparativa_periodos`, `resumen_anual` and resources
`weather://current`, `weather://today`, `weather://station`, `weather://records/alltime`.

## Answer style

- Lead with the answer (value, date, unit), then one line of context (for example "3rd warmest
  day since 2023").
- When comparing periods, give both values and the difference.
- For trends or long series, summarize and offer to go deeper instead of pasting tables.
