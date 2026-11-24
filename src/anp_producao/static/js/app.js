// Produção ANP por campo. Página única que conversa com a API local (/api).
// Todo texto vindo dos dados entra na página por textContent ou esc(), nunca como HTML cru.

// Paleta categórica validada (cobre primeiro) no fundo navy: faixa de luminosidade,
// croma mínimo, separação para daltonismo e contraste. Ordem fixa, nunca reciclada.
const PALETA = ["#C47A3D", "#009E98", "#BF5449", "#5289C6", "#C05E80", "#AF8D32", "#8C6EBD", "#5D9850"];
const COR_OUTROS = "#6B7A90";
const COR_TOTAL = "#F2F2F0";
const SUPERFICIE = "#132235";
const TOP_N = 8;
const MAX_ALVOS = 8;
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const TIPOS = {
  cluster: "Cluster", campo: "Campo", poco: "Poço", instalacao: "Instalação", bacia: "Bacia",
  estado: "Estado", operador: "Operador", empresa: "Sócia", ambiente: "Ambiente", todos: "Brasil",
};

const estado = {
  alvos: [],
  variavel: "petroleo",
  unidade: "",
  periodo: "60",
  inicio: "",
  fim: "",
  visao: "",
  liquidaDe: "",
  detalheQuebra: "poco",
  detalheMes: "",
};

let status = null;
let variaveis = [];
let clusters = [];
let filtros = null;
let resultado = null;
let grafico = null;
let pedidoSerie = 0;
let pedidoDetalhe = 0;
const coresEntidade = new Map();

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ------------------------------------------------------------------ utilidades
function esc(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function el(tag, atributos = {}, ...filhos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(atributos)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "style") n.setAttribute("style", v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) {
    if (f === null || f === undefined || f === false) continue;
    n.append(f instanceof Node ? f : document.createTextNode(String(f)));
  }
  return n;
}

function icone(nome) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icone");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${nome}`);
  svg.append(use);
  return svg;
}

function formatar(valor, casas = 0) {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "n/d";
  const abs = Math.abs(valor);
  let c = casas;
  if (casas === 0 && abs > 0 && abs < 10) c = abs < 1 ? 2 : 1;
  return new Intl.NumberFormat("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c }).format(valor);
}

function formatarEixo(valor) {
  if (valor === 0) return "0";
  const abs = Math.abs(valor);
  if (abs >= 1e9) return `${formatar(valor / 1e9, 1)} bi`;
  if (abs >= 1e6) return `${formatar(valor / 1e6, abs >= 1e7 ? 0 : 1)} mi`;
  if (abs >= 1e4) return `${formatar(valor / 1e3, 0)} mil`;
  if (abs >= 1000) return formatar(valor, 0);
  return formatar(valor, abs >= 10 ? 0 : abs >= 1 ? 1 : 2);
}

function percentual(valor, casas = 1) {
  if (valor === null || valor === undefined) return "n/d";
  const sinal = valor > 0 ? "+" : "";
  return `${sinal}${formatar(valor * 100, casas)}%`;
}

const rotuloMes = (iso) => (iso ? `${MESES[+iso.slice(5, 7) - 1]}/${iso.slice(0, 4)}` : "");
const rotuloMesCurto = (iso) => `${MESES[+iso.slice(5, 7) - 1]}/${iso.slice(2, 4)}`;

function somarMeses(iso, n) {
  const total = +iso.slice(0, 4) * 12 + (+iso.slice(5, 7) - 1) + n;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

function dataHora(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function debounce(fn, ms = 250) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => x !== "" && u.append(k, x));
    else u.append(k, v);
  }
  return u.toString();
}

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    ...opcoes,
    headers: { "Content-Type": "application/json", "X-ANP": "1", ...(opcoes.headers || {}) },
  });
  if (!resposta.ok) {
    let mensagem = resposta.statusText;
    try { mensagem = (await resposta.json()).erro || mensagem; } catch { /* resposta sem JSON */ }
    const erro = new Error(mensagem || `Erro ${resposta.status}`);
    erro.status = resposta.status;
    throw erro;
  }
  return resposta.json();
}

let timerToast;
function toast(mensagem, erro = false) {
  const t = $("#toast");
  t.textContent = mensagem;
  t.classList.toggle("erro", erro);
  t.classList.remove("oculto");
  clearTimeout(timerToast);
  timerToast = setTimeout(() => t.classList.add("oculto"), erro ? 8000 : 4500);
}

function baixar(url) {
  const a = el("a", { href: url, download: "" });
  document.body.append(a);
  a.click();
  a.remove();
}

function confirmarEmDoisCliques(botao, textoConfirmacao, acao) {
  if (botao.dataset.confirmando === "1") {
    botao.dataset.confirmando = "";
    botao.textContent = botao.dataset.textoOriginal;
    acao();
    return;
  }
  botao.dataset.textoOriginal = botao.textContent;
  botao.dataset.confirmando = "1";
  botao.textContent = textoConfirmacao;
  setTimeout(() => {
    if (botao.dataset.confirmando === "1") {
      botao.dataset.confirmando = "";
      botao.textContent = botao.dataset.textoOriginal;
    }
  }, 5000);
}

// --------------------------------------------------------------- estado e URL
function alvoDeId(id) {
  if (id === "todos") return { id: "todos", nome: "Brasil", tipo: "todos" };
  const i = id.indexOf(":");
  if (i < 0) return { id: `campo:${id}`, nome: id, tipo: "campo" };
  return { id, tipo: id.slice(0, i), nome: id.slice(i + 1) };
}

// Formato do link: #alvos=cluster:Polo%20X,campo:B%C3%9AZIOS&var=gas&un=mil%20m3%2Fd&per=60
// Cada alvo é codificado sozinho; a vírgula separa alvos. O mesmo formato sai de 'anp link'.
function estadoParaHash() {
  const partes = [["alvos", estado.alvos.map((a) => encodeURIComponent(a.id)).join(",")], ["var", estado.variavel]];
  if (estado.unidade) partes.push(["un", estado.unidade]);
  partes.push(["per", estado.periodo]);
  if (estado.periodo === "personalizado") {
    if (estado.inicio) partes.push(["ini", estado.inicio]);
    if (estado.fim) partes.push(["fim", estado.fim]);
  }
  if (estado.visao) partes.push(["visao", estado.visao]);
  if (estado.liquidaDe) partes.push(["liq", estado.liquidaDe]);
  return partes.map(([k, v]) => `${k}=${k === "alvos" ? v : encodeURIComponent(v)}`).join("&");
}

