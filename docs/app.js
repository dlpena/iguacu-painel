/* Funções compartilhadas do Painel Iguaçu (sem framework, sem build). */
const PAL = { escuro: '#294086', medio: '#0193DE', claro: '#80B5E1', marinho: '#000080', limite: '#C0504D',
              fsarh: '#E08A1E', cinza: '#6B7280', chuva: '#80B5E1', grade: '#E5E7EB' };
// usinas: só a defluência é exibida (a afluência horária do ONS é resíduo de balanço)
const SERIES = { nivel_montante: PAL.escuro, nivel_jusante: PAL.claro, defluencia: PAL.medio, vazao_turbinada: '#3FB0E8',
                 vazao_vertida: '#0B6BA8', afluencia: '#8C9BB5', vazao_natural: '#8C9BB5', pct_volume_util: PAL.escuro };
const ROT = { nivel_montante: 'Nível montante', nivel_jusante: 'Nível jusante', defluencia: 'Defluência', vazao_turbinada: 'Turbinada',
              vazao_vertida: 'Vertida', afluencia: 'Afluência (ONS, resíduo de balanço)', vazao_natural: 'Vazão natural (diária)',
              pct_volume_util: 'Volume útil (%)' };
const PAGINAS = [['index.html', 'O rio agora'], ['trecho.html?t=cataratas', 'Cataratas'], ['chuva.html', 'Chuva'],
                 ['catalogo.html', 'Catálogo'], ['fontes.html', 'Fontes e método']];
const PAPEL = { montante: 'estação fluviométrica a montante', afluente: 'afluente', barramento: 'estação fluviométrica no barramento', jusante: 'estação fluviométrica a jusante', pluviometro: 'pluviômetro' };

/* marcador de usina nos mapas: triângulo azul-escuro; rótulo fixo à direita ou só no hover */
const SVG_TRIANGULO = '<svg width="18" height="16" viewBox="0 0 18 16"><path d="M9 1 L17 15 L1 15 Z" fill="#294086" stroke="#fff" stroke-width="1.5"/></svg>';
function marcadorUsina(latlng, nome, rotuloFixo = false) {
  // âncora abaixo do triângulo: ele fica como um pino acima do ponto, sem cobrir a régua de barramento que tem a mesma coordenada
  const ic = L.divIcon({ className: '', iconSize: [18, 16], iconAnchor: [9, 26], html: SVG_TRIANGULO });
  const m = L.marker(latlng, { icon: ic, zIndexOffset: 500 });
  if (rotuloFixo) m.bindTooltip(nome, { permanent: true, direction: 'right', offset: [8, -18], className: 'rotulo-usina' });
  else m.bindTooltip(nome, { direction: 'top', offset: [0, -26] });
  return m;
}

