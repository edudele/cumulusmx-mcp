# cumulusmx-mcp reference

Parameter notes for the 51 tools of cumulusmx-mcp. Tool names are in backticks in the first column.

## Read-only tools

| Tool | Parameters and notes |
|---|---|
| `ping` | No parameters. Returns `pong`. |
| `server_status` | Returns the MCP server version and the CumulusMX version. Use it to confirm the backend is reachable. |
| `station_info` | `kind`: `version`, `units`, `alarms`, `snowinfo`, `timeformat`, `wsport`, `displaylangs`. |
| `current_data` | Full current-conditions JSON. |
| `snapshot` | Compact snapshot: temperature, feels-like, humidity, dew point, pressure and trend, wind, rain, sun/UV, today's extremes, sunrise/sunset, moon, station status. |
| `today_all` | Today vs yesterday for all variables. `strip_html` (default true). |
| `today_yesterday` | `kind`: `temp`, `hum`, `rain`, `wind`, `pressure`, `solar`. |
| `recent_data` | Last hours of data. `offset`, `length` (max 300). |
| `logfile` | Interval log. `start`, `end` (yyyy-mm-dd, max 31 days), `offset`, `length` (max 300), `extra` for extra sensors. |
| `dayfile` | Paginated raw daily history. `start`, `length` (max 500), `search`. Prefer `dayfile_range`. |
| `dayfile_range` | `start`, `end`, optional `columns` (names or English aliases), `max_rows` (max 400), `summary_only`. |
| `climate_summary` | `start`, `end`. Absolute max/min temperature with dates, mean, total rain, rain days, wettest day, peak gust, pressure extremes, sun hours, UV and radiation peaks. |
| `count_days` | `variable` (name or alias), `op` (`>`, `>=`, `<`, `<=`, `==`), `value`, optional `start`/`end`, `list_days` (max 366 dates). Counts matching days with a per-year breakdown, for example `temp_max >= 35` or `rain >= 0.2`. Compares in station units. |
| `find_streaks` | Same condition as `count_days` plus `min_length` (default 2) and `top` (1-50). Longest runs of consecutive days, and `racha_en_curso` if a run ends on the last day with data. Dry spell: `rain < 0.2`; heatwave: `temp_max >= 35`; frost: `temp_min <= 0`. A day without data breaks a run. |
| `anomaly` | `start`, `end`, `variable` (default `temp_media`), optional `baseline_start_year` / `baseline_end_year`. Compares the period with the same calendar window in the other years: `valor`, `normal`, `diferencia`, `diferencia_pct` (accumulated variables only), `percentil`, `posicion_entre_anios`, `por_anio`. Rain and sun hours are summed, others averaged. In-progress periods are compared on the elapsed part only. Years with under 80 % coverage are skipped; with fewer than 3 reference years a warning is returned. |
| `data_quality` | Optional `start`/`end`, `max_items`. Missing days, days without temperature and incoherent values (min > max, humidity outside 0-100, negative rain or sun). Run it before trusting records or long-term comparisons. |
| `monthly_summary` | `year` (2000-2050). One aggregate per month that has data. |
| `compare_periods` | `start_a`, `end_a`, `start_b`, `end_b`. Returns `{"A": ..., "B": ...}` with the `climate_summary` statistics. |
| `year_records` | `year`. Extremes, rain totals, wettest day, frost days, warm days (>=25 and >=30 C). Errors if the year has no data. |
| `rankings` | `variable`, `top` (1-100, default 10), `order` (`asc`/`desc`), `start_year` (default: server `CUMULUS_DATA_START_YEAR`), `end_year` (default: current year). |
| `records` | `scope`: `alltime`, `thismonth`, `thisyear`, `month` (then `month` 1-12). `kind`: `temperature`, `humidity`, `pressure`, `wind`, `rain`. |
| `records_all` | Same scopes, all five kinds in one call. `strip_html` (default true). |
| `records_period` | `startdate`, `enddate`, both `yyyy-mm-dd`, years 2000-2050. |
| `graph_summary` | `name` (for example `tempdata`, `winddata`, `raindata`, `pressdata`, `humdata`, `solardata`, `wdirdata`, or `intvtemp`, `intvwind`, `intvrain`, `intvpress`, `intvhum`, `intvsolar`), optional `start`/`end`, `points` (0-300) to include a downsampled series. Timestamps are UTC. |
| `graph_data` | Raw series for the same names plus `dailyrain`, `sunhours`, `dailytemp`, `units`, `availabledata`. Large output. |
| `daily_graph_data` | Daily series such as `tempdata`, `raindata`, `degdaydata`, `tempsumdata`, `chillhrsdata`. Whole history, large output. |
| `noaa_report` | `period`: `noaayear` or `noaamonth`, `year`, `month`. Plain text. |
| `diary` | Without `date`: summary of days with notes. With `date`: that entry. |
| `get_tags` | `tags`: up to 50 webtag names (letters, digits, underscore), for example `["temp", "hum", "press"]`. |
| `render_template` | A webtag template up to 1000 characters that contains `<#tag ...>` markers, for example `<#temp dp=2> <#time format="HH:mm">`. |
| `api_get` | Whitelisted read-only GET on CumulusMX endpoints. Use only when no dedicated tool exists. Settings, utils and reports-generation paths are blocked. |
| `get_error_log` | CumulusMX warnings and errors. |
| `get_station_settings`, `get_calibration_settings`, `get_alarm_settings`, `get_noaa_settings` | Read configuration. May contain credentials, so mask secrets. |