function aplicarHash(hash) {
  if (!hash) return false;
  const p = {};
  for (const par of hash.replace(/^#/, "").split("&")) {
    const i = par.indexOf("=");
    if (i > 0) p[par.slice(0, i)] = par.slice(i + 1);
  }
  if (!p.alvos) return false;
  const dec = (v) => { try { return decodeURIComponent(v || ""); } catch { return v || ""; } };
  estado.alvos = p.alvos.split(",").filter(Boolean).map((x) => alvoDeId(dec(x))).slice(0, MAX_ALVOS);
  estado.variavel = dec(p.var) || "petroleo";
  estado.unidade = dec(p.un);
  estado.inicio = dec(p.ini);
  estado.fim = dec(p.fim);
  estado.periodo = dec(p.per) || (estado.inicio || estado.fim ? "personalizado" : "60");
  estado.visao = dec(p.visao);
  estado.liquidaDe = dec(p.liq).split(",")[0];
  return true;
}

function salvarEstado() {
  const hash = estadoParaHash();
  if (location.hash.slice(1) !== hash) history.replaceState(null, "", `#${hash}`);
  try { localStorage.setItem("anp-ultimo-estado", hash); } catch { /* armazenamento indisponível */ }
}

// ------------------------------------------------------------------ status
function renderStatus() {
  const titulo = $("#status-titulo");
  const detalhe = $("#status-detalhe");
  const ponto = $("#status-base .ponto");
  if (!status) return;
  ponto.classList.toggle("vazio", status.base_vazia);
  titulo.textContent = status.base_vazia ? "Base vazia" : `Dados até ${status.ultimo_mes_rotulo}`;
  const ultima = status.ultima_atualizacao;
  detalhe.textContent = ultima?.concluida_em ? `base atualizada em ${dataHora(ultima.concluida_em)}` : "nunca atualizada";
  const dicas = [];
  if (status.pagina_anp_atualizada_em) dicas.push(`Página da ANP atualizada em ${dataHora(status.pagina_anp_atualizada_em)}`);
  if (status.proxima_publicacao_estimada) dicas.push(`Próxima publicação estimada para o ${status.proxima_publicacao_estimada}`);
  $("#status-base").title = dicas.join(". ");
  const rev = $("#btn-revisoes");
  rev.classList.toggle("oculto", !status.revisoes_nao_vistas);
  rev.querySelector("span").textContent = `${status.revisoes_nao_vistas} revisões da ANP`;
  $("#area-vazia").classList.toggle("oculto", !status.base_vazia);
  $("#area-dados").classList.toggle("oculto", status.base_vazia);
}

async function atualizarStatus() {
  status = await api("/api/status");
  renderStatus();
  return status;
}

let timerProgresso = null;
async function pedirAtualizacao(completa = false) {
  try {
    await api("/api/atualizar", { method: "POST", body: JSON.stringify({ completa }) });
  } catch (e) {
    if (e.status !== 409) { toast(e.message, true); return; }
  }
  acompanharAtualizacao();
}

function acompanharAtualizacao() {
  const idAntes = status?.ultima_atualizacao?.id ?? null;
  const tinhaDados = !status?.base_vazia;
  let ciclosParado = 0;
  let ciclos = 0;
  $("#progresso").classList.remove("oculto");
  $("#btn-atualizar").disabled = true;
  $("#progresso-texto").textContent = "Iniciando a atualização";
  $("#progresso-barra").style.width = "2%";
  clearInterval(timerProgresso);
  timerProgresso = setInterval(async () => {
    ciclos += 1;
    let s;
    try { s = await atualizarStatus(); } catch { return; }
    if (s.atualizacao_em_andamento) {
      ciclosParado = 0;
      if (s.progresso) {
        $("#progresso-barra").style.width = `${Math.max(2, s.progresso.progresso || 0)}%`;
        $("#progresso-texto").textContent = s.progresso.mensagem || "Atualizando";
      }
      if (!tinhaDados && !s.base_vazia && ciclos % 10 === 0) carregarTudo();
      return;
    }
    ciclosParado += 1;
    const terminou = s.ultima_atualizacao && s.ultima_atualizacao.id !== idAntes;
    if (terminou || ciclosParado > 10) {
      clearInterval(timerProgresso);
      $("#progresso").classList.add("oculto");
      $("#btn-atualizar").disabled = false;
      if (terminou) {
        const u = s.ultima_atualizacao;
        toast(u.status === "erro" ? `A atualização falhou: ${u.erro || u.mensagem}` : u.mensagem, u.status === "erro");
      } else {
        toast("A atualização não começou. Veja dados/logs/atualizacao.log na pasta do projeto.", true);
      }
      carregarTudo();
    }
  }, 1500);
}

// ---------------------------------------------------------------- catálogos
async function carregarClusters() {
  clusters = await api("/api/clusters");
  // nomes de clusters renomeados ou apagados saem da seleção
  renderClusters();
}

async function carregarFiltros() {
  filtros = await api("/api/filtros");
  const preencher = (sel, itens, primeiro) => {
    const atual = sel.value;
    sel.replaceChildren(el("option", { value: "", text: primeiro }), ...itens.map((i) => el("option", { value: i, text: i })));
    sel.value = itens.includes(atual) ? atual : "";
  };
  preencher($("#filtro-bacia"), filtros.bacias, "Todas as bacias");
  preencher($("#filtro-operador"), filtros.operadores, "Todos os operadores");
  $("#lista-empresas").replaceChildren(...filtros.empresas.map((e) => el("option", { value: e })));
  const atalhos = $("#atalhos");
  const grupo = (rotulo, tipo, itens) => el("optgroup", { label: rotulo }, ...itens.map((i) => el("option", { value: `${tipo}:${i}`, text: i })));
  atalhos.replaceChildren(
    el("option", { value: "", text: "Adicionar bacia, operador, empresa" }),
    el("option", { value: "todos", text: "Brasil (todos os campos)" }),
    grupo("Ambiente", "ambiente", ["MAR", "TERRA"]),
    grupo("Bacias", "bacia", filtros.bacias),
    grupo("Estados", "estado", filtros.estados),
    grupo("Operadores", "operador", filtros.operadores),
    grupo("Empresas sócias", "empresa", filtros.empresas),
  );
}

function montarSeletorVariavel() {
  const grupos = new Map();
  for (const v of variaveis) {
    if (!grupos.has(v.grupo)) grupos.set(v.grupo, []);
    grupos.get(v.grupo).push(el("option", { value: v.id, text: v.nome, title: v.descricao }));
  }
  $("#sel-variavel").replaceChildren(...[...grupos].map(([g, opcoes]) => el("optgroup", { label: g }, ...opcoes)));
}

function variavelAtual() {
  return variaveis.find((v) => v.id === estado.variavel) || variaveis[0];
}

function sincronizarControles() {
  const v = variavelAtual();
  estado.variavel = v.id;
  $("#sel-variavel").value = v.id;
  if (!v.unidades.some((u) => u.id === estado.unidade)) estado.unidade = v.unidade_padrao;
  $("#sel-unidade").replaceChildren(...v.unidades.map((u) => el("option", { value: u.id, text: u.rotulo })));
  $("#sel-unidade").value = estado.unidade;
  $("#sel-periodo").value = estado.periodo;
  $("#controle-datas").classList.toggle("oculto", estado.periodo !== "personalizado");
  if (estado.periodo === "personalizado") {
    $("#data-inicio").value = estado.inicio;
    $("#data-fim").value = estado.fim;
  }
  $("#sel-participacao").value = estado.liquidaDe;
  ajustarVisoes();
}

function visoesPermitidas() {
  if (estado.alvos.length !== 1) return [""];
  const tipo = estado.alvos[0].tipo;
  if (tipo === "poco") return [""];
  if (tipo === "campo") return ["", "poco", "instalacao"];
  if (tipo === "instalacao") return ["", "campo", "poco"];
  return ["", "campo", "poco", "instalacao"];
}

function ajustarVisoes() {
  const permitidas = visoesPermitidas();
  if (!permitidas.includes(estado.visao)) estado.visao = "";
  for (const b of $$("#seg-visao button")) {
    b.disabled = !permitidas.includes(b.dataset.visao);
    b.setAttribute("aria-pressed", String(b.dataset.visao === estado.visao));
  }
}

function periodoAtual() {
  const fim = status?.ultimo_mes;
  if (estado.periodo === "tudo" || !fim) return { inicio: "", fim: "" };
  if (estado.periodo === "personalizado") return { inicio: estado.inicio, fim: estado.fim };
  return { inicio: somarMeses(fim, -(+estado.periodo - 1)), fim: "" };
}

// ------------------------------------------------------------------ seleção
function estaSelecionado(id) {
  return estado.alvos.some((a) => a.id === id);
}

function selecionarUnico(alvo) {
  estado.alvos = [alvo];
  aoMudarSelecao();
}

function alternarAlvo(alvo) {
  if (estaSelecionado(alvo.id)) {
    estado.alvos = estado.alvos.filter((a) => a.id !== alvo.id);
  } else {
    if (estado.alvos.length >= MAX_ALVOS) { toast(`Compare até ${MAX_ALVOS} alvos por vez`, true); return; }
    estado.alvos = [...estado.alvos, alvo];
  }
  aoMudarSelecao();
}

function aoMudarSelecao() {
  estado.detalheMes = "";
  ajustarVisoes();
  renderSelecionados();
  renderClusters();
  marcarCamposSelecionados();
  carregarSerie();
}

function renderSelecionados() {
  const box = $("#selecionados");
  if (!estado.alvos.length) {
    box.replaceChildren(el("span", { class: "vazio-lista", text: "Escolha um cluster, campo ou recorte à esquerda. Marque mais de um para comparar." }));
    return;
  }
  box.replaceChildren(...estado.alvos.map((a) => el("span", { class: "chip" },
    el("span", { class: "tipo", text: TIPOS[a.tipo] || a.tipo }),
    el("span", { text: a.nome }),
    el("button", { type: "button", "aria-label": `Tirar ${a.nome}`, title: "Tirar da seleção", onclick: () => alternarAlvo(a), text: "×" }),
  )));
}

function renderClusters() {
  const ul = $("#lista-clusters");
  if (!clusters.length) {
    ul.replaceChildren(el("li", { class: "vazio-lista", text: "Nenhum cluster ainda. Crie o primeiro em Novo." }));
    return;
  }
  ul.replaceChildren(...clusters.map((c, i) => {
    const alvo = { id: `cluster:${c.nome}`, nome: c.nome, tipo: "cluster" };
    const ativo = estaSelecionado(alvo.id);
    const li = el("li", { class: `item${ativo ? " ativo" : ""}`, title: c.descricao || c.campos_resolvidos.slice(0, 12).join(", ") },
      el("input", { type: "checkbox", checked: ativo, "aria-label": `Comparar ${c.nome}`, onclick: (e) => { e.stopPropagation(); alternarAlvo(alvo); } }),
      el("span", { class: "nome" }, el("span", { class: "cor", style: `background:${c.cor || PALETA[i % PALETA.length]}` }), c.nome),
      el("span", { style: "display:flex; gap:6px; align-items:center" },
        el("span", { class: "meta", text: `${c.n_campos} ${c.n_campos === 1 ? "campo" : "campos"}` }),
        el("button", { type: "button", class: "btn-texto acao", title: "Editar cluster", "aria-label": `Editar ${c.nome}`, onclick: (e) => { e.stopPropagation(); abrirCluster(c); } }, icone("lapis")),
      ),
    );
    li.addEventListener("click", () => selecionarUnico(alvo));
    return li;
  }));
}

const buscarCampos = debounce(async () => {
  if (status?.base_vazia) { $("#lista-campos").replaceChildren(); return; }
  const r = await api(`/api/campos?${qs({ busca: $("#busca-campos").value, bacia: $("#filtro-bacia").value, operador: $("#filtro-operador").value, limite: 40 })}`);
  $("#total-campos").textContent = `${r.total} ${r.total === 1 ? "campo" : "campos"}`;
  const ul = $("#lista-campos");
  if (!r.campos.length) { ul.replaceChildren(el("li", { class: "vazio-lista", text: "Nenhum campo encontrado" })); return; }
  ul.replaceChildren(...r.campos.map((c) => {
    const alvo = { id: `campo:${c.nome}`, nome: c.nome, tipo: "campo" };
    const ativo = estaSelecionado(alvo.id);
    const meta = c.petroleo_bbl_d >= 1 ? `${formatar(c.petroleo_bbl_d)} bbl/d` : c.gas_mil_m3_d > 0 ? `${formatar(c.gas_mil_m3_d, 1)} mil m³/d` : "sem produção";
    const dica = [c.bacia, c.estado, c.operadores.join(", "), c.ambientes.join("/")].filter(Boolean).join(" · ");
    const li = el("li", { class: `item${ativo ? " ativo" : ""}`, title: dica, "data-id": alvo.id },
      el("input", { type: "checkbox", checked: ativo, "aria-label": `Comparar ${c.nome}`, onclick: (e) => { e.stopPropagation(); alternarAlvo(alvo); } }),
      el("span", { class: "nome", text: c.nome }),
      el("span", { class: "meta num", text: meta }),
    );
    li.addEventListener("click", () => selecionarUnico(alvo));
    return li;
  }));
});

function marcarCamposSelecionados() {
  for (const li of $$("#lista-campos li[data-id]")) {
    const ativo = estaSelecionado(li.dataset.id);
    li.classList.toggle("ativo", ativo);
    li.querySelector("input").checked = ativo;
  }
}

// ------------------------------------------------------------------- série
function parametrosSerie() {
  const { inicio, fim } = periodoAtual();
  return {
    alvo: estado.alvos.map((a) => a.id), variavel: estado.variavel, unidade: estado.unidade,
    inicio, fim, quebra: estado.visao, liquida_de: estado.liquidaDe ? [estado.liquidaDe] : [], top_n: TOP_N,
  };
}

async function carregarSerie() {
  salvarEstado();
  if (!status || status.base_vazia) return;
  if (!estado.alvos.length) {
    resultado = null;
    $("#kpis").replaceChildren();
    $("#titulo-grafico").textContent = "";
    if (grafico) grafico.clear();
    $("#avisos").replaceChildren();
    $("#tabela-mensal").replaceChildren();
    $("#cartao-detalhe").classList.add("oculto");
    return;
  }
  const meu = ++pedidoSerie;
  $("#cartao-grafico").classList.add("carregando");
  try {
    const r = await api(`/api/serie?${qs(parametrosSerie())}`);
    if (meu !== pedidoSerie) return;
    resultado = r;
    // nomes oficiais devolvidos pela API (ex.: 'gama' vira 'GAMA')
    r.alvos.forEach((a, i) => { if (estado.alvos[i]) estado.alvos[i] = { id: a.id, nome: a.nome, tipo: a.tipo }; });
    renderSelecionados();
    renderTitulo();
    renderKpis();
    renderGrafico();
    renderAvisos();
    renderTabelaMensal();
    salvarEstado();
    carregarDetalhe();
  } catch (e) {
    if (meu === pedidoSerie) toast(e.message, true);
  } finally {
    if (meu === pedidoSerie) $("#cartao-grafico").classList.remove("carregando");
  }
}

function renderTitulo() {
  const r = resultado;
  const partes = [r.alvos.map((a) => a.nome).join(" vs ")];
  if (r.quebra) partes.push({ campo: "por campo", poco: "por poço", instalacao: "por instalação" }[r.quebra]);
  if (r.liquida_de.length) partes.push(`parte de ${r.liquida_de.join(", ")}`);
  partes.push(`${rotuloMes(r.periodo.inicio)} a ${rotuloMes(r.periodo.fim)}`);
  $("#titulo-grafico").replaceChildren(
    el("span", { text: `${r.variavel.nome} (${r.unidade.rotulo})` }),
    el("small", { text: partes.join(" · ") }),
  );
}

function textoKpi(k) {
  if (k.valor === null || k.valor === undefined) return { valor: "n/d", unidade: "" };
  if (k.tipo === "percentual") return { valor: percentual(k.valor), unidade: "" };
  if (k.tipo === "pontos") return { valor: `${k.valor > 0 ? "+" : ""}${formatar(k.valor, 1)}`, unidade: "p.p." };
  const casas = k.id === "produtores" ? 0 : k.id.startsWith("acumulado") ? 2 : resultado.unidade.casas;
  return { valor: formatar(k.valor, casas), unidade: k.unidade || "" };
}

function renderKpis() {
  const box = $("#kpis");
  const r = resultado;
  if (r.alvos.length === 1) {
    const kpis = r.indicadores[r.alvos[0].id] || [];
    box.replaceChildren(...kpis.map((k) => {
      const t = textoKpi(k);
      let detalhe = k.detalhe || "";
      if (k.id === "pico" && k.fracao_atual !== null && k.fracao_atual !== undefined) detalhe += ` · hoje em ${formatar(k.fracao_atual * 100, 0)}% do pico`;
      const seta = (k.tipo === "percentual" || k.tipo === "pontos") && k.valor ? el("span", { class: "seta", "aria-hidden": "true", text: k.valor > 0 ? "▲" : "▼" }) : null;
      return el("div", { class: "kpi" },
        el("span", { class: "rotulo", text: k.rotulo }),
        el("div", { class: "valor num" }, seta, t.valor, el("span", { class: "unidade", text: t.unidade })),
        el("div", { class: "detalhe", text: detalhe }),
      );
    }));
    return;
  }
  const colunas = [["ultimo", "Último mês"], ["var_mensal", "Var. mensal"], ["var_anual", "Var. anual"], ["media_12m", "Média 12 meses"], ["acumulado_ano", "Acumulado no ano"]];
  const tabela = el("table", {},
    el("thead", {}, el("tr", {}, el("th", { text: "Alvo" }), ...colunas.map(([, r2]) => el("th", { text: r2 })))),
    el("tbody", {}, ...r.alvos.map((a) => {
      const kpis = Object.fromEntries((r.indicadores[a.id] || []).map((k) => [k.id, k]));
      return el("tr", {}, el("td", { class: "texto", text: a.nome }), ...colunas.map(([id]) => {
        const k = kpis[id];
        if (!k) return el("td", { text: "" });
        const t = textoKpi(k);
        return el("td", { text: `${t.valor} ${t.unidade}`.trim(), title: k.detalhe || "" });
      }));
    })),
  );
  box.replaceChildren(el("div", { class: "card tabela-kpis", style: "grid-column: 1 / -1; padding: 0; overflow:auto" }, tabela));
}

function renderAvisos() {
  $("#avisos").replaceChildren(...(resultado.avisos || []).map((a) => el("div", { class: "aviso" }, icone("alerta"), el("span", { text: a }))));
}

function atribuirCores(series) {
  const usadas = new Set();
  const cores = new Array(series.length);
  series.forEach((s, i) => {
    if (s.tipo === "outros") { cores[i] = COR_OUTROS; return; }
    if (s.tipo === "cluster" && series.length > 1) {
      const c = clusters.find((c2) => `cluster:${c2.nome}` === s.id);
      if (c?.cor && !usadas.has(c.cor)) { cores[i] = c.cor; usadas.add(c.cor); return; }
    }
    const anterior = coresEntidade.get(s.id);
    if (anterior && !usadas.has(anterior)) { cores[i] = anterior; usadas.add(anterior); }
  });
  series.forEach((s, i) => {
    if (cores[i]) return;
    const livre = PALETA.find((c) => !usadas.has(c)) || COR_OUTROS;
    cores[i] = livre;
    usadas.add(livre);
    coresEntidade.set(s.id, livre);
  });
  if (series.length === 1 && series[0].tipo === "cluster") {
    const c = clusters.find((c2) => `cluster:${c2.nome}` === series[0].id);
    if (c?.cor) cores[0] = c.cor;
  }
  return cores;
}

function iniciarGrafico() {
  if (grafico || !window.echarts) return;
  grafico = window.echarts.init($("#grafico"), null, { renderer: "canvas" });
  window.addEventListener("resize", () => grafico && grafico.resize());
}

function renderGrafico() {
  iniciarGrafico();
  if (!grafico) return;
  const r = resultado;
  const casas = r.unidade.casas;
  const pilha = Boolean(r.quebra) && r.variavel.tipo === "volume";
  const cores = atribuirCores(r.series);
  const meses = r.meses;
  const muitasSeries = r.series.length > 1;
  const rotular = !pilha && r.series.length <= 4;

  const series = r.series.map((s, i) => {
    const cor = cores[i];
    const base = {
      name: s.nome, type: "line", data: s.valores, connectNulls: false, showSymbol: false, symbol: "circle",
      symbolSize: 9, smooth: false, color: cor, emphasis: { focus: "series" },
      lineStyle: { width: 2, cap: "round", join: "round", color: cor },
      itemStyle: { color: cor, borderColor: SUPERFICIE, borderWidth: 2 },
    };
    if (pilha) {
      base.stack = "total";
      base.areaStyle = { color: cor, opacity: 0.9 };
      base.lineStyle = { width: 2, color: SUPERFICIE }; // espaço de 2px entre as faixas
    } else if (!muitasSeries) {
      base.areaStyle = { color: cor, opacity: 0.1 };
    }
    if (rotular) {
      base.endLabel = {
        show: true, color: "#F2F2F0", fontSize: 12, distance: 8,
        formatter: (p) => (muitasSeries ? `${p.seriesName}  ${formatar(p.value, casas)}` : formatar(p.value, casas)),
      };
      base.labelLayout = { moveOverlap: "shiftY" };
    }
    return base;
  });
  if (r.total && !pilha) {
    series.push({
      name: `Total ${r.total.nome}`, type: "line", data: r.total.valores, connectNulls: false, showSymbol: false,
      color: COR_TOTAL, lineStyle: { width: 2, color: COR_TOTAL }, z: 5,
    });
  }
  // Faixas dos meses que a ANP não publicou (ou publicou incompletos)
  const lacunas = new Set((r.lacunas || []).map((l) => l.mes));
  const completados = new Set((r.complementados || []).map((c) => c.mes));
  if (lacunas.size) {
    let maximo = 0;
    meses.forEach((_, i) => {
      const soma = pilha ? r.series.reduce((acc, s) => acc + (s.valores[i] || 0), 0) : Math.max(0, ...r.series.map((s) => s.valores[i] || 0));
      maximo = Math.max(maximo, soma);
    });
    series.push({
      name: "Sem dado da ANP", type: "bar", silent: true, barWidth: "100%", barGap: "-100%", z: 0,
      itemStyle: { color: "rgba(184, 192, 204, 0.08)", borderRadius: 0 }, tooltip: { show: false },
      data: meses.map((m) => (lacunas.has(m) ? maximo : null)),
    });
  }

  const legenda = muitasSeries || (r.total && !pilha);
  const comZoom = meses.length > 36;
  grafico.setOption({
    backgroundColor: "transparent",
    animationDuration: 300,
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: "#B8C0CC" },
    grid: { left: 8, right: rotular ? (muitasSeries ? 170 : 72) : 24, top: legenda ? 44 : 16, bottom: comZoom ? 58 : 28, containLabel: true },
    legend: legenda ? {
      type: "scroll", top: 0, left: 0, icon: "roundRect", itemWidth: 16, itemHeight: 4, itemGap: 16,
      textStyle: { color: "#B8C0CC", fontSize: 12 }, pageTextStyle: { color: "#B8C0CC" },
      data: series.filter((s) => s.name !== "Sem dado da ANP").map((s) => ({ name: s.name, itemStyle: { color: s.color }, lineStyle: { color: s.color } })),
    } : { show: false },
    tooltip: {
      trigger: "axis", confine: true, backgroundColor: "rgba(19, 34, 53, 0.96)", borderColor: "rgba(196, 122, 61, 0.35)",
      textStyle: { color: "#F2F2F0", fontSize: 12 }, padding: [10, 12],
      axisPointer: { type: "line", lineStyle: { color: "rgba(242, 242, 240, 0.35)", width: 1 } },
      formatter: (itens) => {
        const idx = itens[0]?.dataIndex ?? 0;
        const mes = meses[idx];
        const linhas = itens.filter((p) => p.seriesName !== "Sem dado da ANP");
        if (pilha) linhas.sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
        const corpo = linhas.map((p) => `<div style="display:flex;align-items:center;gap:8px;margin-top:4px">
            <span style="display:inline-block;width:14px;height:3px;border-radius:2px;background:${p.color}"></span>
            <b style="min-width:72px;text-align:right;font-variant-numeric:tabular-nums">${p.value === null || p.value === undefined ? "sem dado" : esc(formatar(p.value, casas))}</b>
            <span style="color:#B8C0CC">${esc(p.seriesName)}</span></div>`).join("");
        let total = "";
        if (pilha) {
          const soma = linhas.reduce((acc, p) => acc + (p.value || 0), 0);
          const faltou = linhas.some((p) => p.value === null || p.value === undefined);
          total = `<div style="border-top:1px solid rgba(184,192,204,0.2);margin-top:6px;padding-top:6px"><b>${faltou ? "incompleto" : esc(formatar(soma, casas))}</b> <span style="color:#B8C0CC">total</span></div>`;
        }
        const nota = lacunas.has(mes) ? `<div style="color:#E1A36A;margin-top:6px">Mês sem dado completo da ANP</div>`
          : completados.has(mes) ? `<div style="color:#B8C0CC;margin-top:6px">Completado com o arquivo de produção por zona da ANP</div>` : "";
        return `<div style="font-weight:600;margin-bottom:2px">${esc(rotuloMes(mes))} · ${esc(r.unidade.rotulo)}</div>${corpo}${total}${nota}`;
      },
    },
    xAxis: {
      type: "category", data: meses.map(rotuloMesCurto), boundaryGap: lacunas.size > 0,
      axisLine: { lineStyle: { color: "#2C3F5A" } }, axisTick: { show: false },
      axisLabel: { color: "rgba(184, 192, 204, 0.75)", fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value", min: r.variavel.tipo === "volume" ? 0 : null, scale: r.variavel.tipo !== "volume",
      splitLine: { lineStyle: { color: "#22344D", width: 1, type: "solid" } },
      axisLabel: { color: "rgba(184, 192, 204, 0.75)", fontSize: 11, formatter: formatarEixo },
    },
    dataZoom: comZoom ? [
      { type: "slider", height: 18, bottom: 8, borderColor: "#22344D", backgroundColor: "rgba(15,28,46,0.6)",
        fillerColor: "rgba(196, 122, 61, 0.16)", handleStyle: { color: "#C47A3D", borderColor: "#C47A3D" },
        moveHandleStyle: { color: "#C47A3D" }, textStyle: { color: "rgba(184,192,204,0.7)" },
        dataBackground: { lineStyle: { color: "#3A4E6B" }, areaStyle: { color: "rgba(58,78,107,0.3)" } },
        labelFormatter: (i) => rotuloMes(meses[i] || "") },
      { type: "inside", zoomOnMouseWheel: false, moveOnMouseMove: false, moveOnMouseWheel: false },
    ] : [],
    series,
  }, { notMerge: true });
  $("#grafico").setAttribute("aria-label", `${r.variavel.nome} em ${r.unidade.rotulo}, ${r.alvos.map((a) => a.nome).join(", ")}, ${rotuloMes(r.periodo.inicio)} a ${rotuloMes(r.periodo.fim)}`);
}

function renderTabelaMensal() {
  const r = resultado;
  const wrap = $("#tabela-mensal");
  if (wrap.classList.contains("oculto")) return;
  const series = [...r.series];
  if (r.total) series.push({ ...r.total, nome: `Total ${r.total.nome}` });
  const casas = r.unidade.casas;
  const lacunas = new Set((r.lacunas || []).map((l) => l.mes));
  const completados = new Set((r.complementados || []).map((c) => c.mes));
  const marca = (m) => (lacunas.has(m) ? " *" : completados.has(m) ? " †" : "");
  const dica = (m) => (lacunas.has(m) ? "Mês sem dado completo da ANP" : completados.has(m) ? "Completado com o arquivo de produção por zona da ANP" : null);
  const linhas = r.meses.map((m, i) => ({ m, i })).reverse();
  wrap.replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, el("th", { text: "Mês" }), ...series.map((s) => el("th", { text: s.nome })))),
    el("tbody", {}, ...linhas.map(({ m, i }) => el("tr", {},
      el("td", { text: rotuloMes(m) + marca(m), title: dica(m) }),
      ...series.map((s) => {
        const v = s.valores[i];
        return v === null || v === undefined ? el("td", { class: "sem-dado", text: "sem dado" }) : el("td", { text: formatar(v, casas) });
      }),
    ))),
  ));
}

