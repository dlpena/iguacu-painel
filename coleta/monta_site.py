# -*- coding: utf-8 -*-
"""Gera os JSON compactos que as páginas estáticas leem (docs/data/), a partir de dados/ e config/.

O eixo de organização é o rio (config/trechos.yaml): cada trecho reúne a usina e as réguas que a explicam.

Saídas:
  status.json           frescor e erros de cada fonte, citações
  bacia.json            trechos em ordem de rio, com usina, réguas (valor atual + referência de 30 d) e avisos
  trecho/<slug>.json    séries do trecho: usina (horário 90 d, diário 400 d, regras) e réguas (horário 90 d)
  estacoes.json         catálogo completo com trecho, papel, último dado, referências e sparkline
  estacao/<codigo>.json série bruta de 90 d de uma estação e chuva diária
  chuva.json            chuva média da bacia (MERGE) diária e mensal contra a MLT, grade das células, pluviômetros
  avisos.json           eventos das últimas 24 h que merecem olhar (o painel não conclui descumprimento)
  bacia.geojson         contorno simplificado da bacia para o mapa
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import CONFIG, DADOS, DOCS, agora_brt, estacoes, ler_status, log, regras, usinas  # noqa: E402
from geo import simplificar  # noqa: E402

SAIDA = DOCS / "data"
DIAS_HO, DIAS_DI, DIAS_TELE, DIAS_CHUVA = 90, 400, 90, 400
VARS_HO = {"val_nivelmontante": "nivel_montante", "val_niveljusante": "nivel_jusante", "val_vazaodefluente": "defluencia",
           "val_vazaoturbinada": "vazao_turbinada", "val_vazaovertida": "vazao_vertida", "val_vazaoafluente": "afluencia"}
VARS_DI = {"val_vazaonatural": "vazao_natural", "val_vazaoafluente": "afluencia", "val_vazaodefluente": "defluencia",
           "val_volumeutilcon": "pct_volume_util", "val_nivelmontante": "nivel_montante"}
MESES_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


# ------------------------------------------------------------------ utilitários
def lista(s, nd=2):
    return [None if pd.isna(v) else round(float(v), nd) for v in s]


def instantes(s, fmt="%Y-%m-%dT%H:%M"):
    return [t.strftime(fmt) for t in s]


def num(v, nd=2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def gravar(nome: str, obj) -> None:
    p = SAIDA / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def concat(padrao: str) -> pd.DataFrame:
    arqs = sorted(glob.glob(str(DADOS / padrao)))
    return pd.concat([pd.read_parquet(a) for a in arqs], ignore_index=True) if arqs else pd.DataFrame()


def horas_desde(t, agora):
    return None if t is None or pd.isna(t) else round((agora - t).total_seconds() / 3600, 1)


def valor_ha(g: pd.DataFrame, col: str, t_ref, horas: int):
    alvo = t_ref - timedelta(hours=horas)
    h = g[g["din_instante"] <= alvo]
    return h[col].iloc[-1] if len(h) else None


def trechos() -> list[dict]:
    return yaml.safe_load((CONFIG / "trechos.yaml").read_text(encoding="utf-8"))["trechos"]


# ------------------------------------------------------------------ usinas
def usina_atual(u, ho, di, agora):
    g = ho[ho["nom_reservatorio"] == u["ons"]].sort_values("din_instante")
    d = di[di["nom_reservatorio"] == u["ons"]].sort_values("din_instante")
    atual, tend, ref = {}, {}, {}
    if len(g):
        ult = g.iloc[-1]
        atual = {"instante": ult["din_instante"].strftime("%Y-%m-%dT%H:%M")}
        for col, var in VARS_HO.items():
            atual[var] = num(ult.get(col))
        for col, var in (("val_vazaodefluente", "defluencia"), ("val_nivelmontante", "nivel_montante")):
            antes = valor_ha(g, col, ult["din_instante"], 6)
            tend[var + "_6h"] = None if antes is None or pd.isna(antes) or pd.isna(ult[col]) else round(float(ult[col] - antes), 2)
        atual["frescor_h"] = horas_desde(ult["din_instante"], agora)
        j30 = g[g["din_instante"] > agora - timedelta(days=30)]
        ref = {"defluencia_media_30d": num(j30["val_vazaodefluente"].mean()), "defluencia_max_30d": num(j30["val_vazaodefluente"].max()),
               "defluencia_min_30d": num(j30["val_vazaodefluente"].min()),
               "nivel_min_30d": num(j30["val_nivelmontante"].min()), "nivel_max_30d": num(j30["val_nivelmontante"].max())}
    if len(d):
        ud = d.iloc[-1]
        atual["dia"] = ud["din_instante"].strftime("%Y-%m-%d")
        for col, var in VARS_DI.items():
            if var != "nivel_montante":
                atual[var + "_di"] = num(ud.get(col))
    return atual, tend, ref


def usina_detalhe(u, ho, di, tele, cat, regras_por_usina, montante):
    g = ho[ho["nom_reservatorio"] == u["ons"]].sort_values("din_instante")
    d = di[di["nom_reservatorio"] == u["ons"]].sort_values("din_instante")
    out = {"slug": u["slug"], "nome": u["nome"], "curto": u["curto"], "tipo": u["tipo"], "agente": u["agente"], "ons": u["ons"],
           "regras": regras_por_usina.get(u["slug"], []),
           "horario": {"instante": instantes(g["din_instante"]), **{var: lista(g[col]) for col, var in VARS_HO.items() if col in g}},
           "diario": {"data": instantes(d["din_instante"], "%Y-%m-%d"), **{var: lista(d[col]) for col, var in VARS_DI.items() if col in d}}}
    if montante is not None and len(g):
        m = ho[ho["nom_reservatorio"] == montante["ons"]].drop_duplicates("din_instante").set_index("din_instante")["val_vazaodefluente"]
        out["montante"] = {"slug": montante["slug"], "curto": montante["curto"], "defluencia": lista(m.reindex(g["din_instante"]).values)}
    return out


# ------------------------------------------------------------------ estações
def estacao_atual(cod, tele, agora):
    g = tele[tele["codigo"] == cod].sort_values("instante")
    if not len(g):
        return {}
    ult = g.iloc[-1]
    gc = g.dropna(subset=["cota_cm"])
    gv = g.dropna(subset=["vazao"])
    j30 = g[g["instante"] > agora - timedelta(days=30)]
    item = {"ultimo_instante": ult["instante"].strftime("%Y-%m-%dT%H:%M"), "frescor_h": horas_desde(ult["instante"], agora),
            "cota_m": num(gc["cota_cm"].iloc[-1] / 100, 3) if len(gc) else None,
            "vazao": num(gv["vazao"].iloc[-1]) if len(gv) else None,
            "chuva_24h": num(g.loc[g["instante"] > agora - timedelta(hours=24), "chuva_mm"].sum(), 1),
            "chuva_7d": num(g.loc[g["instante"] > agora - timedelta(days=7), "chuva_mm"].sum(), 1),
            "ref": {"vazao_media_30d": num(j30["vazao"].mean()), "vazao_max_30d": num(j30["vazao"].max()), "vazao_min_30d": num(j30["vazao"].min()),
                    "cota_media_30d": num(j30["cota_cm"].mean() / 100, 2) if j30["cota_cm"].notna().any() else None,
                    "chuva_30d": num(j30["chuva_mm"].sum(), 1)}}
    antes = g[g["instante"] <= ult["instante"] - timedelta(hours=6)]
    if len(antes) and pd.notna(ult["vazao"]) and antes["vazao"].notna().any():
        item["vazao_6h"] = num(ult["vazao"] - antes["vazao"].dropna().iloc[-1])
    sete = g[g["instante"] > agora - timedelta(days=7)].set_index("instante")
    if len(sete):
        if sete["vazao"].notna().any():
            sp = sete["vazao"].resample("6h").mean()
            item["spark"] = {"var": "vazao", "t": instantes(sp.index), "v": lista(sp)}
        elif sete["cota_cm"].notna().any():
            sp = sete["cota_cm"].resample("6h").mean() / 100
            item["spark"] = {"var": "cota_m", "t": instantes(sp.index), "v": lista(sp, 3)}
        else:
            sp = sete["chuva_mm"].resample("1D").sum()
            item["spark"] = {"var": "chuva_mm", "t": instantes(sp.index, "%Y-%m-%d"), "v": lista(sp, 1)}
    return item


def serie_horaria(cod, tele):
    g = tele[tele["codigo"] == cod].sort_values("instante")
    if not len(g):
        return {"instante": [], "vazao": [], "cota_m": []}
    h = g.set_index("instante")[["vazao", "cota_cm"]].resample("1h").mean()
    h = h[h.notna().any(axis=1)]
    return {"instante": instantes(h.index), "vazao": lista(h["vazao"]), "cota_m": lista(h["cota_cm"] / 100, 3)}


def chuva_diaria(cod, tele, dias=30):
    g = tele[tele["codigo"] == cod].sort_values("instante")
    g = g[g["instante"] > g["instante"].max() - timedelta(days=dias)] if len(g) else g
    if not len(g) or g["chuva_mm"].notna().sum() == 0:
        return None
    ch = g.set_index("instante")["chuva_mm"].resample("1D").sum(min_count=1)
    return {"data": instantes(ch.index, "%Y-%m-%d"), "mm": lista(ch, 1)}


# ------------------------------------------------------------------ chuva
def monta_chuva(tele, agora, est_json):
    pasta = DADOS / "merge"
    csv = pasta / "chuva_bacia_diaria.csv"
    out = {"meta": {"produto": "MERGE/INPE, grade 0,1°; o arquivo do dia D tem hora de referência 12 UTC (metadado do GRIB)"}}
    if not csv.exists():
        return out
    s = pd.read_csv(csv, parse_dates=["data"]).sort_values("data")
    s = s[s["data"] >= pd.Timestamp(agora.date() - timedelta(days=DIAS_CHUVA))]
    out["diaria"] = {"data": instantes(s["data"], "%Y-%m-%d"), "mm": lista(s["chuva_mm"], 1)}
    mlt = json.loads((pasta / "mlt.json").read_text(encoding="utf-8")) if (pasta / "mlt.json").exists() else {}
    mlt_mm = mlt.get("mlt_mm", {})
    men = s.groupby(s["data"].dt.to_period("M")).agg(mm=("chuva_mm", "sum"), dias=("chuva_mm", "size"))
    out["mensal"] = [{"mes": str(p), "mm": round(float(r.mm), 1), "dias": int(r.dias), "mlt": mlt_mm.get(MESES_EN[p.month - 1])}
                     for p, r in men.iterrows()]
    out["mlt"] = mlt
    ult = s["data"].max()

    def acum(n):
        return round(float(s.loc[s["data"] > ult - timedelta(days=n), "chuva_mm"].sum()), 1)

    mes_atual = s[s["data"].dt.to_period("M") == ult.to_period("M")]
    out["acumulados"] = {"ultimo_dia": ult.strftime("%Y-%m-%d"), "d1": acum(1), "d7": acum(7), "d30": acum(30),
                         "mes_atual": round(float(mes_atual["chuva_mm"].sum()), 1), "dias_mes": int(len(mes_atual)),
                         "mlt_mes": mlt_mm.get(MESES_EN[ult.month - 1])}
    if (pasta / "celulas.parquet").exists() and (pasta / "mascara.npz").exists():
        z = np.load(pasta / "mascara.npz")
        c = pd.read_parquet(pasta / "celulas.parquet")
        c["data"] = pd.to_datetime(c["data"])
        piv = c.pivot(index="data", columns="celula", values="mm").sort_index()
        grade = {"lat": lista(z["lat"], 2), "lon": lista(z["lon"], 2), "passo": 0.1}
        for n, k in ((1, "d1"), (7, "d7"), (30, "d30")):
            sub = piv[piv.index > ult - timedelta(days=n)]
            grade[k] = lista(sub.sum(axis=0).reindex(range(len(z["idx"]))), 1)
            grade[k + "_dias"] = int(len(sub))
        out["grade"] = grade
    plu = []
    grade = out.get("grade")
    for e in est_json:
        if e.get("chuva_7d") is None or e["lat"] is None:
            continue
        g = tele[(tele["codigo"] == e["codigo"]) & (tele["instante"] > agora - timedelta(days=30))]
        if g["chuva_mm"].notna().sum() == 0:
            continue
        item = {"codigo": e["codigo"], "nome": e["nome"], "curto": e.get("curto"), "tipo": e["tipo"], "papel": e.get("papel"), "lat": e["lat"], "lon": e["lon"],
                "trecho": e.get("trecho"), "ordem_trecho": e.get("ordem_trecho", 99), "frescor_h": e.get("frescor_h"),
                "mm_24h": e["chuva_24h"], "mm_7d": e["chuva_7d"], "mm_30d": num(g["chuva_mm"].sum(), 1), "ultimo_instante": e.get("ultimo_instante")}
        if grade:
            # célula do MERGE mais próxima do pluviômetro (grade de 0,1°): comparação satélite × observado
            la, lo = np.asarray(grade["lat"]), np.asarray(grade["lon"])
            k = int(np.argmin((la - e["lat"]) ** 2 + (lo - e["lon"]) ** 2))
            if (la[k] - e["lat"]) ** 2 + (lo[k] - e["lon"]) ** 2 <= 0.1 ** 2:
                item["merge"] = {"d1": grade["d1"][k], "d7": grade["d7"][k], "d30": grade["d30"][k], "celula": k}
        plu.append(item)
    out["pluviometros"] = plu
    return out


# ------------------------------------------------------------------ avisos
def monta_avisos(ho, di, est_json, agora, chuva, st, trecho_da_usina, trecho_da_estacao):
    av = []
    janela = ho[ho["din_instante"] > agora - timedelta(hours=24)] if len(ho) else ho
    us = {u["slug"]: u for u in usinas()}
    col_de = {v: k for k, v in VARS_HO.items()}
    for r in regras():
        u = us[r["usina"]]
        g = janela[janela["nom_reservatorio"] == u["ons"]].sort_values("din_instante") if len(janela) else janela
        col = col_de.get(r["variavel"])
        if not len(g) or not col or col not in g:
            continue
        s = g[col]
        base = {"usina": u["slug"], "trecho": trecho_da_usina.get(u["slug"]), "curto": u["curto"], "regra": r["titulo"], "fonte": r["fonte"], "id": r["id"]}
        var = r["variavel"].replace("_", " ")
        if r["tipo"] == "minimo":
            limite = r["valor"]
            nat = None
            if r.get("piso_natural") is not None:
                d = di[di["nom_reservatorio"] == u["ons"]].sort_values("din_instante")
                if len(d) and pd.notna(d["val_vazaonatural"].iloc[-1]):
                    nat = float(d["val_vazaonatural"].iloc[-1])
                    if nat < limite:
                        limite = max(nat, r["piso_natural"])
            tol = r.get("tolerancia", 0)
            zeros = s <= 5
            abaixo = (s < limite - tol) & ~zeros
            if zeros.any():
                av.append({**base, "nivel": "atencao", "quando": g.loc[zeros, "din_instante"].max().strftime("%Y-%m-%dT%H:%M"),
                           "texto": f"{int(zeros.sum())} h com {var} igual a zero nas últimas 24 h. Zero em série do ONS exige corroboração "
                                    f"(o ONS já registrou zeros espúrios nesta bacia)."})
            if abaixo.any():
                extra = f" (limite reduzido à vazão natural de {nat:.0f} m³/s do dia anterior)" if nat is not None and nat < r["valor"] else ""
                av.append({**base, "nivel": "atencao", "quando": g.loc[abaixo, "din_instante"].max().strftime("%Y-%m-%dT%H:%M"),
                           "texto": f"{int(abaixo.sum())} h com {var} abaixo de {limite:.0f} {r['unidade']} nas últimas 24 h (mínimo horário {s.min():.0f}){extra}."})
        elif r["tipo"] in ("maximo", "maximo_declarado"):
            acima = s > r["valor"]
            if acima.any():
                rot = "declarado ao ONS" if r["tipo"] == "maximo_declarado" else "da outorga"
                av.append({**base, "nivel": "atencao" if r["tipo"] == "maximo" else "info",
                           "quando": g.loc[acima, "din_instante"].max().strftime("%Y-%m-%dT%H:%M"),
                           "texto": f"{int(acima.sum())} h com {var} acima de {r['valor']} {r['unidade']} ({rot}) nas últimas 24 h (máximo horário {s.max():.2f})."})
        elif r["tipo"] == "rampa":
            dif = s.diff().abs()
            acima = dif > r["valor"]
            if acima.any():
                av.append({**base, "nivel": "info", "quando": g.loc[acima, "din_instante"].max().strftime("%Y-%m-%dT%H:%M"),
                           "texto": f"{int(acima.sum())} variação(ões) horária(s) de {var} acima de {r['valor']} {r['unidade']} (máxima {dif.max():.0f}). "
                                    f"Diferença de médias horárias é só indício, não mede a rampa instantânea."})
    for u in usinas():
        g = ho[ho["nom_reservatorio"] == u["ons"]] if len(ho) else ho
        h = horas_desde(g["din_instante"].max(), agora) if len(g) else None
        if h is None or h > 4:
            av.append({"nivel": "fonte", "usina": u["slug"], "trecho": trecho_da_usina.get(u["slug"]), "curto": u["curto"], "quando": agora.strftime("%Y-%m-%dT%H:%M"),
                       "texto": f"ONS sem dado horário de {u['curto']} há {h if h is not None else '?'} h.", "regra": "frescor", "fonte": "ONS dados abertos"})
    for e in est_json:
        if e.get("frescor_h") is not None and 6 < e["frescor_h"] <= 24 * 7:
            av.append({"nivel": "fonte", "estacao": e["codigo"], "trecho": trecho_da_estacao.get(e["codigo"]), "curto": e["nome"], "quando": e["ultimo_instante"],
                       "texto": f"Estação {e['codigo']} ({e['nome']}) sem transmitir há {e['frescor_h']} h.", "regra": "frescor", "fonte": "telemetria ANA"})
    ud = chuva.get("acumulados", {}).get("ultimo_dia")
    if ud and (agora.date() - pd.Timestamp(ud).date()).days > 1:
        av.append({"nivel": "fonte", "quando": ud, "texto": f"MERGE: último dia disponível {ud}.", "regra": "frescor", "fonte": "INPE"})
    for fonte in ("ons", "telemetria", "merge"):
        if st.get(fonte) and not st[fonte].get("ok", True):
            detalhe = "; ".join(map(str, st[fonte].get("erros") or st[fonte].get("falhas") or []))[:200]
            av.append({"nivel": "fonte", "quando": (st[fonte].get("atualizado_em") or "")[:16], "regra": "coleta",
                       "texto": f"Coleta {fonte}: falhou na última rodada ({detalhe}).", "fonte": fonte})
    ordem = {"atencao": 0, "info": 1, "fonte": 2}
    av.sort(key=lambda a: (ordem[a["nivel"]], a.get("quando", "")))
    return av


# ------------------------------------------------------------------ principal
def main() -> int:
    agora = agora_brt().replace(tzinfo=None)
    ho = concat("ons/ho_*.parquet")
    di = concat("ons/di_*.parquet")
    tele = concat("tele/*.parquet")
    if len(ho):
        ho = ho[ho["din_instante"] > agora - timedelta(days=DIAS_HO)]
    if len(di):
        di = di[di["din_instante"] > agora - timedelta(days=DIAS_DI)]
    if len(tele):
        tele = tele[tele["instante"] > agora - timedelta(days=DIAS_TELE)]
    else:
        tele = pd.DataFrame(columns=["codigo", "instante", "cota_cm", "vazao", "chuva_mm"])
    cat = estacoes()
    us = usinas()
    us_por_slug = {u["slug"]: u for u in us}
    tr = trechos()
    regras_por_usina = {}
    for r in regras():
        regras_por_usina.setdefault(r["usina"], []).append(
            {k: r.get(k) for k in ("id", "variavel", "tipo", "valor", "unidade", "titulo", "fonte", "vigencia", "nota", "piso_natural")})
    st = {f: ler_status(f) for f in ("ons", "telemetria", "merge")}
    carimbo = agora.strftime("%Y-%m-%dT%H:%M")

    # índice estação -> trecho/papel/curto
    info_est = {}
    for i, t in enumerate(tr):
        for e in t["estacoes"]:
            info_est[e["codigo"]] = {"trecho": t["slug"], "trecho_nome": t["nome"], "papel": e["papel"], "curto": e["curto"], "rio_afluente": e.get("rio"), "esquema": e.get("esquema", True), "ordem": i}
        for c in t.get("pluviometros", []):
            info_est.setdefault(c, {"trecho": t["slug"], "trecho_nome": t["nome"], "papel": "pluviometro", "curto": None, "rio_afluente": None, "ordem": i})
    trecho_da_usina = {t["usina"]: t["slug"] for t in tr if t.get("usina")}
    trecho_da_estacao = {c: v["trecho"] for c, v in info_est.items()}

    # ---- catálogo de estações
    est_json = []
    for _, r in cat.iterrows():
        cod = r["Codigo"]
        inf = info_est.get(cod, {})
        item = {"codigo": cod, "nome": r["Nome"], "curto": inf.get("curto") or r["Nome"].title(), "tipo": r["TipoEstacao"], "rio": r["Rio"], "municipio": r["Municipio"],
                "responsavel": r["ResponsavelSigla"], "operadora": r["OperadoraSigla"], "telemetrica": r["EstacaoTelemetrica"],
                "area_km2": num(r["AreaDrenagem"], 0), "lat": num(r["Latitude"], 4), "lon": num(r["Longitude"], 4),
                "trecho": inf.get("trecho"), "trecho_nome": inf.get("trecho_nome"), "papel": inf.get("papel"), "rio_afluente": inf.get("rio_afluente"), "esquema": inf.get("esquema", True),
                "ordem_trecho": inf.get("ordem", 99), "descricao": (r["Descricao"] or "")[:200]}
        item.update(estacao_atual(cod, tele, agora))
        est_json.append(item)
    est_por_cod = {e["codigo"]: e for e in est_json}
    gravar("estacoes.json", {"gerado_em": carimbo, "estacoes": est_json})
    for e in est_json:
        g = tele[tele["codigo"] == e["codigo"]].sort_values("instante")
        det = {k: v for k, v in e.items() if k != "spark"}
        det["serie"] = {"instante": instantes(g["instante"]), "cota_m": lista(g["cota_cm"] / 100, 3), "vazao": lista(g["vazao"])}
        det["chuva_diaria"] = chuva_diaria(e["codigo"], tele, 90)
        gravar(f"estacao/{e['codigo']}.json", det)

    # ---- chuva e avisos
    chuva = monta_chuva(tele, agora, est_json)
    gravar("chuva.json", {"gerado_em": carimbo, **chuva})
    avisos = monta_avisos(ho, di, est_json, agora, chuva, st, trecho_da_usina, trecho_da_estacao)
    gravar("avisos.json", {"gerado_em": carimbo, "avisos": avisos})

    # ---- bacia (esquema) e trechos
    bacia = []
    for i, t in enumerate(tr):
        u = us_por_slug.get(t.get("usina") or "")
        bloco = {"slug": t["slug"], "nome": t["nome"], "curto": t.get("curto", t["nome"]), "contexto": t["contexto"], "ordem": i, "usina": None,
                 "estacoes": [], "pluviometros": [], "avisos": [a for a in avisos if a.get("trecho") == t["slug"]],
                 "vizinhos": {"montante": tr[i - 1]["slug"] if i else None, "jusante": tr[i + 1]["slug"] if i + 1 < len(tr) else None}}
        if u:
            atual, tend, ref = usina_atual(u, ho, di, agora)
            rg = regras_por_usina.get(u["slug"], [])
            faixa = {"min": next((q["valor"] for q in rg if q["variavel"] == "nivel_montante" and q["tipo"] == "minimo"), None),
                     "max": next((q["valor"] for q in rg if q["variavel"] == "nivel_montante" and q["tipo"] == "maximo"), None),
                     "declarado": next((q["valor"] for q in rg if q["variavel"] == "nivel_montante" and q["tipo"] == "maximo_declarado"), None),
                     "q_min": next((q["valor"] for q in rg if q["variavel"] == "defluencia" and q["tipo"] == "minimo"), None)}
            bloco["usina"] = {**{k: u.get(k) for k in ("slug", "ons", "nome", "curto", "tipo", "agente", "lat", "lon", "estacao_barramento")},
                              "atual": atual, "tendencia": tend, "ref": ref, "faixa": faixa, "n_regras": len(rg)}
        for e in t["estacoes"]:
            base = est_por_cod.get(e["codigo"], {"codigo": e["codigo"], "nome": e["codigo"]})
            bloco["estacoes"].append({k: base.get(k) for k in ("codigo", "nome", "curto", "papel", "rio_afluente", "esquema", "area_km2", "lat", "lon", "responsavel",
                                                              "ultimo_instante", "frescor_h", "cota_m", "vazao", "vazao_6h", "chuva_24h", "chuva_7d", "ref", "spark")})
        for c in t.get("pluviometros", []):
            base = est_por_cod.get(c)
            if base:
                bloco["pluviometros"].append({k: base.get(k) for k in ("codigo", "nome", "lat", "lon", "responsavel", "ultimo_instante", "frescor_h", "chuva_24h", "chuva_7d", "ref")})
        bacia.append(bloco)
        # detalhe do trecho
        det = {"slug": t["slug"], "nome": t["nome"], "curto": t.get("curto", t["nome"]), "contexto": t["contexto"], "vizinhos": bloco["vizinhos"], "usina": None,
               "estacoes": [], "pluviometros": []}
        if u:
            idx = next(k for k, x in enumerate(us) if x["slug"] == u["slug"])
            det["usina"] = usina_detalhe(u, ho, di, tele, cat, regras_por_usina, us[idx - 1] if idx else None)
            det["usina"]["atual"], det["usina"]["tendencia"], det["usina"]["ref"] = bloco["usina"]["atual"], bloco["usina"]["tendencia"], bloco["usina"]["ref"]
        for e in bloco["estacoes"]:
            det["estacoes"].append({**{k: e.get(k) for k in ("codigo", "nome", "curto", "papel", "rio_afluente", "area_km2", "lat", "lon", "responsavel", "frescor_h", "ultimo_instante", "vazao", "cota_m", "ref")},
                                    "serie": serie_horaria(e["codigo"], tele)})
        for p in bloco["pluviometros"]:
            det["pluviometros"].append({**p, "chuva_diaria": chuva_diaria(p["codigo"], tele, 30)})
        gravar(f"trecho/{t['slug']}.json", det)
    gravar("bacia.json", {"gerado_em": carimbo, "trechos": bacia})

    ult_ons = st["ons"].get("ultimo_instante") or {}
    gravar("status.json", {
        "gerado_em": carimbo,
        "ons": {"ok": st["ons"].get("ok"), "atualizado_em": st["ons"].get("atualizado_em"), "erros": st["ons"].get("erros", []),
                "ultimo_instante": max(ult_ons.values()) if ult_ons else None},
        "telemetria": {"ok": st["telemetria"].get("ok"), "atualizado_em": st["telemetria"].get("atualizado_em"),
                       "com_dado": sum(1 for v in st["telemetria"].get("estacoes", {}).values() if v.get("n")),
                       "total": len(st["telemetria"].get("estacoes", {})), "erros": st["telemetria"].get("erros", [])},
        "merge": {"ok": st["merge"].get("ok"), "atualizado_em": st["merge"].get("atualizado_em"), "ultimo_dia": st["merge"].get("ultimo_dia"),
                  "falhas": st["merge"].get("falhas", [])},
        "citacoes": {"ons_ho": st["ons"].get("citacao_ho"), "ons_di": st["ons"].get("citacao_di"), "telemetria": st["telemetria"].get("citacao"),
                     "merge": st["merge"].get("citacao"), "mlt": chuva.get("mlt", {}).get("fonte")},
    })

    geo = SAIDA / "bacia.geojson"
    if not geo.exists() or geo.stat().st_mtime < (CONFIG / "bacia_iguacu.geojson").stat().st_mtime:
        n = simplificar(CONFIG / "bacia_iguacu.geojson", geo)
        log(f"bacia.geojson: {n} vértices, {geo.stat().st_size // 1024} KB")
    log(f"site montado: {len(bacia)} trechos, {len(est_json)} estações, {len(avisos)} avisos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
