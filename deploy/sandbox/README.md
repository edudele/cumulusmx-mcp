# Sandbox: probar escrituras sobre una copia de tu estación

Las herramientas de escritura (`edit_dayfile`, `edit_datalog`, `edit_records`, `edit_recent_data`, `edit_rain_today`,
`update_settings`...) cambian la estación de verdad. Para probarlas, o para dejar que una IA las pruebe, monta una **segunda
instancia de CumulusMX con una copia de tus ficheros** y conéctale un segundo MCP. Lo que se rompa se rompe en la copia.

Esta carpeta tiene el `docker-compose.example.yml` completo. Lo que sigue es lo que hay que hacer y, sobre todo, **por qué**.

## 1. La copia no debe poder hablar con el exterior

La copia lleva tus `Cumulus.ini` y sus credenciales reales: FTP, MQTT, MySQL, Weather Underground, WeatherCloud, CWOP,
correo... Si arranca con acceso a la red, **publicará datos viejos o inventados con tu identidad** (sensores de Home Assistant
pisados por MQTT, filas insertadas en tu base de datos de producción, subidas a tus servicios meteorológicos).

- Pon `cumulusmxro` **solo** en una red Docker con `internal: true` (sin salida a Internet ni a tu LAN). Es la protección
  principal y no depende de acordarte de apagar cada servicio.
- El MCP del sandbox sí está en esa red y en la de siempre, para que Claude lo alcance.
- No montes en la copia `cron/`, `utils/`, `mqtt/` ni `publicweb/` de la estación real: son los scripts y plantillas que
  suben o publican datos. En `Settings -> Web` (programas externos) deja vacíos `realtimeprogram` y `dailyprogram`.
- Si MySQL va a seguir activo en la copia, que apunte a **otra base de datos** (`cumulusmxro`), no a la de producción. Con la
  red interna ni siquiera la verá; aun así, lo más limpio es deshabilitarlo.

Una vez arrancada, comprueba (con el propio MCP del sandbox) que la copia no tiene nada activo hacia fuera. Se puede apagar
desde el MCP, con copia de seguridad restaurable:

```text
update_mysql_settings  {"monthly": {"enabled": false}, "realtime": {"enabled": false}, "dayfile": {"enabled": false}, "options": {"updateonedit": false}}
update_mqtt_settings   {"dataUpdate": {"enabled": false}, "interval": {"enabled": false}}
```

Y revisa `get_settings` de `internet`, `thirdparty` y `mqtt`. Ojo con una peculiaridad de CumulusMX: **solo guarda los campos
de un servicio si ese servicio está habilitado**. Desde la 1.8.2, si pides cambiar un campo de un servicio apagado,
`update_settings` responde `aplicado: false` y lista `no_guardados`, en vez de decir que fue bien.

## 2. Misma imagen, mismo locale

Usa la **misma imagen** que tu estación real (`hucknz/cumulusmx:latest`, por ejemplo) y el **mismo `LANG`/`LC_ALL`**
(`es_ES.UTF-8`). El locale decide la coma decimal y los formatos de fecha, y varios fallos reales solo aparecen con coma
decimal. Un sandbox con otro locale no prueba lo mismo.

## 3. Copiar los datos

Copia la carpeta de la estación a la de la copia. Si `cumulusmx.db` (SQLite) puede estar en uso, **para antes la estación real**
unos segundos para que la copia sea coherente:

```bash
docker stop cumulusmx
sudo rsync -a --delete /docker/cumulusmx/data/    /docker/cumulusmxro/data/
sudo rsync -a --delete /docker/cumulusmx/config/  /docker/cumulusmxro/config/
docker start cumulusmx
```

Cambia el tipo de estación **solo en la copia** a `Simulator` (así no intenta conectar con tu hardware ni con la API de tu
fabricante):

```bash
sudo sed -i '/^\[Station\]/,/^\[/{s/^Type=.*/Type=17/}' /docker/cumulusmxro/config/Cumulus.ini
```

Con el Simulator la copia genera datos en vivo inventados; tu histórico (`dayfile.txt`, logs mensuales, récords) es el real.
Las pruebas de escritura sobre el histórico no dependen de esos datos en vivo.

## 4. El MCP del sandbox

- **Token distinto** del de producción (`openssl rand -hex 32`). Así un conector puede estar en el cliente sin que el otro se
  confunda con él.
- **Volumen de datos escribible** por el usuario del contenedor. El MCP guarda una copia previa de cada cosa que cambia
  (`/data/backups`); si no puede escribir, **se niega a modificar nada** (desde la 1.8.2) y te lo explica. El usuario por
  defecto de la imagen es el uid 10001. Si fijas `user: "1000:1000"` en el compose, la carpeta debe ser de ese usuario, o
  fallará:

  ```bash
  sudo mkdir -p /docker/cumulusmxro/mcp-data
  sudo chown -R 1000:1000 /docker/cumulusmxro/mcp-data
  docker exec cumulusmx-mcp-sandbox sh -c 'mkdir -p /data/backups && touch /data/backups/prueba && echo OK && rm /data/backups/prueba'
  ```

- `CUMULUSMX_URL` apunta a la **copia** (`http://cumulusmxro:8998`), nunca a la estación real.

## 5. Conectarlo a Claude como segundo conector

Añade un conector MCP personalizado con la URL de tu sandbox y su token, **con otro nombre** que el de producción (por ejemplo
"Estación SANDBOX"). Antes de pedirle nada de escritura, que la IA compruebe que está en la copia y no en la real: por ejemplo
`server_status` y `snapshot` deben mostrar `DataStopped`/datos del Simulator, y `get_settings` de `station` el tipo
`Simulator`.

## 6. Qué probar, y cómo dejarlo todo como estaba

Cada escritura del MCP devuelve un `copia_id`, y `restore_backup` la deshace. La batería recomendada, siempre editar → comprobar
→ restaurar → comprobar que quedó igual:

| Qué | Herramientas |
|---|---|
| Histórico | `edit_dayfile`, `edit_datalog` |
| Récords | `edit_records` (alltime, monthly, thisyear, thismonth) |
| Datos recientes | `edit_recent_data` (con la fila completa de `recent_data`) |
| Lluvia de hoy | `edit_rain_today` |
| Ajustes | `update_settings` y los dedicados (NOAA, estación, calibración, alarmas, MQTT, MySQL) |
| Diario | `edit_diary`, `delete_diary` |

Lo que **no** conviene en una copia con datos vivos: `station_input` (los datos enviados se registran como observaciones reales).

Límites conocidos de CumulusMX, que no son fallos del MCP: los datos recientes se guardan con los decimales que muestra la
API; la hora de un récord se guarda al minuto; un récord sin valor ni fecha no puede restaurarse a "sin establecer"; el
asistente (`wizard`) recalcula `davisvp2.conntype` al guardar; y los espacios finales de los textos se recortan.

## 7. Reiniciar el sandbox a cero

```bash
docker stop cumulusmxro
sudo rsync -a --delete /docker/cumulusmx/data/ /docker/cumulusmxro/data/       # con la real parada si usa SQLite
sudo sed -i '/^\[Station\]/,/^\[/{s/^Type=.*/Type=17/}' /docker/cumulusmxro/config/Cumulus.ini
docker start cumulusmxro
```