// ----------------------------------------------------------------- detalhe
const COLUNAS_DETALHE = [
  ["nome", "Nome", "texto"], ["situacao", "Situação", "texto"], ["campos", "Campo", "texto"], ["instalacao", "Instalação", "texto"],
  ["petroleo_bbl_d", "Petróleo bbl/d", 0], ["gas_mil_m3_d", "Gás mil m³/d", 1], ["agua_bbl_d", "Água bbl/d", 0],
  ["boe_d", "Total boe/d", 0], ["participacao_boe", "% do total", "pct"], ["corte_agua_pct", "Corte de água %", 1],
  ["rgo_m3_m3", "RGO m³/m³", 0], ["var_mensal_petroleo", "Var. mensal", "pct"], ["var_anual_petroleo", "Var. anual", "pct"],
  ["inj_agua_m3_d", "Inj. água m³/d", 0], ["inj_gas_mil_m3_d", "Inj. gás mil m³/d", 1],
];
let detalhe = null;
let ordemDetalhe = { chave: "boe_d", desc: true };

function quebrasDetalhePermitidas() {
  if (estado.alvos.length !== 1) return [];
  const tipo = estado.alvos[0].tipo;
  if (tipo === "poco") return [];
  if (tipo === "campo") return ["poco", "instalacao"];
  if (tipo === "instalacao") return ["poco", "campo"];
  return ["poco", "instalacao", "campo"];
}

