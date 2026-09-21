# -*- coding: utf-8 -*-
"""Chuva do MERGE/INPE (GPM + pluviômetros, grade 0,1°) na bacia do Iguaçu.

  Diário: https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{ano}/{mes}/MERGE_CPTEC_{ano}{mes}{dia}.grib2 (~500 KB)
  Climatologia mensal 1998-2024: .../CLIMATOLOGY/MONTHLY_ACCUMULATED/MERGE_CPTEC_acum_{mes}.nc

Grava:
  dados/merge/mascara.npz              células com peso na bacia (config/bacia_iguacu.geojson): índice, lat/lon, peso em km²
  dados/merge/chuva_bacia_diaria.csv   data, chuva_mm (média ponderada por área), n_celulas, versao, metodo
  dados/merge/celulas.parquet          data, celula, mm  (valor por célula, últimos 120 dias, para o mapa)
  dados/merge/mlt.json                 MLT mensal oficial na bacia, mesmo método (--mlt, uma vez)
Média espacial "area-ponderada-1" (skill chuva-merge-bacias, 21/09/2026): peso de cada célula = fração da célula dentro
do polígono (interseção exata) x área da célula no elipsoide WGS84. A soma dos pesos tem de bater com a área oficial
do polígono (DME_AR_KM2 do SNIRH, tolerância 0,5%), senão o coletor para. Dias do CSV com outro método são refeitos.
Uso: py coleta/merge.py [--desde AAAA-MM-DD] [--ate AAAA-MM-DD] [--mlt]
Padrão: completa os dias faltantes dos últimos 10 dias. Retomável: dias já no CSV não são baixados de novo.
Armadilha (skill chuva-merge-bacias): o cfgrib nomeia mal a variável; a chuva é identificada pelo código 0/15/5 (PREC no .ctl),
e a longitude da grade diária vem em 240-340 (a da climatologia já vem em -180..180: pesos próprios).
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
from geo import METODO, ler_poligono, pesos  # noqa: E402

URL_DIA = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{a}/{m:02d}/MERGE_CPTEC_{a}{m:02d}{d:02d}.grib2"
PREC = (0, 15, 5)  # disciplina, categoria, número da chuva, conforme o .ctl do INPE (conferido em 1998 e 2026)
URL_CLIM = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/CLIMATOLOGY/MONTHLY_ACCUMULATED/MERGE_CPTEC_acum_{m}.nc"
MESES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
PASTA = DADOS / "merge"
GEOJSON = CONFIG / "bacia_iguacu.geojson"
CSV = PASTA / "chuva_bacia_diaria.csv"
CELULAS = PASTA / "celulas.parquet"
MASCARA = PASTA / "mascara.npz"
DIAS_CELULAS = 120
TOLERANCIA_AREA_PCT = 0.5
MIN_AREA_COM_DADO = 0.999  # fração mínima da área da bacia com dado para aceitar o dia
# O INPE reescreve o arquivo do dia: primeiro com IMERG-Early (~16 UTC do próprio dia), depois com IMERG-Late
# (~02:40 UTC do dia seguinte) e de novo no fechamento do mês (dias 1 a 4 do mês seguinte). Por isso os dias
# recentes são rebaixados a cada rodada, e o mês anterior é refeito no começo do mês.
REFAZ_DIAS = 7
URL_CTL = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{a}/{m:02d}/MERGE_CPTEC_{a}{m:02d}{d:02d}.ctl"
CITACAO = ("INPE/CPTEC, produto MERGE (precipitação diária em grade de 0,1°, satélite GPM combinado com pluviômetros), "
           "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/")


def abrir_precip(caminho: Path):
    """Chuva = variável com código GRIB2 0/15/5, que o .ctl do INPE ao lado de cada arquivo declara como
    "PREC 0,15,5 Surface Precipitation [kg/m^2]" (a outra é "NEST 0,3,1 Number of Stations / Grid Point").
    O INPE reaproveitou códigos da OMM: o cfgrib chama PREC de "rdp" e NEST de "prmsl" (pressão), e nome e
    unidade saem das tabelas da OMM, não do arquivo. Nem nome nem intervalo de valores servem para escolher."""
    ds = xr.open_dataset(str(caminho), engine="cfgrib", backend_kwargs={
        "indexpath": "", "read_keys": ["discipline", "parameterCategory", "parameterNumber"]})
    codigo = lambda n: tuple(ds[n].attrs.get(f"GRIB_{k}") for k in ("discipline", "parameterCategory", "parameterNumber"))
    chuva = [n for n in ds.data_vars if codigo(n) == PREC]
    if len(chuva) != 1:
        raise RuntimeError(f"variável PREC (código {PREC}) não encontrada: {[(n, codigo(n)) for n in ds.data_vars]}")
    v = np.asarray(ds[chuva[0]].values, dtype=float)
    # Sanidade só do mínimo aqui; o máximo é conferido nas células da bacia (media_ponderada). A grade cobre o
    # continente e o oceano, e há valores reais acima de 1.000 mm/dia fora do Brasil (15/09/2003: 1.260 mm no
    # Atlântico Norte, perto de 23°N 67°W). Um teste global parava o dia sem motivo.
    if v.ndim != 2 or np.nanmin(v) < -0.01:
        raise RuntimeError(f"chuva fora do esperado em {chuva[0]}: min {np.nanmin(v)}, ndim {v.ndim}")
    return ds, np.clip(v, 0, None)


def pesos_conferidos(lat, lon) -> tuple[np.ndarray, dict]:
    """Pesos da bacia na grade (lat, lon) e o resumo da conferência de área; para se a área destoar da oficial."""
    geom, oficial = ler_poligono(GEOJSON)
    W = pesos(lat, lon, geom)
    area = float(W.sum())
    if oficial is None:
        raise RuntimeError(f"{GEOJSON.name} sem DME_AR_KM2: baixe do SNIRH com outFields=* para conferir a área")
    dif = 100 * (area - oficial) / oficial
    if abs(dif) > TOLERANCIA_AREA_PCT:
        raise RuntimeError(f"área pelos pesos {area:.1f} km² difere {dif:.3f}% da oficial {oficial:.1f} km²")
    return W, {"metodo": METODO, "celulas": int((W > 0).sum()), "area_pesos_km2": round(area, 1),
               "area_oficial_km2": round(oficial, 1), "dif_area_pct": round(dif, 3)}


def carregar_mascara(ds) -> dict:
    lat, lon = ds["latitude"].values, ds["longitude"].values
    if MASCARA.exists():
        z = np.load(MASCARA)
        if "w" in z.files and str(z["metodo"]) == METODO and tuple(z["shape"]) == (len(lat), len(lon)):
            return {k: z[k] for k in z.files}
    W, conf = pesos_conferidos(lat, lon)
    LO, LA = np.meshgrid(lon, lat)
    idx = np.where(W.ravel() > 0)[0]
    lon_c = ((LO.ravel()[idx] + 180) % 360) - 180
    out = {"shape": np.array(W.shape), "idx": idx, "lat": LA.ravel()[idx], "lon": lon_c, "w": W.ravel()[idx],
           "metodo": np.array(METODO), "conferencia": np.array(json.dumps(conf))}
    PASTA.mkdir(parents=True, exist_ok=True)
    np.savez(MASCARA, **out)
    log(f"pesos: {conf}")
    return out


def media_ponderada(vals: np.ndarray, w: np.ndarray, limite=1000) -> float:
    """limite: máximo aceito numa célula da bacia (mm/dia); None para a climatologia mensal."""
    ok = np.isfinite(vals)
    if limite is not None and ok.any() and vals[ok].max() >= limite:
        raise RuntimeError(f"célula da bacia com {vals[ok].max():.1f} mm (limite 1000): conferir antes de usar")
    if w[ok].sum() < MIN_AREA_COM_DADO * w.sum():
        raise RuntimeError(f"só {w[ok].sum() / w.sum():.1%} da área da bacia tem dado neste dia")
    return float((w[ok] * vals[ok]).sum() / w[ok].sum())


def ler_csv() -> pd.DataFrame:
    if CSV.exists():
        d = pd.read_csv(CSV, parse_dates=["data"])
        for c in ("versao", "metodo"):
            if c not in d.columns:
                d[c] = None
        return d
    return pd.DataFrame(columns=["data", "chuva_mm", "n_celulas", "versao", "metodo"])


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
    # dias calculados com outro método não contam como feitos: são refeitos, para a série não misturar métodos
    feitos = set(serie.loc[serie["metodo"] == METODO, "data"].dt.date) if len(serie) else set()
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
            novos.append({"data": pd.Timestamp(d), "chuva_mm": round(media_ponderada(vals, m["w"]), 3),
                          "n_celulas": int(len(vals)), "versao": versao_do_dia(sess, d), "metodo": METODO})
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
    W = conf = None
    mlt = {}
    for mes in MESES:
        arq = cache / f"acum_{mes}.nc"
        if not arq.exists():
            r = requests.get(URL_CLIM.format(m=mes), timeout=(10, 180))
            r.raise_for_status()
            arq.write_bytes(r.content)
        ds = xr.open_dataset(arq, engine="h5netcdf")
        if W is None:
            W, conf = pesos_conferidos(ds["lat"].values, ds["lon"].values)
            log(f"pesos da climatologia: {conf}")
        v = np.asarray(ds["precacum"].squeeze().transpose("lat", "lon").values, dtype=float)
        mlt[mes] = round(media_ponderada(v[W > 0], W[W > 0], limite=None), 1)
        log(f"MLT {mes}: {mlt[mes]} mm")
    out = {"mlt_mm": mlt, "n_celulas": conf["celulas"], "metodo": METODO, "conferencia_area": conf, "periodo": "1998-2024",
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
    conf = json.loads(str(np.load(MASCARA)["conferencia"])) if MASCARA.exists() and "conferencia" in np.load(MASCARA).files else None
    gravar_status("merge", ok=ok, dias_novos=n, ultimo_dia=ultimo, falhas=falhas, meta_grib=meta, citacao=CITACAO,
                  metodo=METODO, conferencia_area=conf)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