/* esquema longitudinal do rio: usinas e réguas em ordem, com o valor da última hora */
function desenharEsquema(el, trechos, destaque = null) {
  const unico = trechos.length === 1;
  const COL = 56, MARG = unico ? 64 : 28, Y = 150, H = 262;
  const cols = []; // {tipo, x, ...}
  trechos.forEach(t => {
    const ini = cols.length;
    const principais = t.estacoes.filter(e => e.esquema !== false && e.papel !== 'barramento');
    const barr = t.estacoes.find(e => e.papel === 'barramento');
    let usinaInserida = false;
    principais.forEach(e => {
      if (e.papel === 'jusante' && t.usina && !usinaInserida) { cols.push({ tipo: 'usina', t, u: t.usina, barr }); usinaInserida = true; }
      cols.push({ tipo: e.papel === 'afluente' ? 'afluente' : 'regua', t, e });
    });
    if (t.usina && !usinaInserida) cols.push({ tipo: 'usina', t, u: t.usina, barr });
    if (!principais.length && !t.usina && barr) cols.push({ tipo: 'regua', t, e: barr });
    if (cols.length - ini < 2) cols.push({ tipo: 'vazio', t }); // faixa mínima de 2 colunas para caber o nome
    t._ini = ini; t._fim = cols.length;
  });
  const W = MARG * 2 + cols.length * COL;
  const x = i => W - (MARG + i * COL + COL / 2); // espelhado: cabeceira à direita, foz à esquerda, como no mapa
  const cor = h => ({ ok: '#2E8B57', aviso: '#D9A400', off: '#9CA3AF' }[frescorClasse(h)]);
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  // bacia inteira: ocupa a largura toda (com rolagem no celular); um trecho só: tamanho natural, sem esticar
  const estilo = trechos.length > 1 ? `width:100%;min-width:${Math.min(W, 1100)}px;height:auto` : `width:${W}px;max-width:100%;height:auto`;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" style="${estilo}" font-family="Questrial, Century Gothic, sans-serif">`;
  // faixas dos trechos
  trechos.forEach((t, k) => {
    const x1 = W - (MARG + t._ini * COL), x0 = W - (MARG + t._fim * COL);
    if (x1 <= x0) return;
    const sel = t.slug === destaque;
    s += `<a href="trecho.html?t=${t.slug}"><rect x="${x0}" y="0" width="${x1 - x0}" height="${H}" fill="${sel ? '#E3EEFB' : (k % 2 ? '#F1F5FA' : '#FFFFFF')}"><title>${esc(t.nome)}</title></rect>`;
    if (sel) s += `<rect x="${x0 + 1.5}" y="1.5" width="${x1 - x0 - 3}" height="${H - 3}" rx="6" fill="none" stroke="#294086" stroke-width="2.5"/>`;
    s += `<text x="${x0 + 6}" y="14" font-size="${sel ? 12.5 : 11.5}" fill="#294086" ${sel ? 'font-weight="bold"' : ''}>${esc(t.curto || t.nome)}${t.avisos && t.avisos.length ? ` <tspan fill="#E08A1E">●</tspan>` : ''}</text></a>`;
  });
  // rio
  s += `<path d="M${W - MARG} ${Y} H${MARG}" stroke="#80B5E1" stroke-width="6" stroke-linecap="round" fill="none"/>`;
  // setas do sentido do rio (leste -> oeste), uma por faixa de trecho
  trechos.forEach(t => { const xs = W - (MARG + t._fim * COL) + 10; if (t._fim > t._ini) s += `<path d="M${xs + 7} ${Y - 5} L${xs} ${Y} L${xs + 7} ${Y + 5}" fill="none" stroke="#294086" stroke-width="1.6" stroke-linecap="round"/>`; });
  s += unico
    ? `<text x="${W - 4}" y="${Y + 4}" font-size="10" fill="#6B7280" text-anchor="end">montante</text><text x="${4}" y="${Y + 4}" font-size="10" fill="#6B7280">← jusante</text>`
    : `<text x="${W - MARG}" y="${Y + 22}" font-size="10" fill="#6B7280" text-anchor="end">cabeceira (montante)</text><text x="${MARG}" y="${Y + 22}" font-size="10" fill="#6B7280">← sentido do rio · foz (jusante)</text>`;
  cols.forEach((c, i) => {
    const cx = x(i);
    if (c.tipo === 'vazio') {
      return;
    } else if (c.tipo === 'afluente') {
      // afluentes vizinhos alternam altura para os nomes (inclinados 20° para cima) não se cruzarem
      const e = c.e, alto = cols[i + 1] && cols[i + 1].tipo === 'afluente', cy = alto ? Y - 84 : Y - 50;
      s += `<a href="estacao.html?c=${e.codigo}"><path d="M${cx} ${cy} L${x(i + (alto ? 2 : 1)) - 4} ${Y - 3}" stroke="#80B5E1" stroke-width="2.5" fill="none" stroke-dasharray="3 3"/>`;
      s += `<circle cx="${cx}" cy="${cy}" r="6" fill="${cor(e.frescor_h)}" stroke="#fff" stroke-width="1.5"/>`;
      s += `<text x="${cx - 9}" y="${cy + 4}" font-size="11.5" text-anchor="end" fill="#1F2937">${fmt(e.vazao)}</text>`;
      s += `<text transform="translate(${cx + 8} ${cy - 2}) rotate(-20)" font-size="10" fill="#6B7280">${esc(e.curto)}</text>`;
      s += `<title>${esc(e.curto)} (${e.codigo}) · ${PAPEL[e.papel]}${e.rio_afluente ? ' · ' + e.rio_afluente : ''}\n${fmt(e.vazao)} m³/s · ${frescorTexto(e.frescor_h)}</title></a>`;
    } else if (c.tipo === 'regua') {
      const e = c.e;
      s += `<a href="estacao.html?c=${e.codigo}"><circle cx="${cx}" cy="${Y}" r="7" fill="${cor(e.frescor_h)}" stroke="#fff" stroke-width="2"/>`;
      s += `<text x="${cx}" y="${Y - 14}" font-size="12" text-anchor="middle" fill="#1F2937">${fmt(e.vazao !== null && e.vazao !== undefined ? e.vazao : null)}</text>`;
      s += `<text transform="translate(${cx + 3} ${Y + 30}) rotate(45)" font-size="10.5" fill="#374151">${esc(e.curto)}</text>`;
      s += `<title>${esc(e.curto)} (${e.codigo}) · ${PAPEL[e.papel]}\n${fmt(e.vazao)} m³/s${e.cota_m !== null && e.cota_m !== undefined ? ' · cota ' + fmt(e.cota_m, 2) + ' m' : ''} · ${frescorTexto(e.frescor_h)}${e.ref && e.ref.vazao_media_30d ? '\nmédia 30 d: ' + fmt(e.ref.vazao_media_30d) + ' m³/s' : ''}</title></a>`;
    } else {
      const u = c.u, a = u.atual || {};
      const dentro = u.tipo === 'acumulacao' ? `${fmt(a.pct_volume_util, 0)}%` : '▲';
      s += `<a href="trecho.html?t=${c.t.slug}"><rect x="${cx - 23}" y="${Y - 15}" width="46" height="30" rx="4" fill="#294086" stroke="${cor(a.frescor_h)}" stroke-width="2.5"/>`;
      s += `<text x="${cx}" y="${Y + 4}" font-size="11.5" text-anchor="middle" fill="#fff">${dentro}</text>`;
      s += `<text x="${cx}" y="${Y - 22}" font-size="12.5" text-anchor="middle" fill="#294086">${fmt(a.defluencia)}</text>`;
      s += `<text transform="translate(${cx + 3} ${Y + 30}) rotate(45)" font-size="11" fill="#294086">${esc(u.curto)}</text>`;
      s += `<title>${esc(u.nome)} (ONS ${fmtInst(a.instante)})\ndefluência ${fmt(a.defluencia)} m³/s · turbinada ${fmt(a.vazao_turbinada)} · vertida ${fmt(a.vazao_vertida)}\nnível montante ${fmt(a.nivel_montante, 2)} m${u.tipo === 'acumulacao' ? ' · volume útil ' + fmt(a.pct_volume_util, 1) + '%' : ''}${c.barr ? '\nestação de barramento ' + c.barr.codigo + ': ' + fmt(c.barr.vazao) + ' m³/s' : ''}</title></a>`;
    }
  });
  s += '</svg>';
  el.innerHTML = s;
}