async function carregarDetalhe() {
  const cartao = $("#cartao-detalhe");
  const permitidas = quebrasDetalhePermitidas();
  if (!permitidas.length || !resultado) { cartao.classList.add("oculto"); return; }
  if (!permitidas.includes(estado.detalheQuebra)) estado.detalheQuebra = permitidas[0];
  for (const b of $$("#seg-detalhe button")) {
    b.disabled = !permitidas.includes(b.dataset.quebra);
    b.setAttribute("aria-pressed", String(b.dataset.quebra === estado.detalheQuebra));
  }
  cartao.classList.remove("oculto");
  const mes = estado.detalheMes || resultado.periodo.fim;
  $("#mes-detalhe").value = mes;
  const meu = ++pedidoDetalhe;
  try {
    const d = await api(`/api/detalhe?${qs({ alvo: estado.alvos[0].id, quebra: estado.detalheQuebra, mes, liquida_de: estado.liquidaDe ? [estado.liquidaDe] : [], limite: 1000 })}`);
    if (meu !== pedidoDetalhe) return;
    detalhe = d;
    renderDetalhe();
  } catch (e) {
    if (meu === pedidoDetalhe) { $("#tabela-detalhe").replaceChildren(); $("#resumo-detalhe").textContent = e.message; }
  }
}

