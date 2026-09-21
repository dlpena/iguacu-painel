# -*- coding: utf-8 -*-
"""Pesos de área das células de grade num polígono, máscara por centro de célula e simplificação de contorno."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import shapely
from matplotlib.path import Path as MplPath
from shapely.geometry import shape
from shapely.ops import unary_union

# Média espacial "area-ponderada-1", a mesma da skill chuva-merge-bacias (merge_comum.py), desde 21/09/2026:
# peso da célula = fração da célula dentro do polígono (interseção exata) x área da célula no elipsoide WGS84.
METODO = "area-ponderada-1"
WGS84_A_KM, WGS84_F = 6378.137, 1 / 298.257223563


def ler_poligono(geojson: Path):
    """(geometria shapely unida e válida, área oficial em km² pelo atributo DME_AR_KM2 do SNIRH, ou None)."""
    gj = json.loads(Path(geojson).read_text(encoding="utf-8"))
    feicoes = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    geom = unary_union([shape(f["geometry"]) for f in feicoes])
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    areas = [(f.get("properties") or {}).get("DME_AR_KM2") for f in feicoes]
    return geom, (float(sum(areas)) if all(a is not None for a in areas) else None)


def area_faixa_km2(lat_sul, lat_norte, dlon_graus):
    """Área no elipsoide WGS84 do retângulo lat_sul..lat_norte x dlon (km²)."""
    e2 = WGS84_F * (2 - WGS84_F)
    e = np.sqrt(e2)
    b2 = (WGS84_A_KM * (1 - WGS84_F)) ** 2

    def q(fi):
        s = np.sin(np.radians(fi))
        return s / (2 * (1 - e2 * s * s)) + np.log((1 + e * s) / (1 - e * s)) / (4 * e)

    return b2 * np.radians(dlon_graus) * (q(lat_norte) - q(lat_sul))


def _bordas(centros: np.ndarray):
    d = float(np.median(np.abs(np.diff(centros))))
    return centros - d / 2, centros + d / 2, d


def pesos(lat: np.ndarray, lon: np.ndarray, geom) -> np.ndarray:
    """Matriz (nlat, nlon) de pesos em km²: fração da célula no polígono x área da célula. Zero fora.

    lat e lon são os vetores 1D de centros da grade; lon pode vir em 0-360 (MERGE diário vem em 240-340).
    """
    lon_c = ((np.asarray(lon, dtype=float) + 180) % 360) - 180
    lat = np.asarray(lat, dtype=float)
    la0, la1, _ = _bordas(lat)
    lo0, lo1, dlon = _bordas(lon_c)
    area_cel = area_faixa_km2(la0, la1, dlon)
    x0, y0, x1, y1 = geom.bounds
    ii = np.where((la1 >= y0) & (la0 <= y1))[0]
    jj = np.where((lo1 >= x0) & (lo0 <= x1))[0]
    W = np.zeros((lat.size, lon_c.size))
    if not len(ii) or not len(jj):
        return W
    I, J = np.meshgrid(ii, jj, indexing="ij")
    caixas = shapely.box(lo0[J], la0[I], lo1[J], la1[I])
    shapely.prepare(geom)
    frac = np.zeros(caixas.shape)
    dentro = shapely.contains(geom, caixas)
    frac[dentro] = 1.0
    borda = shapely.intersects(geom, caixas) & ~dentro
    frac[borda] = shapely.area(shapely.intersection(geom, caixas[borda])) / shapely.area(caixas[borda])
    W[I, J] = frac * area_cel[I]
    return W


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