function fmt(v, nd = 0) { return (v === null || v === undefined || Number.isNaN(v)) ? '–' : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: nd, maximumFractionDigits: nd }); }
function fmtInst(iso, comAno = false) { if (!iso) return '–'; const [d, h] = iso.split('T'); const [a, m, dd] = d.split('-'); return `${dd}/${m}${comAno ? '/' + a : ''}${h ? ' ' + h : ''}`; }
function fmtData(iso) { if (!iso) return '–'; const [a, m, d] = iso.slice(0, 10).split('-'); return `${d}/${m}/${a}`; }
function frescorClasse(h) { if (h === null || h === undefined) return 'off'; return h <= 2 ? 'ok' : (h <= 24 ? 'aviso' : 'off'); }
function frescorTexto(h) { if (h === null || h === undefined) return 'sem dado'; if (h < 1) return 'há menos de 1 h'; if (h < 48) return `há ${Math.round(h)} h`; return `há ${Math.round(h / 24)} d`; }
function param(nome, padrao) { return new URLSearchParams(location.search).get(nome) || padrao; }
async function carregar(caminho) {
  const v = Math.floor(Date.now() / 300000); // cache de 5 min
  const r = await fetch(`data/${caminho}?v=${v}`);
  if (!r.ok) throw new Error(`${caminho}: ${r.status}`);
  return r.json();
}
function tend(delta, nd = 0, unidade = '') {
  if (delta === null || delta === undefined) return '';
  const cls = Math.abs(delta) < (nd ? 0.005 : 0.5) ? 'igual' : (delta > 0 ? 'sobe' : 'desce');
  const seta = cls === 'igual' ? '→' : (delta > 0 ? '↑' : '↓');
  return `<span class="tend ${cls}" title="variação nas últimas 6 h">${seta} ${fmt(Math.abs(delta), nd)}${unidade}</span>`;
}