function renderDetalhe() {
  const d = detalhe;
  const nomeQuebra = { poco: "poços", instalacao: "instalações", campo: "campos" }[d.quebra];
  $("#titulo-detalhe").textContent = `Detalhe por ${nomeQuebra.replace(/s$/, "").replace("õe", "ão")}`;
  const resumo = [`${d.total_linhas} ${nomeQuebra} com dado em ${d.rotulo_mes}.`,
    `Total de ${formatar(d.total.petroleo_bbl_d)} bbl/d de petróleo e ${formatar(d.total.gas_mil_m3_d, 1)} mil m³/d de gás.`,
    d.total_linhas > d.linhas.length ? `Mostrando os ${d.linhas.length} maiores.` : "",
    ...d.avisos].filter(Boolean).join(" ");
  $("#resumo-detalhe").textContent = resumo;
  const presentes = new Set(d.linhas.flatMap((l) => Object.keys(l)));
  const colunas = COLUNAS_DETALHE.filter(([k]) => presentes.has(k));
  const { chave, desc } = ordemDetalhe;
  const linhas = [...d.linhas].sort((a, b) => {
    const va = a[chave], vb = b[chave];
    if (typeof va === "string" || typeof vb === "string") return desc ? String(vb).localeCompare(String(va)) : String(va).localeCompare(String(vb));
    return desc ? (vb ?? -Infinity) - (va ?? -Infinity) : (va ?? Infinity) - (vb ?? Infinity);
  });
  const celula = (l, [k, , tipo]) => {
    const v = l[k];
    if (tipo === "texto") {
      if (k === "situacao") return el("td", { class: "texto" }, el("span", { class: "marcador", text: v }));
      return el("td", { class: "texto", text: v ?? "" });
    }
    if (v === null || v === undefined) return el("td", { class: "sem-dado", text: "n/d" });
    if (tipo === "pct") return el("td", { text: k === "participacao_boe" ? `${formatar(v * 100, 1)}%` : percentual(v) });
    return el("td", { text: formatar(v, tipo) });
  };
  const alvoDrill = (l) => ({ poco: `poco:${l.nome}`, instalacao: `instalacao:${l.nome}`, campo: `campo:${l.nome}` }[d.quebra]);
  $("#tabela-detalhe").replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, ...colunas.map((c) => el("th", {
      class: `ordenavel${c[2] === "texto" ? " texto" : ""}`, text: c[1] + (c[0] === chave ? (desc ? " ↓" : " ↑") : ""),
      onclick: () => { ordemDetalhe = { chave: c[0], desc: c[0] === chave ? !desc : c[2] !== "texto" }; renderDetalhe(); },
    })))),
    el("tbody", {}, ...linhas.map((l) => el("tr", {
      class: "clicavel", title: "Ver a série deste item",
      onclick: () => selecionarUnico(alvoDeId(alvoDrill(l))),
    }, ...colunas.map((c) => celula(l, c))))),
  ));
}

