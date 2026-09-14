# -*- coding: utf-8 -*-
"""Coleta das estações telemétricas da ANA no webservice público (sem autenticação).

  GET https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos?codEstacao=&dataInicio=dd/mm/aaaa&dataFim=dd/mm/aaaa
  Elemento XML "DadosHidrometereologicos" (grafia do servidor): DataHora, Nivel (cm), Vazao (m³/s), Chuva (mm).

Grava dados/tele/AAAA-MM.parquet (codigo, instante, cota_cm, vazao, chuva_mm), acumulando entre rodadas
(o webservice só serve bem janelas curtas e o HidroWeb exige login). Cada rodada pede os últimos --dias
(padrão 7) de todas as estações de config/estacoes.csv e funde com o que já existe, sem duplicar.
Observação: nas estações de barramento o "Nivel" é a cota do reservatório em cm (ex.: 73856,7 = 738,567 m).
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import DADOS, estacoes, gravar_status, hoje_brt, log  # noqa: E402

WS = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/"
PASTA = DADOS / "tele"
CITACAO = ("ANA, webservice de telemetria (https://telemetriaws1.ana.gov.br/ServiceANA.asmx), "
           "método DadosHidrometeorologicos, dados brutos sem consistência")


def _tag(el) -> str:
    return el.tag.split("}")[-1]


def recente(codigo: str, ini, fim, sessao: requests.Session) -> pd.DataFrame:
    r = sessao.get(WS + "DadosHidrometeorologicos",
                   params={"codEstacao": codigo, "dataInicio": ini.strftime("%d/%m/%Y"), "dataFim": fim.strftime("%d/%m/%Y")},
                   timeout=180)
    r.raise_for_status()
    rows = []
    for el in ET.fromstring(r.text).iter():
        if _tag(el) == "DadosHidrometereologicos":
            d = {_tag(c): (c.text or "").strip() for c in el}
            if d.get("DataHora"):
                rows.append(d)
    if not rows:
        return pd.DataFrame(columns=["codigo", "instante", "cota_cm", "vazao", "chuva_mm"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({"codigo": codigo, "instante": pd.to_datetime(df["DataHora"], errors="coerce")})
    for col, src in (("cota_cm", "Nivel"), ("vazao", "Vazao"), ("chuva_mm", "Chuva")):
        out[col] = pd.to_numeric(df.get(src, pd.Series("", index=df.index)).astype(str).str.replace(",", "."), errors="coerce")
    out = out.dropna(subset=["instante"])
    return out[out[["cota_cm", "vazao", "chuva_mm"]].notna().any(axis=1)]


def fundir(novo: pd.DataFrame) -> list[str]:
    """Funde por mês com o parquet existente; devolve os arquivos tocados."""
    PASTA.mkdir(parents=True, exist_ok=True)
    tocados = []
    for periodo, g in novo.groupby(novo["instante"].dt.to_period("M")):
        arq = PASTA / f"{periodo}.parquet"
        if arq.exists():
            antigo = pd.read_parquet(arq)
            g = pd.concat([antigo, g])
        g = (g.drop_duplicates(["codigo", "instante"], keep="last")
              .sort_values(["codigo", "instante"]).reset_index(drop=True))
        g.to_parquet(arq, index=False)
        tocados.append(arq.name)
    return tocados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=7, help="janela pedida a cada rodada (padrão 7; máx. recomendado 60)")
    args = ap.parse_args()
    fim = hoje_brt()
    ini = fim - timedelta(days=args.dias)
    cat = estacoes()
    sessao = requests.Session()
    partes, por_estacao, erros = [], {}, []
    for cod in cat["Codigo"]:
        try:
            df = recente(cod, ini, fim, sessao)
        except Exception as e:  # noqa: BLE001
            erros.append(f"{cod}: {type(e).__name__}: {e}")
            log("ERRO", cod, e)
            continue
        por_estacao[cod] = {"n": int(len(df)), "ultimo": df["instante"].max().isoformat() if len(df) else None}
        if len(df):
            partes.append(df)
    novo = pd.concat(partes) if partes else pd.DataFrame()
    tocados = fundir(novo) if len(novo) else []
    com_dado = sum(1 for v in por_estacao.values() if v["n"])
    log(f"{com_dado} de {len(cat)} estações com dado na janela {ini}..{fim}; {len(novo)} registros; arquivos {tocados}")
    ok = com_dado > 0
    gravar_status("telemetria", ok=ok, janela={"inicio": str(ini), "fim": str(fim)}, estacoes=por_estacao,
                  arquivos=tocados, erros=erros, citacao=CITACAO)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