/* cabeçalho e rodapé */
function montarTopo(ativo, status) {
  const nav = PAGINAS.map(([h, t]) => `<a href="${h}" class="${h === ativo ? 'ativo' : ''}">${t}</a>`).join('');
  const logo = `<svg viewBox="0 0 60 40"><path d="M2 10 Q15 0 30 10 T58 10" fill="none" stroke="${PAL.claro}" stroke-width="5" stroke-linecap="round"/><path d="M2 20 Q15 10 30 20 T58 20" fill="none" stroke="${PAL.medio}" stroke-width="5" stroke-linecap="round"/><path d="M2 30 Q15 20 30 30 T58 30" fill="none" stroke="${PAL.escuro}" stroke-width="5" stroke-linecap="round"/></svg>`;
  let carimbo = '';
  if (status) {
    const o = status.ons || {}, t = status.telemetria || {}, m = status.merge || {};
    carimbo = `<div class="carimbo">
      <span>Gerado <b>${fmtInst(status.gerado_em)}</b></span>
      <span><span class="ponto ${o.ok ? 'ok' : 'off'}"></span>ONS até <b>${fmtInst(o.ultimo_instante)}</b></span>
      <span><span class="ponto ${t.ok ? 'ok' : 'off'}"></span>Telemetria: <b>${t.com_dado || 0}</b> de ${t.total || 0} estações</span>
      <span><span class="ponto ${m.ok ? 'ok' : 'off'}"></span>MERGE até <b>${fmtData(m.ultimo_dia)}</b></span></div>`;
  }
  document.getElementById('topo').innerHTML = `<div class="interno"><a class="marca" href="index.html">${logo}<div>Painel Iguaçu<small>acompanhamento hidrológico da bacia</small></div></a><nav class="menu">${nav}</nav>${carimbo}</div>`;
}
function montarRodape(status) {
  const c = (status && status.citacoes) || {};
  document.getElementById('rodape').innerHTML = `<div class="interno">
    <p><b>Painel técnico não oficial</b>, desenvolvido por Diego Liz Pena. Não é produto da Agência Nacional de Águas e Saneamento Básico nem do ONS. Os dados são brutos, sem consistência, e podem ser revisados pelas fontes. O painel exibe dado e regra; não conclui descumprimento.</p>
    <p>Fontes: ${c.ons_ho || 'ONS, Dados Abertos (base horária)'}; ${c.ons_di || 'ONS, Dados Abertos (base diária)'}; ${c.telemetria || 'ANA, webservice de telemetria'}; ${c.merge || 'INPE/CPTEC, MERGE'}; ${c.mlt || ''}.</p>
    <p>Limites de outorga e de FSAR-H lidos nos documentos primários (outorgas ANA nº 2.382/2022 e nº 2.590/2019; formulários FSAR-H lidos em 12/09/2026). Código e dados: <a href="https://github.com/dlpena/iguacu-painel">github.com/dlpena/iguacu-painel</a>.</p></div>`;
}
async function iniciar(ativo) {
  let status = null;
  try { status = await carregar('status.json'); } catch (e) { console.warn(e); }
  montarTopo(ativo, status);
  montarRodape(status);
  return status;
}