// ------------------------------------------------------------------ clusters
let edicao = null;

function abrirCluster(cluster = null) {
  if (!filtros) { toast("Atualize a base antes de criar clusters", true); return; }
  edicao = {
    original: cluster?.nome || null,
    cor: cluster?.cor || "",
    incluir: [...(cluster?.incluir || [])],
    excluir: [...(cluster?.excluir || [])],
    regra: JSON.parse(JSON.stringify(cluster?.regra || {})),
  };
  $("#titulo-modal-cluster").textContent = cluster ? `Editar ${cluster.nome}` : "Novo cluster";
  $("#cl-nome").value = cluster?.nome || "";
  $("#cl-descricao").value = cluster?.descricao || "";
  $("#cl-ativos").checked = Boolean(edicao.regra.somente_ativos);
  $("#cl-busca").value = "";
  $("#cl-resultados").replaceChildren();
  $("#cl-apagar").classList.toggle("oculto", !cluster);
  renderCores();
  renderMultis();
  renderChipsCluster();
  atualizarPrevia();
  $("#modal-cluster").showModal();
  $("#cl-nome").focus();
}

function renderCores() {
  const opcoes = ["", ...PALETA];
  $("#cl-cores").replaceChildren(...opcoes.map((c) => el("button", {
    type: "button", title: c ? c : "Automática", "aria-label": c ? `Cor ${c}` : "Cor automática",
    "aria-pressed": String(edicao.cor === c),
    style: c ? `background:${c}` : "background: repeating-linear-gradient(45deg, #22344D, #22344D 4px, #1A2A3F 4px, #1A2A3F 8px)",
    onclick: () => { edicao.cor = c; renderCores(); },
  })));
}

function renderMultis() {
  for (const box of $$("#form-cluster .multi")) {
    const criterio = box.dataset.criterio;
    const opcoes = filtros[criterio] || [];
    const marcados = new Set(edicao.regra[criterio] || []);
    const itens = opcoes.map((o) => el("label", { "data-texto": o.toLowerCase() },
      el("input", { type: "checkbox", value: o, checked: marcados.has(o), onchange: (e) => {
        const set = new Set(edicao.regra[criterio] || []);
        if (e.target.checked) set.add(o); else set.delete(o);
        edicao.regra[criterio] = [...set];
        atualizarPrevia();
      } }),
      el("span", { text: o }),
    ));
    const busca = opcoes.length > 8 ? el("input", { class: "campo-texto busca-multi", type: "search", placeholder: "Filtrar", oninput: (e) => {
      const t = e.target.value.toLowerCase();
      itens.forEach((i) => { i.style.display = i.dataset.texto.includes(t) ? "" : "none"; });
    } }) : null;
    box.replaceChildren(...[busca, ...itens].filter(Boolean));
  }
}

function renderChipsCluster() {
  const chip = (nome, aoTirar) => el("span", { class: "chip" }, el("span", { text: nome }),
    el("button", { type: "button", "aria-label": `Tirar ${nome}`, onclick: aoTirar, text: "×" }));
  $("#cl-incluidos").replaceChildren(...(edicao.incluir.length ? edicao.incluir.map((n) => chip(n, () => {
    edicao.incluir = edicao.incluir.filter((x) => x !== n); renderChipsCluster(); atualizarPrevia();
  })) : [el("span", { class: "vazio-lista", text: "Nenhum campo escolhido à mão" })]));
  $("#cl-excluidos").replaceChildren(...(edicao.excluir.length ? edicao.excluir.map((n) => chip(n, () => {
    edicao.excluir = edicao.excluir.filter((x) => x !== n); renderChipsCluster(); atualizarPrevia();
  })) : [el("span", { class: "vazio-lista", text: "Nenhum. Para tirar um campo que a regra puxa, use o × na prévia abaixo." })]));
}

function regraAtual() {
  const r = { ...edicao.regra, somente_ativos: $("#cl-ativos").checked };
  for (const k of ["operadores", "empresas", "bacias", "estados", "ambientes", "situacoes"]) r[k] = r[k] || [];
  return r;
}

