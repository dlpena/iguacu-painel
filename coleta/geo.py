# -*- coding: utf-8 -*-
"""Máscara de grade por polígono e simplificação de contorno, só com numpy e matplotlib.path."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath


def aneis(geojson: Path):
    """Gera (exterior, [furos]) de cada polígono do GeoJSON, em lon/lat."""
    gj = json.loads(Path(geojson).read_text(encoding="utf-8"))
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for anel in polys:
            yield np.asarray(anel[0], dtype=float), [np.asarray(h, dtype=float) for h in anel[1:]]


def mascara(lats: np.ndarray, lons: np.ndarray, geojson: Path) -> np.ndarray:
    """True nas células (lat, lon) dentro do polígono. lons pode vir em 0-360 (MERGE diário vem em 240-340)."""
    lon_c = ((lons + 180) % 360) - 180
    pts = np.column_stack([lon_c.ravel(), lats.ravel()])
    m = np.zeros(lats.size, dtype=bool)
    for ext, furos in aneis(geojson):
        x0, y0 = ext.min(axis=0)
        x1, y1 = ext.max(axis=0)
        cand = np.where((pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1))[0]
        if not len(cand):
            continue
        dentro = MplPath(ext).contains_points(pts[cand])
        for furo in furos:
            dentro &= ~MplPath(furo).contains_points(pts[cand])
        m[cand[dentro]] = True
    return m.reshape(lats.shape)


def _dp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker iterativo."""
    n = len(pts)
    if n < 3:
        return pts
    manter = np.zeros(n, dtype=bool)
    manter[0] = manter[-1] = True
    pilha = [(0, n - 1)]
    while pilha:
        i, j = pilha.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = b - a
        comp = np.dot(seg, seg)
        p = pts[i + 1:j]
        if comp == 0:
            d = np.linalg.norm(p - a, axis=1)
        else:
            t = np.clip(((p - a) @ seg) / comp, 0, 1)
            d = np.linalg.norm(p - (a + t[:, None] * seg), axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            idx = i + 1 + k
            manter[idx] = True
            pilha.append((i, idx))
            pilha.append((idx, j))
    return pts[manter]


def simplificar(geojson: Path, saida: Path, tol: float = 0.004) -> int:
    """Grava um GeoJSON leve para o mapa (tol em graus; 0,004° ≈ 400 m). Devolve o nº de vértices."""
    gj = json.loads(Path(geojson).read_text(encoding="utf-8"))
    total = 0
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        novos = []
        for anel in polys:
            simp = [_dp(np.asarray(a, dtype=float), tol) for a in anel]
            simp = [np.round(s, 4).tolist() for s in simp if len(s) >= 4]
            if simp:
                total += sum(len(s) for s in simp)
                novos.append(simp)
        g["coordinates"] = novos if g["type"] == "MultiPolygon" else novos[0]
    Path(saida).write_text(json.dumps(gj, separators=(",", ":")), encoding="utf-8")
    return total