/* Plotly */
function layoutBase(extra = {}) {
  return Object.assign({
    font: { family: 'Questrial, Century Gothic, sans-serif', size: 12, color: '#1F2937' },
    paper_bgcolor: '#fff', plot_bgcolor: '#fff', margin: { l: 56, r: 16, t: 30, b: 40 },
    hovermode: 'x unified', showlegend: true,
    legend: { orientation: 'h', y: 1.12, x: 0, font: { size: 11 } },
    xaxis: { gridcolor: PAL.grade, zeroline: false, hoverformat: '%d/%m %H:%M' },
    yaxis: { gridcolor: PAL.grade, zeroline: false, fixedrange: false },
  }, extra);
}
const CONFIG_PLOT = { responsive: true, displaylogo: false, locale: 'pt-BR', modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                      toImageButtonOptions: { format: 'png', scale: 2 } };
function linhaLimite(y, texto, cor = PAL.limite, tracado = 'dash') {
  return { shape: { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: cor, width: 1.5, dash: tracado } },
           ann: { xref: 'paper', x: 1, y: y, text: texto, showarrow: false, xanchor: 'right', yanchor: 'bottom', font: { size: 10, color: cor }, bgcolor: 'rgba(255,255,255,.7)' } };
}
function traco(x, y, nome, cor, extra = {}) { return Object.assign({ x, y, name: nome, type: 'scatter', mode: 'lines', line: { color: cor, width: 1.6 }, connectgaps: false }, extra); }
function recorte(x, arrays, dias) {
  if (!dias || !x.length) return { x, arrays };
  const lim = new Date(x[x.length - 1]); lim.setDate(lim.getDate() - dias);
  const i0 = Math.max(0, x.findIndex(t => new Date(t) >= lim));
  const c = a => a ? a.slice(i0) : a;
  const out = {}; for (const k in arrays) out[k] = c(arrays[k]);
  return { x: x.slice(i0), arrays: out };
}

/* sparkline SVG */
function spark(vals, cor = PAL.medio, w = 110, h = 26) {
  const v = vals.filter(x => x !== null);
  if (v.length < 2) return '';
  const mn = Math.min(...v), mx = Math.max(...v), r = (mx - mn) || 1;
  const pts = vals.map((x, i) => x === null ? null : `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 2 - (x - mn) / r * (h - 4)).toFixed(1)}`).filter(Boolean).join(' ');
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${cor}" stroke-width="1.5"/></svg>`;
}

/* tabela ordenável */
function tabelaOrdenavel(tabela) {
  const ths = tabela.querySelectorAll('th');
  ths.forEach((th, i) => th.addEventListener('click', () => {
    const asc = th.dataset.ord !== 'asc';
    ths.forEach(t => delete t.dataset.ord); th.dataset.ord = asc ? 'asc' : 'desc';
    const linhas = Array.from(tabela.tBodies[0].rows);
    const val = tr => { const c = tr.cells[i]; const bruto = c.dataset.v ?? c.textContent; const n = parseFloat(bruto.replace(/\./g, '').replace(',', '.')); return Number.isNaN(n) ? bruto.toLowerCase() : n; };
    linhas.sort((a, b) => { const va = val(a), vb = val(b); if (va === vb) return 0; if (va === '' || va === '–') return 1; if (vb === '' || vb === '–') return -1; return (va > vb ? 1 : -1) * (asc ? 1 : -1); });
    linhas.forEach(l => tabela.tBodies[0].appendChild(l));
  }));
}

/* escala de cores para chuva (mm) */
function corChuva(mm, max) {
  if (mm === null || mm === undefined) return '#ddd';
  const t = Math.min(1, mm / (max || 1));
  const paradas = [[0, [246, 248, 251]], [.15, [128, 181, 225]], [.4, [1, 147, 222]], [.7, [41, 64, 134]], [1, [0, 0, 80]]];
  let a = paradas[0], b = paradas[paradas.length - 1];
  for (let i = 0; i < paradas.length - 1; i++) if (t >= paradas[i][0] && t <= paradas[i + 1][0]) { a = paradas[i]; b = paradas[i + 1]; break; }
  const f = (t - a[0]) / ((b[0] - a[0]) || 1);
  const c = a[1].map((x, i) => Math.round(x + (b[1][i] - x) * f));
  return `rgb(${c.join(',')})`;
}
