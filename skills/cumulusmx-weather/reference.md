# cumulusmx-mcp reference

Parameter notes for the 89 tools of cumulusmx-mcp. Tool names are in backticks in the first column.

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
| `climatology` | Optional `start_year` (default: server `CUMULUS_DATA_START_YEAR`), `end_year`. Per calendar month: `n_anios`, mean temperature/max/min, mean rain and rain days, mean sun hours, absolute max/min and gust with year, warmest/coldest/wettest/driest month with year. Only months with at least 80 % of days with data count. |
| `frost_dates` | Optional `threshold` (default 0; frost = `temp_min <= threshold`), `start_year`, `end_year`, `hemisphere` (`north`: spring = Jan-Jun, autumn = Jul-Dec; `south`: first and last frost of the calendar year). Per year: `ultima_helada_primavera`, `primera_helada_otono`, `dias_helada`, `periodo_sin_heladas_dias` (north only), `parcial`; plus `fecha_media` (MM-DD) from complete years only. Advise a safe date, not the mean. |
| `degree_days` | Optional `start`/`end` (default: 1 January of this year to the last day with data), `base` (default 10), `upper`, `method` (`average`, or `modified` which floors tmin at `base` and caps tmax at `upper`). Totals overall, `por_mes`, `por_anio`, in the station's temperature units. |
| `wind_rose` | Optional `start`/`end`. 16 sectors with days and percentage of the dominant daily direction, plus the peak gust per gust direction. A bearing of 0 can mean calm. |
| `daily_briefing` | No parameters. `ahora` (live tags, plain numbers), `datos_hasta` (last closed day), `ultimo_dia_cerrado_en_contexto` (percentile/rank of its Tmax, Tmin and rain vs the same month in other years), `mes_en_curso` (month-to-date anomaly of temperature and rain), `rachas_en_curso` (dry, Tmax >= 30, frost). Warns when the history is stale and degrades when live data or history is unavailable. |
| `get_settings` | `section`: `program`, `station`, `internet`, `thirdparty`, `extrasensors`, `extrawebfiles`, `calibration`, `language`, `noaa`, `mysql`, `alarms`, `useralarms`, `wizard`, `customlogs_interval`, `customlogs_daily`, `display`, `httpfiles`, `mqtt`. Same structure that `update_settings` accepts. May contain passwords (masked when `CUMULUS_MASK_SECRETS=true`). |
| `get_mysql_settings`, `get_mqtt_settings` | Shortcuts for `get_settings("mysql")` and `get_settings("mqtt")`. MySQL: `server{host,port,user,pass,database,advanced{sslMode,tlsVers}}`, `options`, `monthly`, `realtime`, `dayfile`, `custom*`. |
| `mysql_cache` | `start`, `length` (max 500), `search`. Pending SQL commands that failed to reach MySQL. |
| `diary_export` | Whole diary as CSV text. |
| `get_current_conditions_text` | The current-conditions text published by CumulusMX. |
| `records_edit_data` | `scope`: `alltime`, `monthly`, `thismonth`, `thisyear`; `source`: `records` (current values), `dayfile` or `logfile` (candidates, slower). |
| `interval_data` | `start`, `end` (Unix seconds, or `yyyy-mm-dd` / `yyyy-mm-dd HH:MM` when the server has `CUMULUS_TIMEZONE`), `fields` (main-log names such as `Temperature`, `Humidity`, `DewPoint`, `WindSpeed`, `RecentHighGust`, `RainfallRate`, `SeaLevelPressure`, `UvIndex`, `SolarRadiation`, `FeelsLike`, `Humidex`, `BGT`, `WBGT`; extra-sensor log names such as `Temp1`, `SoilTemp1`, `SoilMoist1`, `LeafWet1`, `CO2`, `SoilEC1`; or numeric indexes), `max_rows` (max 2000). Returns `campos` and `filas` with real numbers. |
| `daily_data` | Same arguments with the official day-file names (`MaxTemperature`, `MinTemperature`, `TotalRainfallToday`, `HighWindGust`, `TotalHoursOfSunshine`, `HighBgt`, ...). |
| `records_query` | `dataname` (e.g. `HighTemp`, `LowTemp`, `AvgTemp`, `TotalRain`, `HighGust`, `HighHumidex`, `ET`...), `function` (`min`, `max`, `sum`, `avg`, `count`), `period` (`ThisYear`, `ThisMonth`, `ThisDay`, `Year-N`, `Month-N`, `Day-N` or `User` with `start`/`end`), optional `where` (`>`, `<`, `>=`, `<=`, `=`, `<>`) with `value`. Computed by CumulusMX itself. Returns `{value, time}`. |
| `graph_select` | `kind` (`chart` or `period`), `series` (names from `graph_data("availabledata")`), `from_date`, `to_date`, optional `colours`. |
| `live_sample` | `seconds` (max 60), `max_messages` (max 20). Messages from the `/ws/` WebSocket, same JSON as `current_data`. Reports an `error` field if the socket cannot be opened. |
| `weather_alerts` | Optional `thresholds` (`frost_temp` 3, `wind_gust` 60, `rain_rate` 20, `heat_index` 35, `uv` 8, `pressure_drop_3h` -3, `humidity_low` 20; station units). Returns `hay_alertas`, `alertas` (`tipo`, `nivel` alerta > aviso > info, `mensaje`, `valor`, `umbral`), the conditions used and `avisos` (for example when the station is not metric). |
| `period_report` | `period` (`week` = 7 days ending at `ref_date`, `month` = day 1 to `ref_date`, `year` = 1 January to `ref_date`), `ref_date` (default: last day with data). Returns Markdown: temperature, rain and streaks, wind, pressure, sun, comparison with the station's normals, missing-data note. |
| `list_backups` | `kind` (`settings`, `dayfile`, `datalog`, `recent`, `mysqlcache`, `records` or empty), `limit`. Metadata only: `id`, `creada`, `tipo`, `objetivo`, `herramienta`. |
| `get_backup` | `backup_id`. Full content (passwords masked with `CUMULUS_MASK_SECRETS` or the read-only token). |
| `get_audit_log` | `limit`, `tool`. Recent writes: tool, token scope (`full` / `readonly`), arguments without secrets, result (`ok`, `fallo`, `denegado`, `error`). |
| `monthly_summary` | `year` (2000-2050). One aggregate per month that has data. |
| `compare_periods` | `start_a`, `end_a`, `start_b`, `end_b`. Returns `{"A": ..., "B": ...}` with the `climate_summary` statistics. |
| `year_records` | `year`. Extremes, rain totals, wettest day, frost days, warm days (>=25 and >=30 C). Errors if the year has no data. |
| `rankings` | `variable`, `top` (1-100, default 10), `order` (`asc`/`desc`), `start_year` (default: server `CUMULUS_DATA_START_YEAR`), `end_year` (default: current year). |
| `records` | `scope`: `alltime`, `thismonth`, `thisyear`, `month` (then `month` 1-12). `kind`: `temperature`, `humidity`, `pressure`, `wind`, `rain`. |
| `records_all` | Same scopes, all five kinds in one call. `strip_html` (default true). |
| `records_period` | `startdate`, `enddate`, both `yyyy-mm-dd`, years 2000-2050. |
| `graph_summary` | `name` (for example `tempdata`, `winddata`, `raindata`, `pressdata`, `humdata`, `solardata`, `wdirdata`, or `intvtemp`, `intvwind`, `intvrain`, `intvpress`, `intvhum`, `intvsolar`), optional `start`/`end`, `points` (0-300) to include a downsampled series. Timestamps are UTC. |
| `graph_data` | Raw series. Names: `tempdata`, `winddata`, `raindata`, `pressdata`, `wdirdata`, `humdata`, `solardata`, `airqualitydata`, `extratemp`, `extrahum`, `extradew`, `soiltemp`, `soilmoist`, `soilec`, `leafwetness`, `usertemp`, `co2sensor`, `laserdepth`, `snow24h`; daily: `dailyrain`, `sunhours`, `dailytemp`; meta: `units`, `graphconfig`, `availabledata`; per interval with `start`/`end`: `intvtemp`, `intvwind`, `intvrain`, `intvpress`, `intvhum`, `intvsolar` and `intv` + the extra-sensor names above. Large output. |
| `daily_graph_data` | Daily series: `tempdata`, `winddata`, `raindata`, `pressdata`, `wdirdata`, `humdata`, `solardata`, `degdaydata`, `dailysnow`, `tempsumdata`, `chillhrsdata`. Whole history, large output. |
| `noaa_report` | `period`: `noaayear` or `noaamonth`, `year`, `month`. Plain text. |
| `simple_report` | `report`: `temperature`, `rainfall`, `windrun`, `sunshine`, `et`, `drydays`, `wetdays`; `text` (default true; false returns HTML). CumulusMX summary tables built from the whole day file, in the station's units. Large output: summarize it. |
| `extra_sensors` | `sensor`: `temp`, `hum`, `dew`, `soiltemp`, `soilmoisture`, `soilec`, `leaf8`, `airqual`, `lightning`, `usertemp`, `laserdepth`, `laserdistance`, `snow24h`, `snowseason`, `airLinkCountsOut`, `airLinkAqiOut`, `airLinkPctOut`, `airLinkCountsIn`, `airLinkAqiIn`, `airLinkPctIn`, `co2sensor`, `bgt`. Current values; only sensors configured on the station return data. |
| `diary` | Without `date`: summary of days with notes. With `date`: that entry. |
| `get_tags` | `tags`: up to 50 webtag names (letters, digits, underscore, case sensitive), for example `["temp", "hum", "press"]`. `plain_numbers` (default true) applies CumulusMX's `rc` format: `10.0` instead of `10,0`. All values are returned as strings. |
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
Also available: `lluvia_24h_max` (max rain in 24 h), `bgt_max`, `wbgt_max` (black-globe / wet-bulb globe temperature; blank on stations without the sensor or on older day files), `lluvia_rate_max`, `viento_medio_max`, `dir_viento_dominante`, `solar_max`, `uv_max`, `ET`,
`recorrido_viento`, `indice_calor_max`, `temp_aparente_min/max`, `sensacion_viento_min`, `rocio_min/max`,
`humidex_max`, `grados_dia_calef`, `grados_dia_refr`, `horas_frio` and their `hora_*` time-of-extreme companions.

