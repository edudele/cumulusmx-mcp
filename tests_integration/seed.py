"""Datos sintéticos para el CumulusMX real de las pruebas de integración (formato de la 5.2.0, verificado en su código).

- dayfile.txt: 59 campos por línea (LogFiles/DayFileRec.cs), un año largo de historia terminando AYER y con un hueco de 3 días
  (para que data_quality tenga algo que encontrar). Incluye heladas en invierno y días de lluvia.
- <aaaamm>log.txt: log de intervalos (LogFiles/LogFileRec.cs, 31 campos: "dd/MM/yy HH:mm,<marca de tiempo>,..."), del mes
  anterior, cada 10 minutos durante dos días. Las marcas de tiempo son segundos Unix de la hora local de la máquina (UTC en CI).

Uso: python -m tests_integration.seed <directorio data de CumulusMX>   (escribe además seed.json con las fechas usadas).
"""
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

DAYS = 400                  # días de historia
GAP_AT = 100                # posición (desde el inicio) del hueco de 3 días
GAP_LEN = 3


def hhmm(rng: random.Random, lo: int = 0, hi: int = 23) -> str:
    return f"{rng.randint(lo, hi):02d}:{rng.randint(0, 59):02d}"


def day_line(day: dt.date, rng: random.Random) -> str:
    doy = day.timetuple().tm_yday
    season = -math.cos(2 * math.pi * (doy - 15) / 365)             # -1 en pleno invierno, +1 en pleno verano
    mean = 12 + 10 * season + rng.uniform(-3, 3)
    tmin, tmax = mean - 5 - rng.uniform(0, 2), mean + 5 + rng.uniform(0, 2)
    rain = round(rng.uniform(0.2, 25), 1) if rng.random() < 0.28 else 0.0
    pmin = round(1008 + rng.uniform(-8, 6), 1)
    pmax = round(pmin + rng.uniform(1, 9), 1)
    hmin = rng.randint(25, 60)
    hmax = min(100, hmin + rng.randint(20, 50))
    gust = round(rng.uniform(15, 65), 1)
    f = [
        day.strftime("%d/%m/%y"),                                                    # 0
        f"{gust:.1f}", str(rng.randint(0, 359)), hhmm(rng),                           # 1-3
        f"{tmin:.1f}", hhmm(rng, 0, 8), f"{tmax:.1f}", hhmm(rng, 12, 18),             # 4-7
        f"{pmin:.1f}", hhmm(rng), f"{pmax:.1f}", hhmm(rng),                           # 8-11
        f"{(rain * 1.5):.1f}", hhmm(rng),                                            # 12-13
        f"{rain:.1f}", f"{mean:.1f}",                                                # 14-15
        f"{rng.uniform(40, 300):.1f}", f"{gust * 0.45:.1f}", hhmm(rng),               # 16-18
        str(hmin), hhmm(rng, 12, 18), str(hmax), hhmm(rng, 0, 8),                     # 19-22
        f"{rng.uniform(0, 6):.1f}", f"{max(0.0, rng.uniform(0, 11) * (season + 1.2) / 2.2):.1f}",  # 23-24
        f"{tmax + 1:.1f}", hhmm(rng, 12, 18),                                        # 25-26
        f"{tmax + 1.5:.1f}", hhmm(rng, 12, 18), f"{tmin - 2:.1f}", hhmm(rng, 0, 8),   # 27-30
        f"{rain / 3:.1f}", hhmm(rng),                                                # 31-32
        f"{tmin - 3:.1f}", hhmm(rng, 0, 8),                                          # 33-34
        f"{mean + 2:.1f}", hhmm(rng), f"{mean - 6:.1f}", hhmm(rng),                   # 35-38
        str(rng.randint(0, 359)),                                                    # 39
        f"{max(0.0, 18 - mean):.1f}", f"{max(0.0, mean - 18):.1f}",                   # 40-41
        str(rng.randint(150, 900)), hhmm(rng, 11, 15),                                # 42-43
        f"{rng.uniform(0, 9):.1f}", hhmm(rng, 11, 15),                                # 44-45
        f"{tmax + 1:.1f}", hhmm(rng, 12, 18), f"{tmin - 3:.1f}", hhmm(rng, 0, 8),     # 46-49
        f"{tmax + 2:.1f}", hhmm(rng, 12, 18),                                        # 50-51
        f"{max(0.0, 8 - mean) * 2:.1f}",                                             # 52
        f"{rain:.1f}", hhmm(rng),                                                    # 53-54
        "", "", "", "",                                                              # 55-58 (sin sensor BGT/WBGT)
    ]
    assert len(f) == 59, len(f)
    return ",".join(f)


