# -*- coding: utf-8 -*-
"""Chuva do MERGE/INPE (GPM + pluviômetros, grade 0,1°) na bacia do Iguaçu.

  Diário: https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{ano}/{mes}/MERGE_CPTEC_{ano}{mes}{dia}.grib2 (~500 KB)
  Climatologia mensal 1998-2024: .../CLIMATOLOGY/MONTHLY_ACCUMULATED/MERGE_CPTEC_acum_{mes}.nc

Grava:
  dados/merge/mascara.npz              índices das células dentro do polígono (config/bacia_iguacu.geojson) + lat/lon
  dados/merge/chuva_bacia_diaria.csv   data, chuva_mm (média das células), n_celulas
  dados/merge/celulas.parquet          data, celula, mm  (valor por célula, últimos 120 dias, para o mapa)
  dados/merge/mlt.json                 MLT mensal oficial na bacia (--mlt, uma vez)
Uso: py coleta/merge.py [--desde AAAA-MM-DD] [--ate AAAA-MM-DD] [--mlt]
Padrão: completa os dias faltantes dos últimos 10 dias. Retomável: dias já no CSV não são baixados de novo.
Armadilha (skill chuva-merge-bacias): o cfgrib nomeia mal a variável; a chuva é identificada pelo intervalo de valores,
e a longitude da grade diária vem em 240-340 (a da climatologia já vem em -180..180: máscara própria).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import CONFIG, DADOS, gravar_status, hoje_brt, log  # noqa: E402
from geo import mascara  # noqa: E402

URL_DIA = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{a}/{m:02d}/MERGE_CPTEC_{a}{m:02d}{d:02d}.grib2"
URL_CLIM = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/CLIMATOLOGY/MONTHLY_ACCUMULATED/MERGE_CPTEC_acum_{m}.nc"
MESES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
PASTA = DADOS / "merge"
GEOJSON = CONFIG / "bacia_iguacu.geojson"
CSV = PASTA / "chuva_bacia_diaria.csv"
CELULAS = PASTA / "celulas.parquet"
MASCARA = PASTA / "mascara.npz"
DIAS_CELULAS = 120
# O INPE reescreve o arquivo do dia: primeiro com IMERG-Early (~16 UTC do próprio dia), depois com IMERG-Late
# (~02:40 UTC do dia seguinte) e de novo no fechamento do mês (dias 1 a 4 do mês seguinte). Por isso os dias
# recentes são rebaixados a cada rodada, e o mês anterior é refeito no começo do mês.
REFAZ_DIAS = 7
URL_CTL = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{a}/{m:02d}/MERGE_CPTEC_{a}{m:02d}{d:02d}.ctl"
CITACAO = ("INPE/CPTEC, produto MERGE (precipitação diária em grade de 0,1°, satélite GPM combinado com pluviômetros), "
           "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/")


def abrir_precip(caminho: Path):
    ds = xr.open_dataset(str(caminho), engine="cfgrib", backend_kwargs={"indexpath": ""})
    for nome in ds.data_vars:
        v = np.asarray(ds[nome].values, dtype=float)
        if v.ndim == 2 and np.nanmin(v) >= -0.01 and np.nanmax(v) < 1000:
            return ds, np.clip(v, 0, None)
    raise RuntimeError(f"variável de chuva não identificada: {list(ds.data_vars)}")


def carregar_mascara(ds) -> dict:
    lat, lon = ds["latitude"].values, ds["longitude"].values
    if MASCARA.exists():
        z = np.load(MASCARA)
        if tuple(z["shape"]) == (len(lat), len(lon)):
            return {k: z[k] for k in z.files}
    LO, LA = np.meshgrid(lon, lat)
    m = mascara(LA, LO, GEOJSON)
    idx = np.where(m.ravel())[0]
    lon_c = ((LO.ravel()[idx] + 180) % 360) - 180
    out = {"shape": np.array(m.shape), "idx": idx, "lat": LA.ravel()[idx], "lon": lon_c}
    PASTA.mkdir(parents=True, exist_ok=True)
    np.savez(MASCARA, **out)
    log(f"máscara: {len(idx)} células (~{len(idx) * 0.1 * 0.1 * 111 * 111:.0f} km²)")
    return out


def ler_csv() -> pd.DataFrame:
    if CSV.exists():
        d = pd.read_csv(CSV, parse_dates=["data"])
        if "versao" not in d.columns:
            d["versao"] = None
        return d
    return pd.DataFrame(columns=["data", "chuva_mm", "n_celulas", "versao"])


def versao_do_dia(sess, d: date) -> str | None:
    """Qual rodada do IMERG gerou o arquivo do dia (early, late ou final), lida no título do .ctl."""
    try:
        r = sess.get(URL_CTL.format(a=d.year, m=d.month, d=d.day), timeout=(10, 30))
        if r.ok:
            for linha in r.text.splitlines():
                if linha.lower().startswith("title"):
                    return linha.split("GPM-IMERG", 1)[-1].strip(" _-").lower() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def processa_dias(dias: list[date]) -> tuple[int, list[str], dict]:
    serie = ler_csv()
    feitos = set(serie["data"].dt.date) if len(serie) else set()
    cel = pd.read_parquet(CELULAS) if CELULAS.exists() else pd.DataFrame(columns=["data", "celula", "mm"])
    sess = requests.Session()
    tmp = PASTA / "tmp.grib2"
    PASTA.mkdir(parents=True, exist_ok=True)
    novos, falhas, meta = [], [], {}
    m = None
    limite_refaz = hoje_brt() - timedelta(days=REFAZ_DIAS)
    for d in dias:
        if d in feitos and d < limite_refaz:
            continue
        try:
            r = sess.get(URL_DIA.format(a=d.year, m=d.month, d=d.day), timeout=(10, 90))
            if r.status_code == 404:
                falhas.append(f"{d}: ainda não publicado (404)")
                continue
            r.raise_for_status()
            tmp.write_bytes(r.content)
            ds, v = abrir_precip(tmp)
            if m is None:
                m = carregar_mascara(ds)
                meta = {k: str(ds[k].values) for k in ("time", "step", "valid_time") if k in ds.coords}
            vals = v.ravel()[m["idx"]]
            novos.append({"data": pd.Timestamp(d), "chuva_mm": round(float(np.nanmean(vals)), 3), "n_celulas": int(len(vals)),
                          "versao": versao_do_dia(sess, d)})
            if d >= hoje_brt() - timedelta(days=DIAS_CELULAS):
                cel = pd.concat([cel, pd.DataFrame({"data": pd.Timestamp(d), "celula": np.arange(len(vals)), "mm": np.round(vals, 2)})])
        except Exception as e:  # noqa: BLE001
            falhas.append(f"{d}: {type(e).__name__}: {e}")
            log("FALHA", d, e)
    tmp.unlink(missing_ok=True)
    if novos:
        serie = pd.concat([serie, pd.DataFrame(novos)]).drop_duplicates("data", keep="last").sort_values("data")
        serie.to_csv(CSV, index=False, date_format="%Y-%m-%d")
        cel["data"] = pd.to_datetime(cel["data"])
        cel = cel[cel["data"] >= pd.Timestamp(hoje_brt() - timedelta(days=DIAS_CELULAS))]
        cel = cel.drop_duplicates(["data", "celula"], keep="last").sort_values(["data", "celula"])
        cel.to_parquet(CELULAS, index=False)
    return len(novos), falhas, meta


def calcular_mlt() -> dict:
    cache = PASTA / "clim"
    cache.mkdir(parents=True, exist_ok=True)
    m = None
    mlt = {}
    for mes in MESES:
        arq = cache / f"acum_{mes}.nc"
        if not arq.exists():
            r = requests.get(URL_CLIM.format(m=mes), timeout=(10, 180))
            r.raise_for_status()
            arq.write_bytes(r.content)
        ds = xr.open_dataset(arq, engine="h5netcdf")
        if m is None:
            LO, LA = np.meshgrid(ds["lon"].values, ds["lat"].values)
            m = mascara(LA, LO, GEOJSON)
            log(f"máscara da climatologia: {m.sum()} células")
        v = np.asarray(ds["precacum"].values).squeeze()
        mlt[mes] = round(float(np.nanmean(v[m])), 1)
        log(f"MLT {mes}: {mlt[mes]} mm")
    out = {"mlt_mm": mlt, "n_celulas": int(m.sum()), "periodo": "1998-2024",
           "fonte": "INPE/CPTEC, climatologia mensal do MERGE (MERGE_CPTEC_acum_{mes}.nc), 1998-2024"}
    (PASTA / "mlt.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde")
    ap.add_argument("--ate")
    ap.add_argument("--mlt", action="store_true")
    args = ap.parse_args()
    hoje = hoje_brt()
    d1 = date.fromisoformat(args.ate) if args.ate else hoje
    if args.desde:
        d0 = date.fromisoformat(args.desde)
    elif hoje.day <= 6:
        d0 = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)  # refaz o mês anterior no fechamento
    else:
        d0 = d1 - timedelta(days=10)
    dias = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    n, falhas, meta = processa_dias(dias)
    if args.mlt or not (PASTA / "mlt.json").exists():
        try:
            calcular_mlt()
        except Exception as e:  # noqa: BLE001
            falhas.append(f"MLT: {type(e).__name__}: {e}")
    serie = ler_csv()
    ultimo = str(serie["data"].max().date()) if len(serie) else None
    reais = [f for f in falhas if "404" not in f]
    ok = len(serie) > 0 and not reais
    log(f"{n} dias novos; último dia {ultimo}; falhas: {falhas[:5]}")
    gravar_status("merge", ok=ok, dias_novos=n, ultimo_dia=ultimo, falhas=falhas, meta_grib=meta, citacao=CITACAO)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