English aliases: `date`, `mintemp`, `maxtemp`, `avgtemp`/`temp`, `rain`/`precip`/`precipitation`,
`wind`/`gust`/`windgust`, `press`/`minpress`, `maxpress`, `hum`/`minhum`, `maxhum`,
`sun`/`sunhours`, `solar`, `uv`, `et`, `rain24h`, `bgt`, `wbgt`. Unknown columns raise an error that lists the valid ones.

Units (default configuration): temperature C, rain mm, gust km/h, pressure hPa, humidity %, sun hours h,
solar W/m2.

`summary_only=true` returns `n_dias`, the covered dates and, per numeric column, the min and max
(with date), the mean and the number of days with data. Additive columns (`lluvia_total`, `horas_sol`,
`ET`, `recorrido_viento`) also get a `suma` (total).

## Write tools (destructive, ask first)

Configuration writes return JSON: `seccion`, `campos_modificados` (`{path: {antes, despues}}`, secrets always masked),
`aplicado`, `respuesta`. With `dry_run=true` nothing is sent. With no differences nothing is sent either.

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

### Configuration, MySQL, MQTT

| Tool | Effect |
|---|---|
| `update_settings` | `section` (same names as `get_settings`), `settings` (object or JSON string), `dry_run`, `replace`. Reads the current section, deep-merges only the given fields (or replaces the section with `replace=true`) and sends it in the format each endpoint expects. Refuses to write if the current section cannot be read. |
| `update_mysql_settings`, `update_mqtt_settings`, `update_noaa_settings` | `settings`, `dry_run`. Partial merge of that section. |
| `mysql_table` | `action` (`create` or `update`), `table` (`monthly`, `dayfile`, `realtime`). Creates the table or adapts its structure. Run it after configuring and enabling the table. |

