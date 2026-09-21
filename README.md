# Painel Iguaçu

Acompanhamento hidrológico da bacia do rio Iguaçu em página estática (GitHub Pages), atualizado
automaticamente a cada hora por GitHub Actions, só com fontes públicas e sem credencial:

| Dado | Fonte | Coletor |
|---|---|---|
| Nível, defluência, turbinada, vertida, volume útil e vazão natural das 6 UHEs (G. B. Munhoz, Segredo, Salto Santiago, Salto Osório, Salto Caxias, Baixo Iguaçu) | ONS, Dados Abertos, parquets públicos no S3 (`dados_hidrologicos_ho` e `dados_hidrologicos_di`) | `coleta/ons.py` |
| Cota, vazão e chuva de 42 estações da Resolução Conjunta ANEEL/ANA nº 3 | ANA, webservice de telemetria `telemetriaws1.ana.gov.br` | `coleta/telemetria.py` |
| Chuva média da bacia (ponderada pela área de 696 células de 0,1°) e por célula, MLT 1998–2024 | INPE/CPTEC, produto MERGE (GRIB2 diário e climatologia NetCDF) | `coleta/merge.py` |

`coleta/monta_site.py` transforma `dados/` nos JSON de `docs/data/`, lidos pelas páginas de `docs/`.

**Painel técnico não oficial.** Não é produto da ANA nem do ONS.
Exibe dado bruto e regra; não conclui descumprimento.

## Estrutura

```
config/      usinas.yaml, estacoes.csv, condicionantes.yaml (regras vigentes, com fonte), bacia_iguacu.geojson
coleta/      comum.py, geo.py, ons.py, telemetria.py, merge.py, monta_site.py
dados/       histórico em parquet/CSV commitado pelo fluxo (ons/, tele/, merge/, status/)
docs/        site estático: index, usina, estacoes, estacao, chuva, cataratas, fontes + data/
.github/workflows/atualiza.yml   fluxo horário
```

## Rodar localmente

```bash
pip install -r requirements.txt
python coleta/ons.py --desde 2025-01        # backfill horário (padrão: mês corrente e anterior)
python coleta/telemetria.py --dias 60       # padrão: 7 dias
python coleta/merge.py --desde 2026-01-01 --mlt
python coleta/monta_site.py
python -m http.server 8765 --directory docs
```

## Atualização automática

O workflow `atualiza.yml` roda por `repository_dispatch` (tipo `atualiza`), por `schedule` horário
(rede de segurança: em repositório gratuito o cron do Actions pode atrasar mais de 1 h) e manualmente
(`workflow_dispatch`, com opções de backfill). O disparo pontual vem de um cron externo (cron-job.org)
chamando:

```
POST https://api.github.com/repos/<usuario>/iguacu-painel/dispatches
Authorization: Bearer <token com permissão de conteúdo no repositório>
{"event_type": "atualiza"}
```

GitHub Pages serve a pasta `docs/` do branch `main`.

## Regras exibidas

Só entram limites lidos em documento primário (outorga ou formulário FSAR-H), cada um com a fonte em
`config/condicionantes.yaml`. As seis usinas estão cobertas:

| Usina | Outorga vigente | FSAR-H lidos |
|---|---|---|
| Foz do Areia (G. B. Munhoz) | 625/2025 | 10840, 10841, 11001 |
| Segredo | 332/2025 | 10842, 10843, 11004, 11032, 9359, 9360 |
| Salto Santiago | 556/2025 | 354, 472, 473, 474, 791, 1428, 4740, 10938, 10939, 10943, 10991, 11013 |
| Salto Osório | 254/2025 | 356, 357, 747, 793, 10945, 10946, 10948, 10989 |
| Salto Caxias | 2.590/2019 | 403/2018, 10844/2026 |
| Baixo Iguaçu | 2.382/2022 | 9383, 9382, 9388, 9389 |

Regra com `de:`/`ate:` (restrição temporária declarada ao ONS) sai sozinha do painel quando vence, pela
filtragem em `coleta/comum.py`. Regra com `condicional: true` é limite que a própria outorga prevê elevar
após termo aditivo ao contrato: continua desenhado, mas ultrapassá-lo gera aviso informativo, não de atenção.

## Adaptar para outra bacia

O código é genérico; o que é do Iguaçu está em poucos pontos. Para uma bacia nova, copiar o repositório e
trocar:

1. `config/bacia_<slug>.geojson` — polígono da bacia (SNIRH/ANA, camada Meso Região Hidrográfica, por `DME_CD`).
   O caminho aparece em `coleta/merge.py`, `coleta/hidrografia.py` e `coleta/monta_site.py`.
2. `config/usinas.yaml` — usinas do conjunto do ONS, com `ons`/`id_ons` exatamente como no parquet, tipo
   (acumulação ou fio d'água) e a estação de barramento de cada uma.
3. `coleta/ons.py` — filtro `nom_bacia == "IGUACU"`.
4. `config/estacoes.csv` — estações da Resolução Conjunta ANEEL/ANA nº 3 na bacia (código, nome, rio, área,
   lat/lon, responsável).
5. `config/trechos.yaml` — o eixo do painel: trechos de montante para jusante, o que chega e o que sai de
   cada um, papel de cada estação (montante, afluente, barramento, jusante), contexto em texto e pluviômetros.
6. `config/condicionantes.yaml` — outorgas e FSAR-H lidos, sempre com fonte; sem isso a usina fica sem linha
   de limite, que é o comportamento correto.
7. `coleta/hidrografia.py` — dicionário de rios a buscar no SNIRH (principal e afluentes monitorados).
8. Textos e centro do mapa em `docs/`: nome do painel em `app.js` (`montarTopo`, `LEGENDA_HIDRO`) e
   `mapa.setView([-25.7, -52.3], 7)` em `index.html`, `chuva.html` e `trecho.html`.

Caminho recomendado para virar um sistema de várias bacias: reunir esses pontos num `config/bacia.yaml`
(slug, nome, `nom_bacia` do ONS, geojson, centro e zoom do mapa, textos de identidade) e publicar uma bacia
por repositório ou uma pasta por bacia em `docs/`, mantendo `coleta/` compartilhado. O que muda de bacia
para bacia é sobretudo **o desenho do rio** (trechos.yaml) e **as regras** (condicionantes.yaml), não o código.
