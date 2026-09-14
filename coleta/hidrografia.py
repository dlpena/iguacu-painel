# -*- coding: utf-8 -*-
"""Hidrografia para os mapas: rio Iguaçu e afluentes monitorados, da camada "Rios principais" do SNIRH/ANA.

  https://portal1.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer/0/query
  (camada "Curso d'Água": NORIOCOMP nome, DEDOMINIAL domínio, NUAREAMONT área a montante em km²)

Consulta por nome, recorta ao polígono da bacia (config/bacia_iguacu.geojson) e grava
config/hidrografia.geojson (rodar uma vez; os mapas leem docs/data/hidrografia.geojson gerado pelo monta_site).
Uso: py coleta/hidrografia.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import requests
from matplotlib.path import Path as MplPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import CONFIG, log  # noqa: E402
from geo import aneis  # noqa: E402

URL = "https://portal1.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer/0/query"
# nome exato na camada -> rótulo e classe (principal ou afluente monitorado). Só afluentes com estação no painel;
# o rio Jordão, por exemplo, fica de fora porque não tem estação cadastrada.
RIOS = {
    "Rio Iguaçu": ("Rio Iguaçu", "principal"),
    "Rio Chopim": ("Rio Chopim", "afluente"),
    "Rio Capanema": ("Rio Capanema", "afluente"),
    "Rio dos Andradas": ("Rio Andrada", "afluente"),
    "Rio das Cobras": ("Rio das Cobras", "afluente"),
    # o rio da estação 65975300 ("rio Andrada" no cadastro da ANA/agente) consta na camada como Rio São Salvador:
    # verificado em 14/09/2026 pela posição da estação (área a montante 1.213-1.401 km² na camada contra 1.387 km² no cadastro)
    "Rio São Salvador": ("Rio Andrada (São Salvador na base da ANA)", "afluente"),
}
CITACAO = ('ANA/SNIRH, serviço "Rios principais" (SNIRH2016/Rios_principais, camada Curso d\'Água), '
           'https://portal1.snirh.gov.br/arcgis/rest/services/SNIRH2016/Cursos_Agua_dominialidade/FeatureServer')


def dentro_da_bacia(poligonos):
    def f(lon, lat):
        for ext, furos in poligonos:
            if MplPath(ext).contains_point((lon, lat)) and not any(MplPath(h).contains_point((lon, lat)) for h in furos):
                return True
        return False
    return f


def main() -> int:
    where = " OR ".join(f"NORIOCOMP = '{n}'" for n in RIOS)
    # f=json (esri): no formato geojson o serviço devolve geometrias nulas
    r = requests.get(URL, params={"where": where, "outFields": "NORIOCOMP,NUAREAMONT,DEDOMINIAL", "returnGeometry": "true",
                                  "outSR": "4326", "f": "json"}, timeout=120)
    r.raise_for_status()
    gj = r.json()
    if "error" in gj:
        raise RuntimeError(gj["error"])
    pol = list(aneis(CONFIG / "bacia_iguacu.geojson"))
    dentro = dentro_da_bacia(pol)
    saida = []
    for f in gj.get("features", []):
        g = f.get("geometry") or {}
        linhas = g.get("paths") or []
        f["properties"] = f.get("attributes", {})
        nome_ana = f["properties"]["NORIOCOMP"]
        rotulo, classe = RIOS[nome_ana]
        for ln in linhas:
            pts = np.asarray(ln, dtype=float)
            if not len(pts):
                continue
            # trecho fica se a maioria dos vértices está dentro da bacia (o Iguaçu tem homônimos fora dela)
            n_in = sum(dentro(x, y) for x, y in pts[:: max(1, len(pts) // 20)])
            if n_in < max(1, len(pts[:: max(1, len(pts) // 20)]) * 0.6):
                continue
            saida.append({"type": "Feature", "properties": {"nome": rotulo, "classe": classe, "nome_ana": nome_ana,
                                                             "dominio": f["properties"].get("DEDOMINIAL"), "area_montante_km2": f["properties"].get("NUAREAMONT")},
                          "geometry": {"type": "LineString", "coordinates": np.round(pts, 4).tolist()}})
    out = {"type": "FeatureCollection", "fonte": CITACAO, "features": saida}
    (CONFIG / "hidrografia.geojson").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    log(f"{len(saida)} trechos gravados: {dict(Counter(f['properties']['nome'] for f in saida))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