### `rankings` variables

`temp_min`, `temp_max`, `lluvia_total` (rain), `racha_max` (peak gust), `temp_media`, `horas_sol`.
Result keys: `variable`, `unidad`, `orden`, `periodo`, `top` (each item has `posicion`, date and value).
Use `order="asc"` for coldest minimum or driest, `desc` for the highest values.

### Day-file columns

Common columns for `dayfile_range`: `fecha`, `temp_min`, `temp_max`, `temp_media`, `lluvia_total`,
`racha_max`, `pres_min`, `pres_max`, `hum_min`, `hum_max`, `horas_sol`.
Also available: `lluvia_rate_max`, `viento_medio_max`, `dir_viento_dominante`, `solar_max`, `uv_max`, `ET`,
`recorrido_viento`, `indice_calor_max`, `temp_aparente_min/max`, `sensacion_viento_min`, `rocio_min/max`,
`humidex_max`, `grados_dia_calef`, `grados_dia_refr`, `horas_frio` and their `hora_*` time-of-extreme companions.

English aliases: `date`, `mintemp`, `maxtemp`, `avgtemp`/`temp`, `rain`/`precip`/`precipitation`,
`wind`/`gust`/`windgust`, `press`/`minpress`, `maxpress`, `hum`/`minhum`, `maxhum`,
`sun`/`sunhours`, `solar`, `uv`, `et`. Unknown columns raise an error that lists the valid ones.

Units (default configuration): temperature C, rain mm, gust km/h, pressure hPa, humidity %, sun hours h,
solar W/m2.

`summary_only=true` returns `n_dias`, the covered dates and, per numeric column, the min and max
(with date), the mean and the number of days with data. Additive columns (`lluvia_total`, `horas_sol`,
`ET`, `recorrido_viento`) also get a `suma` (total).

## Write tools (destructive, ask first)

| Tool | Effect |
|---|---|
| `reloaddayfile` | Reloads `dayfile.txt` in CumulusMX and clears the local cache. Safe, but run it only after editing the day file. |
| `clear_alarms` | `alarm` name (for example `BatteryLow`, `Sensor`) or `all` (default). |
| `clear_error_log` | Deletes the CumulusMX error log. Read it with `get_error_log` first. |
| `purgemysql` | Purges the MySQL pending/failed command queue and cache. |
| `trigger_ftp` | Immediate upload. Booleans `dailygraphs`, `noaa`, `graphs`, `logfiles`. |
| `generate_reports` | `report_type` `year` or `month`, `year`, `month` (default: current). Creates files on the server. |
| `upload_reports` | Uploads NOAA reports with the same arguments as `generate_reports`. |
| `test_email` | `from_email`, `dest_email`, optional `use_html`, `use_bcc`. Sends a real test email to check alarm SMTP settings. |
| `edit_current_conditions` | Sets the current-conditions text. |
| `edit_rain_today` | `raintoday`, optional `midnightrain`. Changes recorded rainfall. |
| `edit_diary` | Creates or updates a diary entry with observations and snow/storm flags. |
| `delete_diary` | Deletes the diary entry of one `date`. |
| `update_station_settings`, `update_calibration_settings`, `update_alarm_settings` | Partial merge of a settings object. Read the current settings first. |

When writes are disabled (`ENABLE_WRITE_TOOLS=false`) these tools return an error message
and change nothing.