### History corrections (rewrite files: back up first)

| Tool | Effect |
|---|---|
| `edit_dayfile` | `action` (`Edit` or `Delete`), `line` (1-based, the first value returned by `dayfile`), `values` (the fields of that `dayfile.txt` line, date `dd/MM/yy` first, without the line number). CumulusMX checks the date matches. Also updates SQLite and, if enabled, MySQL. |
| `edit_datalog` | `action`, `line` (1-based within the month's log), `values` (all fields; the second one is the Unix `TS` that selects the file), `extra` (true for the extra-sensor log). |
| `edit_recent_data` | `action`, `values` (CSV fields of the record, as in `recent_data`). |
| `edit_mysql_cache` | `action`, `keys`, `statements` (one per key, for `Edit`). |
| `edit_records` | `scope` (`alltime`, `monthly` with `month` 1-12, `thismonth`, `thisyear`), `field` (e.g. `highTemp`, `lowTemp`, `highGust`, `highDailyRain`, `highMonthlyRain`, `longestDryPeriod`, `highWbgt`; `highMonthlyRain` does not exist for `thismonth`), `value`, `time` (`dd/MM/yyyy HH:mm`, `dd/MM/yyyy` for daily records, `MMM yyyy` for monthly rain). The value is sent with the station's decimal separator. |

### Undo

| Tool | Effect |
|---|---|
| `restore_backup` | `backup_id`, `dry_run` (default **true**). Restores a settings section (replacing it with the backup), a day-file or interval-log line, a recent-data record, a MySQL-cache statement or a record (previous value and time), as long as the original write was an *Edit*. A *Delete* cannot be re-inserted (CumulusMX has no such action): `get_backup` shows what was lost. The restore creates its own backup, so it can be undone too. |

`edit_dayfile`, `edit_datalog`, `edit_recent_data`, `edit_mysql_cache` and `edit_records` return JSON with `respuesta` (CumulusMX's answer) and `copia_id`. `edit_mysql_cache` Edit takes exactly one key and one statement; Delete reads the statements from the cache itself.

### Diary and device input

| Tool | Effect |
|---|---|
| `diary_upload` | `csv_content`: header line plus one record per line, as produced by `diary_export`. |
| `diary_automate` | `sensor` 1-4 selects the laser snow sensor that fills the diary; 0 disables automation. |
| `station_input` | `protocol` (POST: `ecowitt`, `ecowittextra`, `json`, `jsonextra`; GET: `ecowitt`, `ecowittextra`, `wunderground`, `ambient`, `ambientextra`), `method`, `body`, `query`, `content_type`. Sends a reading to `/station/<protocol>`; fails with 503 if the configured station is not of that type. Stored as a real observation. |

`generate_reports` also accepts `report_type="all"` to generate every missing report.
