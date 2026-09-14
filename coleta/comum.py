# -*- coding: utf-8 -*-
"""Caminhos, fuso e leitura das configurações compartilhados pelos coletores."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config"
DADOS = RAIZ / "dados"
DOCS = RAIZ / "docs"
STATUS = DADOS / "status"

# O runner do GitHub Actions roda em UTC; "hoje" e "mês corrente" têm de ser no horário de Brasília,
# senão à meia-noite UTC (21h BRT) o mês vira antes de o ONS publicar o parquet novo.
BRT = timezone(timedelta(hours=-3))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def agora_brt() -> datetime:
    return datetime.now(BRT)


def hoje_brt():
    return agora_brt().date()


def usinas() -> list[dict]:
    return yaml.safe_load((CONFIG / "usinas.yaml").read_text(encoding="utf-8"))["usinas"]


def regras() -> list[dict]:
    return yaml.safe_load((CONFIG / "condicionantes.yaml").read_text(encoding="utf-8"))["regras"]


def estacoes() -> pd.DataFrame:
    df = pd.read_csv(CONFIG / "estacoes.csv", sep=";", dtype=str).fillna("")
    for c in ("AreaDrenagem", "Latitude", "Longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def gravar_status(fonte: str, **campos) -> None:
    STATUS.mkdir(parents=True, exist_ok=True)
    campos["atualizado_em"] = agora_brt().isoformat(timespec="seconds")
    (STATUS / f"{fonte}.json").write_text(json.dumps(campos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def ler_status(fonte: str) -> dict:
    arq = STATUS / f"{fonte}.json"
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}


def log(*a) -> None:
    print(agora_brt().strftime("%H:%M:%S"), *a, flush=True)