const atualizarPrevia = debounce(async () => {
  const box = $("#cl-previa");
  try {
    const p = await api("/api/clusters/previa", { method: "POST", body: JSON.stringify({ regra: regraAtual(), incluir: edicao.incluir, excluir: edicao.excluir }) });
    const lista = el("div", { class: "lista-chips lista-previa" }, ...p.campos.map((n) => el("span", { class: "chip" }, el("span", { text: n }),
      el("button", { type: "button", "aria-label": `Excluir ${n}`, title: "Excluir do cluster", text: "×", onclick: () => {
        if (edicao.incluir.includes(n)) edicao.incluir = edicao.incluir.filter((x) => x !== n);
        else if (!edicao.excluir.includes(n)) edicao.excluir = [...edicao.excluir, n];
        renderChipsCluster(); atualizarPrevia();
      } }))));
    box.replaceChildren(el("strong", { text: `Este cluster terá ${p.n_campos} campo${p.n_campos === 1 ? "" : "s"}` }),
      p.nao_encontrados.length ? el("div", { text: `Sem dados na base: ${p.nao_encontrados.join(", ")}` }) : null, lista);
  } catch (e) {
    box.textContent = e.message;
  }
}, 300);

const buscarCamposCluster = debounce(async () => {
  const termo = $("#cl-busca").value.trim();
  if (!termo) { $("#cl-resultados").replaceChildren(); return; }
  const r = await api(`/api/campos?${qs({ busca: termo, limite: 12 })}`);
  $("#cl-resultados").replaceChildren(...r.campos.map((c) => el("li", { class: "item", onclick: () => {
    if (!edicao.incluir.includes(c.nome)) edicao.incluir = [...edicao.incluir, c.nome];
    edicao.excluir = edicao.excluir.filter((x) => x !== c.nome);
    renderChipsCluster(); atualizarPrevia();
  } }, icone("mais"), el("span", { class: "nome", text: c.nome }), el("span", { class: "meta", text: [c.bacia, c.operadores[0]].filter(Boolean).join(" · ") }))));
});

async function salvarCluster() {
  const nome = $("#cl-nome").value.trim();
  if (!nome) { toast("Dê um nome ao cluster", true); $("#cl-nome").focus(); return; }
  const corpo = { descricao: $("#cl-descricao").value.trim(), cor: edicao.cor || null, regra: regraAtual(), incluir: edicao.incluir, excluir: edicao.excluir };
  try {
    let salvo;
    if (edicao.original) {
      salvo = await api(`/api/clusters/${encodeURIComponent(edicao.original)}`, { method: "PUT", body: JSON.stringify({ ...corpo, novo_nome: nome, cor: edicao.cor }) });
      estado.alvos = estado.alvos.map((a) => (a.id === `cluster:${edicao.original}` ? { id: `cluster:${salvo.nome}`, nome: salvo.nome, tipo: "cluster" } : a));
    } else {
      salvo = await api("/api/clusters", { method: "POST", body: JSON.stringify({ nome, ...corpo }) });
      estado.alvos = [{ id: `cluster:${salvo.nome}`, nome: salvo.nome, tipo: "cluster" }];
    }
    $("#modal-cluster").close();
    await carregarClusters();
    toast(`Cluster ${salvo.nome} salvo com ${salvo.n_campos} campos`);
    aoMudarSelecao();
  } catch (e) {
    toast(e.message, true);
  }
}

async function apagarCluster() {
  try {
    await api(`/api/clusters/${encodeURIComponent(edicao.original)}`, { method: "DELETE" });
    estado.alvos = estado.alvos.filter((a) => a.id !== `cluster:${edicao.original}`);
    $("#modal-cluster").close();
    await carregarClusters();
    toast("Cluster apagado. Uma cópia fica em dados/backups.");
    aoMudarSelecao();
  } catch (e) {
    toast(e.message, true);
  }
}

async function importarClusters(arquivo) {
  try {
    const dados = JSON.parse(await arquivo.text());
    const r = await api("/api/clusters/importar", { method: "POST", body: JSON.stringify(dados) });
    await carregarClusters();
    toast(`Importação: ${r.criados} criados, ${r.atualizados} atualizados${r.erros.length ? `, ${r.erros.length} com erro` : ""}`, r.erros.length > 0);
  } catch (e) {
    toast(`Não consegui importar: ${e.message}`, true);
  }
}

// ---------------------------------------------------------- revisões e histórico
async function abrirRevisoes() {
  const r = await api("/api/revisoes?limite=500");
  const tabela = el("table", {},
    el("thead", {}, el("tr", {}, ...["Campo", "Mês", "Variável", "Antes", "Depois", "Variação", "Detectada em"].map((t, i) => el("th", { text: t, class: i < 3 ? "texto" : null })))),
    el("tbody", {}, ...r.itens.map((i) => el("tr", {},
      el("td", { class: "texto", text: i.campo }), el("td", { class: "texto", text: i.rotulo_mes }), el("td", { class: "texto", text: `${i.variavel} (${i.unidade})` }),
      el("td", { text: formatar(i.antes, 1) }), el("td", { text: formatar(i.depois, 1) }), el("td", { text: percentual(i.variacao) }),
      el("td", { text: dataHora(i.detectada_em) }),
    ))),
  );
  $("#tabela-revisoes").replaceChildren(r.itens.length ? tabela : el("p", { style: "padding: 12px", text: "Nenhuma revisão registrada." }));
  $("#modal-revisoes").showModal();
}

async function abrirHistorico() {
  const lista = await api("/api/atualizacoes?limite=30");
  const nomes = { ok: "concluída", ok_com_avisos: "concluída com avisos", erro: "falhou", executando: "em andamento", interrompida: "interrompida" };
  const info = [];
  if (status?.pagina_anp_atualizada_em) info.push(`Página da ANP atualizada em ${dataHora(status.pagina_anp_atualizada_em)}.`);
  if (status?.proxima_publicacao_estimada) info.push(`Próxima publicação estimada para o ${status.proxima_publicacao_estimada}.`);
  if (status?.lacunas_recentes?.length) info.push(`Meses que a ANP não publicou ou publicou incompletos nos últimos 24 meses: ${status.lacunas_recentes.map((l) => `${l.rotulo} (${l.ambiente.toLowerCase()}, ${l.tipo})`).join(", ")}.`);
  if (status?.complementos_recentes?.length) info.push(`Completados com o arquivo de produção por zona da ANP nos últimos 24 meses, ${status.complementos_recentes.map((c) => `${c.rotulo} (${c.ambiente.toLowerCase()}, ${Number(c.pocos).toLocaleString("pt-BR")} poços)`).join(", ")}.`);
  $("#info-fonte").textContent = info.join(" ");
  $("#tabela-historico").replaceChildren(lista.length ? el("table", {},
    el("thead", {}, el("tr", {}, ...["Início", "Fim", "Origem", "Situação", "Resultado"].map((t, i) => el("th", { text: t, class: i >= 2 ? "texto" : null })))),
    el("tbody", {}, ...lista.map((a) => el("tr", {},
      el("td", { text: dataHora(a.iniciada_em) }), el("td", { text: dataHora(a.concluida_em) }), el("td", { class: "texto", text: a.origem || "" }),
      el("td", { class: "texto", text: nomes[a.status] || a.status }), el("td", { class: "quebra-linha", text: a.erro ? `${a.mensagem || ""} ${a.erro}` : a.mensagem || "" }),
    ))),
  ) : el("p", { style: "padding: 12px", text: "Nenhuma atualização ainda." }));
  $("#modal-historico").showModal();
}

