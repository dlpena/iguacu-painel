# -*- coding: utf-8 -*-
"""Coleta das 6 usinas do Iguaçu nos dados abertos do ONS (S3 público, sem credencial).

  Horário (desde 2010): dados_hidrologicos_ho/DADOS_HIDROLOGICOS_HO_{ano}_{mes:02d}.parquet
  Diário  (desde 2000): dados_hidrologicos_di/DADOS_HIDROLOGICOS_RES_{ano}.parquet

Grava dados/ons/ho_AAAA-MM.parquet e dados/ons/di_AAAA.parquet só com a bacia IGUACU.
Cada rodada rebaixa o mês/ano corrente (o ONS reescreve o arquivo continuamente) e o mês anterior.
Uso: py coleta/ons.py [--desde AAAA-MM]   (--desde faz o backfill horário a partir daquele mês)

Limpeza herdada da skill fontes-hidrologicas: nomes sem espaços de enchimento (em 2026 o ONS publica
"BAIXO IGUACU      "), val_* para número, horário só no minuto 0, sem duplicatas.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import DADOS, gravar_status, hoje_brt, log, usinas  # noqa: E402

S3 = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/"
HOUR = S3 + "dados_hidrologicos_ho/DADOS_HIDROLOGICOS_HO_{y}_{m:02d}.parquet"
DAILY = S3 + "dados_hidrologicos_di/DADOS_HIDROLOGICOS_RES_{y}.parquet"
PASTA = DADOS / "ons"
CITACAO_HO = ('ONS, Dados Abertos, conjunto "Dados Hidráulicos por Reservatório – Base horária" '
              '(https://dados.ons.org.br/dataset/dados_hidrologicos_ho)')
CITACAO_DI = ('ONS, Dados Abertos, conjunto "Dados Hidráulicos por Reservatório – Base diária" '
              '(https://dados.ons.org.br/dataset/dados-hidrologicos-res)')
COLS = ["nom_reservatorio", "din_instante", "val_nivelmontante", "val_niveljusante", "val_volumeutil",
        "val_volumeutilcon", "val_vazaoafluente", "val_vazaodefluente", "val_vazaoturbinada",
        "val_vazaovertida", "val_vazaonatural", "val_vazaooutrasestruturas", "val_vazaovertidanaoturbinavel"]


def baixa(url: str, tentativas: int = 3) -> pd.DataFrame | None:
    """None em 404 (arquivo ainda não publicado); exceção nos demais erros após as tentativas."""
    erro = None
    for i in range(tentativas):
        try:
            r = requests.get(url, timeout=300)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return pd.read_parquet(io.BytesIO(r.content))
        except Exception as e:  # noqa: BLE001
            erro = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url.rsplit('/', 1)[1]}: {type(erro).__name__}: {erro}")


def limpa(d: pd.DataFrame, horario: bool) -> pd.DataFrame:
    nomes = {u["ons"] for u in usinas()}
    d = d.copy()
    d["nom_reservatorio"] = d["nom_reservatorio"].astype(str).str.strip()
    if "nom_bacia" in d.columns:
        m = d["nom_bacia"].astype(str).str.strip().eq("IGUACU")
    else:
        m = pd.Series(False, index=d.index)
    d = d[m | d["nom_reservatorio"].isin(nomes)]
    d = d[d["nom_reservatorio"].isin(nomes)]
    d["din_instante"] = pd.to_datetime(d["din_instante"])
    for c in d.columns:
        if c.startswith("val_"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    if horario:
        # O ONS grava o registro da 0h como 23:59 do dia anterior (agosto/2026: 744 registros por usina, de
        # 01/08 01:00 a 31/08 23:59). Vira 00:00 do dia seguinte; antes era descartado pelo filtro de minuto zero.
        h0 = (d["din_instante"].dt.hour == 23) & (d["din_instante"].dt.minute == 59)
        d.loc[h0, "din_instante"] = d.loc[h0, "din_instante"] + pd.Timedelta(minutes=1)
        d = d[d["din_instante"].dt.minute == 0]
    d = (d.drop_duplicates(["nom_reservatorio", "din_instante"], keep="last")
          .sort_values(["nom_reservatorio", "din_instante"]))
    cols = [c for c in COLS if c in d.columns]
    return d[cols].reset_index(drop=True)


def meses_alvo(desde: str | None):
    hoje = hoje_brt()
    if desde:
        p0 = pd.Period(desde, "M")
    else:
        p0 = pd.Period(f"{hoje.year}-{hoje.month:02d}", "M") - 1
    p1 = pd.Period(f"{hoje.year}-{hoje.month:02d}", "M")
    return [(p.year, p.month) for p in pd.period_range(p0, p1, freq="M")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", help="AAAA-MM: backfill horário a partir deste mês")
    args = ap.parse_args()
    PASTA.mkdir(parents=True, exist_ok=True)
    hoje = hoje_brt()
    erros, baixados, ultimo = [], [], {}

    # ---- horário
    for (y, m) in meses_alvo(args.desde):
        url = HOUR.format(y=y, m=m)
        try:
            d = baixa(url)
        except Exception as e:  # noqa: BLE001
            erros.append(str(e))
            log("ERRO", e)
            continue
        if d is None:
            msg = f"HO {y}-{m:02d}: ainda não publicado (404)"
            log(msg)
            if not (y == hoje.year and m == hoje.month):
                erros.append(msg)
            continue
        d = limpa(d, horario=True)
        if d.empty:
            erros.append(f"HO {y}-{m:02d}: sem registros da bacia IGUACU (esquema mudou?)")
            continue
        d.to_parquet(PASTA / f"ho_{y}-{m:02d}.parquet", index=False)
        baixados.append(f"ho_{y}-{m:02d}")
        for u, g in d.groupby("nom_reservatorio"):
            ultimo[u] = max(ultimo.get(u, pd.Timestamp.min), g["din_instante"].max())
        log(f"HO {y}-{m:02d}: {len(d)} linhas, último instante {d['din_instante'].max()}")

    # ---- diário (ano corrente; anterior também em janeiro ou no backfill)
    anos = {hoje.year}
    if hoje.month == 1 or args.desde:
        anos.add(hoje.year - 1)
    if args.desde:
        anos.update(range(int(args.desde[:4]), hoje.year))
    for y in sorted(anos):
        try:
            d = baixa(DAILY.format(y=y))
        except Exception as e:  # noqa: BLE001
            erros.append(str(e))
            log("ERRO", e)
            continue
        if d is None:
            erros.append(f"DI {y}: 404")
            continue
        d = limpa(d, horario=False)
        d.to_parquet(PASTA / f"di_{y}.parquet", index=False)
        baixados.append(f"di_{y}")
        log(f"DI {y}: {len(d)} linhas, último dia {d['din_instante'].max().date()}")

    ok = any(b.startswith("ho_") for b in baixados)
    gravar_status("ons", ok=ok, arquivos=baixados, erros=erros,
                  ultimo_instante={u: t.isoformat() for u, t in ultimo.items()},
                  citacao_ho=CITACAO_HO, citacao_di=CITACAO_DI)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