def log_line(ts: dt.datetime, i: int, rng: random.Random) -> str:
    unix = int(ts.replace(tzinfo=dt.timezone.utc).timestamp())      # el servidor de CI trabaja en UTC
    temp = round(14 + 6 * math.sin(i / 12) + rng.uniform(-0.4, 0.4), 1)
    f = [
        ts.strftime("%d/%m/%y %H:%M"), str(unix),                                      # 0-1
        f"{temp:.1f}", str(rng.randint(40, 90)), f"{temp - 5:.1f}",                    # 2-4 temperatura, humedad, rocío
        f"{rng.uniform(0, 20):.1f}", f"{rng.uniform(5, 30):.1f}", str(rng.randint(0, 359)),  # 5-7 viento, racha, rumbo
        "0.0", "0.0", f"{1012 + rng.uniform(-2, 2):.1f}", "0.0",                       # 8-11 lluvia, presión, contador
        f"{temp + 4:.1f}", str(rng.randint(35, 55)),                                   # 12-13 interior
        f"{rng.uniform(5, 35):.1f}", f"{temp - 1:.1f}", f"{temp + 0.5:.1f}",           # 14-16 racha ahora, sensación, calor
        "0.0", "0", "0.00", "0.00",                                                    # 17-20 UV, solar, ET, ET anual
        f"{temp - 0.5:.1f}", "0", "0.0", str(rng.randint(0, 359)), "0.0", "0.0",        # 21-26
        f"{temp - 0.8:.1f}", f"{temp + 0.2:.1f}", "", "",                              # 27-30 sensación, humidex, BGT, WBGT
    ]
    assert len(f) == 31, len(f)
    return ",".join(f)


def seed(data_dir: Path, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    rng = random.Random(20260512)
    data_dir.mkdir(parents=True, exist_ok=True)

    first = today - dt.timedelta(days=DAYS)
    days = [first + dt.timedelta(days=i) for i in range(DAYS)]            # termina AYER
    gap = set(days[GAP_AT:GAP_AT + GAP_LEN])
    lines = [day_line(d, rng) for d in days if d not in gap]
    (data_dir / "dayfile.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    month_start = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)   # primer día del mes anterior
    log_day = month_start + dt.timedelta(days=9)                                  # día 10 del mes anterior
    entries = [dt.datetime.combine(log_day, dt.time()) + dt.timedelta(minutes=10 * i) for i in range(288)]
    log_name = f"{month_start:%Y%m}log.txt"
    (data_dir / log_name).write_text("\n".join(log_line(t, i, rng) for i, t in enumerate(entries)) + "\n", encoding="utf-8")

    info = {
        "today": today.isoformat(), "dayfile_first": days[0].isoformat(), "dayfile_last": days[-1].isoformat(),
        "dayfile_lines": len(lines), "gap": sorted(d.isoformat() for d in gap),
        "log_file": log_name, "log_first": entries[0].isoformat(), "log_last": entries[-1].isoformat(),
        "log_unix_first": int(entries[0].replace(tzinfo=dt.timezone.utc).timestamp()),
    }
    (data_dir.parent / "seed.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1])), indent=2))
