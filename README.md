# Painel Iguaçu

Acompanhamento hidrológico da bacia do rio Iguaçu em página estática (GitHub Pages), atualizado
automaticamente a cada hora por GitHub Actions, só com fontes públicas e sem credencial:

| Dado | Fonte | Coletor |
|---|---|---|
| Nível, defluência, turbinada, vertida, volume útil e vazão natural das 6 UHEs (G. B. Munhoz, Segredo, Salto Santiago, Salto Osório, Salto Caxias, Baixo Iguaçu) | ONS, Dados Abertos, parquets públicos no S3 (`dados_hidrologicos_ho` e `dados_hidrologicos_di`) | `coleta/ons.py` |
| Cota, vazão e chuva de 42 estações da Resolução Conjunta ANEEL/ANA nº 3 | ANA, webservice de telemetria `telemetriaws1.ana.gov.br` | `coleta/telemetria.py` |
| Chuva média da bacia e por célula (585 células de 0,1°), MLT 1998–2024 | INPE/CPTEC, produto MERGE (GRIB2 diário e climatologia NetCDF) | `coleta/merge.py` |

`coleta/monta_site.py` transforma `dados/` nos JSON de `docs/data/`, lidos pelas páginas de `docs/`.

**Painel técnico não oficial**, desenvolvido por Diego Liz Pena. Não é produto da ANA nem do ONS.
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

Só entram limites lidos em documento primário (outorga ou formulário FSAR-H). Hoje: Baixo Iguaçu
(Outorga ANA nº 2.382/2022; FSAR-H 9383/2026, 9382/2026, 9388/2026, 9389/2026) e Salto Caxias
(Outorga ANA nº 2.590/2019; FSAR-H 403/2018, 10844/2026). As demais usinas ficam sem linha de limite
até que suas outorgas sejam lidas para `config/condicionantes.yaml`.