// ----------------------------------------------------------------- eventos
function ligarEventos() {
  $("#btn-atualizar").addEventListener("click", () => pedirAtualizacao(false));
  $$("[data-acao=atualizar]").forEach((b) => b.addEventListener("click", () => pedirAtualizacao(false)));
  $("#btn-atualizar-completa").addEventListener("click", (e) => confirmarEmDoisCliques(e.currentTarget, "Confirmar: baixar tudo", () => {
    $("#modal-historico").close();
    pedirAtualizacao(true);
  }));
  $("#btn-historico").addEventListener("click", abrirHistorico);
  $("#btn-revisoes").addEventListener("click", abrirRevisoes);
  $("#btn-revisoes-vistas").addEventListener("click", async () => {
    await api("/api/revisoes/vistas", { method: "POST" });
    $("#modal-revisoes").close();
    await atualizarStatus();
  });
  $$("dialog [data-fechar]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  $("#sel-variavel").addEventListener("change", (e) => { estado.variavel = e.target.value; estado.unidade = ""; sincronizarControles(); carregarSerie(); });
  $("#sel-unidade").addEventListener("change", (e) => { estado.unidade = e.target.value; carregarSerie(); });
  $("#sel-periodo").addEventListener("change", (e) => {
    estado.periodo = e.target.value;
    if (estado.periodo === "personalizado" && !estado.inicio && resultado) { estado.inicio = resultado.periodo.inicio; estado.fim = resultado.periodo.fim; }
    sincronizarControles();
    carregarSerie();
  });
  $("#data-inicio").addEventListener("change", (e) => { estado.inicio = e.target.value; carregarSerie(); });
  $("#data-fim").addEventListener("change", (e) => { estado.fim = e.target.value; carregarSerie(); });
  $$("#seg-visao button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    estado.visao = b.dataset.visao;
    ajustarVisoes();
    carregarSerie();
  }));
  $("#sel-participacao").addEventListener("change", (e) => {
    const valor = e.target.value.trim();
    if (!valor) { estado.liquidaDe = ""; carregarSerie(); return; }
    const achada = (filtros?.empresas || []).find((x) => x.toLowerCase() === valor.toLowerCase());
    if (!achada) { toast("Empresa não encontrada no cadastro de concessionários da ANP", true); e.target.value = estado.liquidaDe; return; }
    estado.liquidaDe = achada;
    e.target.value = achada;
    carregarSerie();
  });

  $("#busca-campos").addEventListener("input", buscarCampos);
  $("#filtro-bacia").addEventListener("change", buscarCampos);
  $("#filtro-operador").addEventListener("change", buscarCampos);
  $("#atalhos").addEventListener("change", (e) => {
    if (!e.target.value) return;
    selecionarUnico(alvoDeId(e.target.value));
    e.target.value = "";
  });

  $("#btn-novo-cluster").addEventListener("click", () => abrirCluster(null));
  $("#cl-salvar").addEventListener("click", salvarCluster);
  $("#form-cluster").addEventListener("submit", (e) => {
    // Enter num campo salva em vez de fechar a janela
    if (e.submitter?.value !== "cancelar") { e.preventDefault(); salvarCluster(); }
  });
  $("#cl-apagar").addEventListener("click", (e) => confirmarEmDoisCliques(e.currentTarget, "Confirmar exclusão", apagarCluster));
  $("#cl-busca").addEventListener("input", buscarCamposCluster);
  $("#cl-ativos").addEventListener("change", atualizarPrevia);
  $("#btn-exportar-clusters").addEventListener("click", () => baixar("/api/clusters/exportar"));
  $("#arquivo-clusters").addEventListener("change", (e) => { if (e.target.files[0]) importarClusters(e.target.files[0]); e.target.value = ""; });

  $("#btn-imagem").addEventListener("click", () => {
    if (!grafico || !resultado) return;
    const url = grafico.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#0F1C2E", excludeComponents: ["dataZoom"] });
    const nome = `anp_${resultado.alvos.map((a) => a.nome).join("-")}_${resultado.variavel.id}_${resultado.periodo.fim}.png`.replace(/[^\w.\-À-ú]+/g, "_");
    const a = el("a", { href: url, download: nome });
    document.body.append(a); a.click(); a.remove();
  });
  $("#btn-csv").addEventListener("click", () => resultado && baixar(`/api/exportar?${qs({ ...parametrosSerie(), tipo: "serie", formato: "csv" })}`));
  $("#btn-excel").addEventListener("click", () => resultado && baixar(`/api/exportar?${qs({ ...parametrosSerie(), tipo: "serie", formato: "xlsx" })}`));
  $("#btn-link").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); toast("Link desta visão copiado"); } catch { toast(location.href); }
  });
  $("#btn-alternar-tabela").addEventListener("click", (e) => {
    const wrap = $("#tabela-mensal");
    wrap.classList.toggle("oculto");
    const aberto = !wrap.classList.contains("oculto");
    e.currentTarget.textContent = aberto ? "Ocultar" : "Mostrar";
    e.currentTarget.setAttribute("aria-expanded", String(aberto));
    if (aberto && resultado) renderTabelaMensal();
  });
  $$("#seg-detalhe button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    estado.detalheQuebra = b.dataset.quebra;
    ordemDetalhe = { chave: "boe_d", desc: true };
    carregarDetalhe();
  }));
  $("#mes-detalhe").addEventListener("change", (e) => { estado.detalheMes = e.target.value; carregarDetalhe(); });
  const detalheParams = (formato) => qs({ tipo: "detalhe", formato, alvo: estado.alvos[0]?.id, quebra: estado.detalheQuebra, mes: estado.detalheMes || resultado?.periodo.fim, liquida_de: estado.liquidaDe ? [estado.liquidaDe] : [] });
  $("#btn-detalhe-csv").addEventListener("click", () => baixar(`/api/exportar?${detalheParams("csv")}`));
  $("#btn-detalhe-excel").addEventListener("click", () => baixar(`/api/exportar?${detalheParams("xlsx")}`));

  window.addEventListener("hashchange", () => {
    if (location.hash.slice(1) === estadoParaHash()) return;
    if (aplicarHash(location.hash)) { sincronizarControles(); aoMudarSelecao(); }
  });
}

// -------------------------------------------------------------------- início
async function carregarTudo() {
  await atualizarStatus();
  if (status.base_vazia) return;
  await Promise.all([carregarClusters(), carregarFiltros()]);
  buscarCampos();
  // cluster salvo no link ou no navegador que já não existe sai da seleção
  estado.alvos = estado.alvos.filter((a) => a.tipo !== "cluster" || clusters.some((c) => c.nome.toLowerCase() === a.nome.toLowerCase()));
  if (!estado.alvos.length) {
    estado.alvos = clusters.length ? [{ id: `cluster:${clusters[0].nome}`, nome: clusters[0].nome, tipo: "cluster" }] : [{ id: "todos", nome: "Brasil", tipo: "todos" }];
  }
  sincronizarControles();
  renderSelecionados();
  await carregarSerie();
}

async function iniciar() {
  let restaurado = aplicarHash(location.hash);
  if (!restaurado) {
    try { restaurado = aplicarHash(localStorage.getItem("anp-ultimo-estado") || ""); } catch { /* sem armazenamento */ }
  }
  variaveis = await api("/api/variaveis");
  montarSeletorVariavel();
  ligarEventos();
  renderSelecionados();
  try {
    await carregarTudo();
  } catch (e) {
    toast(e.message, true);
  }
  if (status?.atualizacao_em_andamento) acompanharAtualizacao();
}

iniciar();
