"""Consultas sobre a base: séries mensais, indicadores, detalhes e busca de campos.

Alvos aceitos (texto ``tipo:valor``):

| Tipo        | Exemplo                      | O que soma                                   |
|-------------|------------------------------|----------------------------------------------|
| cluster     | ``cluster:Polo Norte``       | campos do cluster salvo                      |
| campo       | ``campo:BÚZIOS``             | um campo                                     |
| poco        | ``poco:7-BUZ-3-RJS``         | um poço                                      |
| instalacao  | ``instalacao:FPSO SEPETIBA`` | poços ligados à instalação                   |
| bacia       | ``bacia:Santos``             | campos da bacia                              |
| estado      | ``estado:Bahia``             | campos do estado                             |
| operador    | ``operador:PETROBRAS``       | campos operados pela empresa                 |
| empresa     | ``empresa:PETRORECÔNCAVO``   | campos onde a empresa tem participação       |
| ambiente    | ``ambiente:TERRA``           | campos em terra ou em mar                    |
| todos       | ``todos``                    | Brasil                                       |

Sem prefixo, o texto é procurado primeiro entre os clusters e depois entre os campos.
"""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from anp_producao import cadastro, qualidade
from anp_producao import clusters as mod_clusters
from anp_producao.cadastro import InfoCampo
from anp_producao.db import COLUNAS_VOLUME
from anp_producao.formatos import dias_no_mes, mes_iso, rotulo_mes, somar_meses
from anp_producao.leitor_csv import chave_nome, limpar_nome, sem_acentos
from anp_producao.variaveis import (
    BBL_POR_M3,
    Unidade,
    Variavel,
    componentes,
    converter,
    obter_unidade,
    obter_variavel,
    para_lista,
)

TIPOS_ALVO = (
    "cluster", "campo", "poco", "instalacao", "bacia", "estado",
    "operador", "empresa", "ambiente", "todos",
)
TIPOS_REGRA = {"bacia": "bacias", "estado": "estados", "operador": "operadores",
               "empresa": "empresas", "ambiente": "ambientes"}
QUEBRAS = ("campo", "poco", "instalacao")
_COLS = list(COLUNAS_VOLUME)


class ErroConsulta(ValueError):
    pass


class NaoEncontrado(LookupError):
    pass


@dataclass(frozen=True)
class Alvo:
    tipo: str
    valor: str

    @property
    def id(self) -> str:
        return "todos" if self.tipo == "todos" else f"{self.tipo}:{self.valor}"


def ler_alvo(texto: str) -> Alvo:
    t = limpar_nome(texto)
    if not t:
        raise ErroConsulta("Alvo vazio")
    if sem_acentos(t).lower() in ("todos", "brasil", "todos:", "total"):
        return Alvo("todos", "Brasil")
    if ":" not in t:
        return Alvo("auto", t)
    tipo, valor = t.split(":", 1)
    tipo = sem_acentos(tipo).strip().lower()
    tipo = {"pocos": "poco", "instalacoes": "instalacao", "campos": "campo", "clusters": "cluster",
            "bacias": "bacia", "operadora": "operador", "socia": "empresa", "socio": "empresa",
            "concessionario": "empresa", "uf": "estado", "local": "ambiente"}.get(tipo, tipo)
    if tipo not in TIPOS_ALVO:
        raise ErroConsulta(f"Tipo de alvo desconhecido: {tipo}. Use um de {', '.join(TIPOS_ALVO)}")
    valor = limpar_nome(valor)
    if tipo != "todos" and not valor:
        raise ErroConsulta(f"Informe o valor do alvo, por exemplo {tipo}:NOME")
    return Alvo(tipo, valor)


def _sugestoes(valor: str, opcoes: list[str], n: int = 5) -> str:
    mapa = {chave_nome(o): o for o in opcoes}
    proximos = difflib.get_close_matches(chave_nome(valor), list(mapa), n=n, cutoff=0.5)
    contem = [o for k, o in mapa.items() if chave_nome(valor) in k][:n]
    nomes = list(dict.fromkeys(contem + [mapa[p] for p in proximos]))[:n]
    return f" Talvez: {', '.join(nomes)}." if nomes else ""


class Contexto:
    """Conexões e dados auxiliares reaproveitados numa mesma consulta."""

    def __init__(self, con: sqlite3.Connection, con_usr: sqlite3.Connection):
        self.con = con
        self.con_usr = con_usr
        self._info: dict[int, InfoCampo] | None = None
        self._lacunas: dict[str, dict[int, str]] | None = None
        self._complementos: dict[str, dict[int, dict]] | None = None
        self._ultimo: int | None = None
        self._ultimo_lido = False

    @property
    def info(self) -> dict[int, InfoCampo]:
        if self._info is None:
            self._info = cadastro.info_campos(self.con)
        return self._info

    @property
    def lacunas(self) -> dict[str, dict[int, str]]:
        if self._lacunas is None:
            self._lacunas = qualidade.lacunas(self.con)
        return self._lacunas

    @property
    def complementos(self) -> dict[str, dict[int, dict]]:
        """Meses completados com o arquivo mensal por zona (ver complemento.py)."""
        if self._complementos is None:
            self._complementos = qualidade.complementos(self.con)
        return self._complementos

    @property
    def ultimo_mes(self) -> int | None:
        if not self._ultimo_lido:
            self._ultimo = self.con.execute("SELECT MAX(mes) FROM producao_campo_mes").fetchone()[0]
            self._ultimo_lido = True
        return self._ultimo

    def exigir_base(self) -> int:
        if self.ultimo_mes is None:
            raise ErroConsulta(
                "A base está vazia. Clique em Atualizar base (ou rode 'anp atualizar') para baixar os dados da ANP."
            )
        return self.ultimo_mes

    def campo(self, nome: str) -> InfoCampo:
        chave = chave_nome(nome)
        for c in self.info.values():
            if chave_nome(c.nome) == chave:
                return c
        raise NaoEncontrado(
            f"Campo não encontrado: {nome}." + _sugestoes(nome, [c.nome for c in self.info.values()])
        )


# --------------------------------------------------------------------------- #
# Resolução de alvos
# --------------------------------------------------------------------------- #

@dataclass
class Grupo:
    """Um conjunto que vira uma série: alvo inteiro, campo, poço ou instalação."""
    id: str
    nome: str
    tipo: str
    dados: pd.DataFrame  # colunas campo_id, mes, volumes e (opcional) n_produtores
    avisos: list[str] = field(default_factory=list)
    campos: list[int] = field(default_factory=list)


def _consulta_lotes(con: sqlite3.Connection, sql: str, ids: list[int], extra: list | None = None) -> pd.DataFrame:
    partes = []
    for i in range(0, len(ids), 500):
        lote = ids[i : i + 500]
        ph = ",".join("?" * len(lote))
        partes.append(pd.read_sql_query(sql.replace("{ids}", ph), con, params=[*lote, *(extra or [])]))
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def _dados_campos(ctx: Contexto, ids: list[int]) -> pd.DataFrame:
    cols = ", ".join(_COLS)
    if not ids:
        return pd.DataFrame(columns=["campo_id", "mes", *_COLS, "n_produtores", "n_injetores", "n_linhas"])
    return _consulta_lotes(
        ctx.con,
        f"SELECT campo_id, mes, {cols}, n_produtores, n_injetores, n_linhas FROM producao_campo_mes "
        "WHERE campo_id IN ({ids})",
        ids,
    )


def resolver_campos(ctx: Contexto, alvo: Alvo) -> tuple[list[int], str, list[str]]:
    """Campos de um alvo do tipo conjunto. Devolve (ids, rótulo, avisos)."""
    if alvo.tipo == "todos":
        return sorted(ctx.info), "Brasil (todos os campos)", []
    if alvo.tipo == "campo":
        c = ctx.campo(alvo.valor)
        return [c.id], c.nome, []
    if alvo.tipo == "cluster":
        try:
            cl = mod_clusters.obter(ctx.con_usr, alvo.valor)
        except mod_clusters.ClusterNaoEncontrado as exc:
            nomes = [c["nome"] for c in mod_clusters.listar(ctx.con_usr)]
            raise NaoEncontrado(str(exc) + _sugestoes(alvo.valor, nomes)) from exc
        ids, faltam = mod_clusters.resolver(cl, ctx.info, ctx.ultimo_mes)
        avisos = []
        if faltam:
            avisos.append(f"{cl['nome']}: sem dados na base para {', '.join(faltam)}")
        return ids, cl["nome"], avisos
    if alvo.tipo in TIPOS_REGRA:
        regra = mod_clusters.Regra.de_dict({TIPOS_REGRA[alvo.tipo]: [alvo.valor]})
        ids = sorted(cid for cid, c in ctx.info.items() if regra.aceita(c, ctx.ultimo_mes))
        if not ids:
            opcoes = opcoes_filtro(ctx).get(TIPOS_REGRA[alvo.tipo], [])
            raise NaoEncontrado(
                f"Nenhum campo encontrado para {alvo.tipo} {alvo.valor}." + _sugestoes(alvo.valor, opcoes)
            )
        rotulo = {"bacia": "Bacia de ", "estado": "", "operador": "Operados por ",
                  "empresa": "Com participação de ", "ambiente": "Ambiente "}[alvo.tipo]
        return ids, f"{rotulo}{alvo.valor}", []
    raise ErroConsulta(f"O alvo {alvo.id} não é um conjunto de campos")


def _id_por_nome(con: sqlite3.Connection, tabela: str, nome: str, rotulo: str) -> tuple[int, str]:
    linha = con.execute(f"SELECT id, nome FROM {tabela} WHERE nome = ? COLLATE NOCASE", (nome,)).fetchone()
    if linha:
        return linha["id"], linha["nome"]
    chave = chave_nome(nome)
    candidatos = con.execute(
        f"SELECT id, nome FROM {tabela} WHERE nome LIKE ? LIMIT 200", (f"%{nome[:4]}%",)
    ).fetchall()
    for c in candidatos:
        if chave_nome(c["nome"]) == chave:
            return c["id"], c["nome"]
    raise NaoEncontrado(f"{rotulo} não encontrado: {nome}." + _sugestoes(nome, [c["nome"] for c in candidatos]))


def grupo_do_alvo(ctx: Contexto, alvo: Alvo) -> Grupo:
    cols = ", ".join(_COLS)
    if alvo.tipo == "auto":
        try:
            return grupo_do_alvo(ctx, Alvo("cluster", alvo.valor))
        except NaoEncontrado:
            try:
                return grupo_do_alvo(ctx, Alvo("campo", alvo.valor))
            except NaoEncontrado as exc:
                raise NaoEncontrado(
                    f"Não achei cluster nem campo chamado {alvo.valor}. "
                    "Use um prefixo como campo:, cluster:, poco: ou bacia:." + str(exc).split(".", 1)[-1]
                ) from exc
    if alvo.tipo == "poco":
        pid, nome = _id_por_nome(ctx.con, "pocos", alvo.valor, "Poço")
        campos = [r[0] for r in ctx.con.execute("SELECT campo_id FROM poco_campo WHERE poco_id = ?", (pid,))]
        dados = _consulta_lotes(
            ctx.con,
            f"SELECT campo_id, mes, {', '.join(f'SUM({c}) AS {c}' for c in _COLS)} FROM producao "
            "WHERE campo_id IN ({ids}) AND poco_id = ? GROUP BY campo_id, mes",
            campos,
            [pid],
        )
        return Grupo(f"poco:{nome}", nome, "poco", dados, campos=campos)
    if alvo.tipo == "instalacao":
        iid, nome = _id_por_nome(ctx.con, "instalacoes", alvo.valor, "Instalação")
        dados = pd.read_sql_query(
            f"SELECT campo_id, mes, {cols}, n_produtores, n_injetores, n_linhas FROM producao_instalacao_mes "
            "WHERE instalacao_id = ?",
            ctx.con,
            params=[iid],
        )
        campos = sorted(int(c) for c in dados["campo_id"].unique())
        return Grupo(f"instalacao:{nome}", nome, "instalacao", dados, campos=campos)
    ids, rotulo, avisos = resolver_campos(ctx, alvo)
    tipo = alvo.tipo
    gid = alvo.id if tipo != "campo" else f"campo:{rotulo}"
    if tipo == "cluster":
        gid = f"cluster:{rotulo}"
    return Grupo(gid, rotulo, tipo, _dados_campos(ctx, ids), avisos=avisos, campos=ids)


# --------------------------------------------------------------------------- #
# Lacunas, participação e montagem de séries
# --------------------------------------------------------------------------- #

def _meses_faltando(ctx: Contexto, dados: pd.DataFrame, chave_unidade: str = "campo_id") -> set[int]:
    """Meses em que alguma unidade ativa (campo, poço, instalação) ficou sem linha
    por lacuna da fonte. Ativa = tinha dado em algum dos três meses anteriores."""
    if dados.empty:
        return set()
    chaves = ["campo_id"] if chave_unidade == "campo_id" else [chave_unidade, "campo_id"]
    faltando: set[int] = set()
    for chave, meses in dados.groupby(chaves)["mes"]:
        campo_id = chave[-1] if isinstance(chave, tuple) else chave
        info = ctx.info.get(int(campo_id))
        ambientes = info.ambientes if info and info.ambientes else list(qualidade.AMBIENTES)
        tem = {int(m) for m in meses}
        linhas = None
        for amb in ambientes:
            for mes, tipo in ctx.lacunas.get(amb, {}).items():
                if mes in faltando:
                    continue
                if mes in tem:
                    # Mês parcial na fonte: o campo veio, mas com bem menos linhas que no mês anterior.
                    if tipo == "parcial" and "n_linhas" in dados:
                        if linhas is None:
                            grupo_linhas = dados.loc[meses.index]
                            linhas = grupo_linhas.groupby("mes")["n_linhas"].sum().to_dict()
                        anterior = next((linhas[somar_meses(mes, -k)] for k in (1, 2, 3)
                                         if somar_meses(mes, -k) in linhas), None)
                        if anterior and linhas.get(mes, 0) < 0.6 * anterior:
                            faltando.add(mes)
                    continue
                if any(somar_meses(mes, -k) in tem for k in (1, 2, 3)):
                    faltando.add(mes)
    return faltando


def _complementados(ctx: Contexto, grupos: list[Grupo], inicio: int, fim: int) -> list[dict]:
    """Meses do período em que os campos dos alvos receberam poços do arquivo por zona."""
    comp = ctx.complementos
    meses = sorted({m for por_mes in comp.values() for m in por_mes if inicio <= m <= fim})
    if not meses:
        return []
    ambientes_por_mes: dict[int, set[str]] = {}
    for g in grupos:
        if g.dados.empty:
            continue
        pares = g.dados.loc[g.dados["mes"].isin(meses), ["campo_id", "mes"]].drop_duplicates()
        for campo_id, mes in pares.itertuples(index=False):
            info = ctx.info.get(int(campo_id))
            for amb in (info.ambientes if info and info.ambientes else qualidade.AMBIENTES):
                if int(mes) in comp.get(amb, {}):
                    ambientes_por_mes.setdefault(int(mes), set()).add(amb)
    itens = []
    for mes in meses:
        ambientes = sorted(ambientes_por_mes.get(mes, ()))
        if ambientes:
            itens.append({
                "mes": mes_iso(mes),
                "rotulo": rotulo_mes(mes),
                "ambientes": ambientes,
                "pocos": sum(comp[a][mes]["pocos"] for a in ambientes),
                "motivo": ", ".join(sorted({comp[a][mes]["motivo"] for a in ambientes})),
            })
    return itens


def _lista_pt(itens: list[str], limite: int = 6) -> str:
    if len(itens) > limite:
        return ", ".join(itens[:limite]) + " e outros"
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def fatores_participacao(ctx: Contexto, empresas: list[str] | None) -> tuple[dict[int, float] | None, list[str]]:
    if not empresas:
        return None, []
    disponiveis = {chave_nome(e["empresa"]): e["empresa"] for e in cadastro.empresas(ctx.con)}
    escolhidas = []
    for e in empresas:
        k = chave_nome(e)
        if k not in disponiveis:
            raise NaoEncontrado(f"Empresa não encontrada no cadastro de concessionários: {e}." +
                                _sugestoes(e, list(disponiveis.values())))
        escolhidas.append(disponiveis[k])
    chaves = {chave_nome(e) for e in escolhidas}
    fatores = {}
    for cid, c in ctx.info.items():
        fatores[cid] = sum(p for emp, p in c.empresas.items() if chave_nome(emp) in chaves) / 100.0
    avisos = [
        "Produção líquida usa a participação atual publicada pela ANP para todo o histórico "
        f"({', '.join(escolhidas)})."
    ]
    return fatores, avisos


def _aplicar_participacao(dados: pd.DataFrame, fatores: dict[int, float] | None) -> pd.DataFrame:
    if fatores is None or dados.empty:
        return dados
    d = dados.copy()
    f = d["campo_id"].map(fatores).fillna(0.0)
    for c in _COLS:
        if c in d:
            d[c] = d[c] * f
    return d


def _base_mensal(dados: pd.DataFrame, meses: list[int]) -> pd.DataFrame:
    if dados.empty:
        base = pd.DataFrame(0.0, index=meses, columns=_COLS)
    else:
        base = dados.groupby("mes")[_COLS].sum().reindex(meses, fill_value=0.0)
    if "n_produtores" in dados:
        base["n_produtores"] = (
            dados.groupby("mes")["n_produtores"].sum().reindex(meses, fill_value=0) if not dados.empty else 0
        )
    return base


def _valores(variavel: Variavel, unidade: Unidade, base: pd.DataFrame, faltando: set[int]) -> tuple[pd.Series, pd.Series, pd.Series | None]:
    dias = pd.Series([dias_no_mes(m) for m in base.index], index=base.index, dtype="float64")
    num, den = componentes(variavel, base)
    valores = converter(variavel, unidade, num, den, dias)
    if faltando:
        mascara = base.index.isin(list(faltando))
        valores = valores.where(~mascara)
        num = num.where(~mascara)
        if den is not None:
            den = den.where(~mascara)
    return valores, num, den


def indicadores(
    variavel: Variavel,
    unidade: Unidade,
    base: pd.DataFrame,
    faltando: set[int],
) -> list[dict]:
    """KPIs do último mês com dado, sobre o histórico inteiro do grupo."""
    if base.empty:
        return []
    valores, num, den = _valores(variavel, unidade, base, faltando)
    validos = valores.dropna()
    if validos.empty:
        return []
    ref = int(validos.index.max())
    v_ref = float(valores.loc[ref])
    kpis: list[dict] = [{
        "id": "ultimo", "rotulo": "Último mês", "valor": v_ref, "unidade": unidade.rotulo,
        "mes": mes_iso(ref), "detalhe": rotulo_mes(ref),
    }]
    percentual = variavel.dimensao == "percentual"

    def variacao(outro_mes: int, id_: str, rotulo: str) -> None:
        v = valores.get(outro_mes)
        item = {"id": id_, "rotulo": rotulo, "valor": None, "comparado_com": mes_iso(outro_mes),
                "detalhe": f"vs {rotulo_mes(outro_mes)}", "tipo": "pontos" if percentual else "percentual"}
        if v is not None and not np.isnan(v):
            if percentual:
                item["valor"] = v_ref - float(v)
            elif v > 0:
                item["valor"] = v_ref / float(v) - 1.0
        else:
            item["detalhe"] += " (sem dado)"
        kpis.append(item)

    variacao(somar_meses(ref, -1), "var_mensal", "Variação mensal")
    variacao(somar_meses(ref, -12), "var_anual", "Variação anual")

    janela = [m for m in base.index if somar_meses(ref, -11) <= m <= ref]
    if variavel.tipo == "razao":
        n = num.loc[janela].sum(min_count=1)
        d = den.loc[janela].sum(min_count=1)
        media = float(n / d * unidade.fator) if d and d > 0 else None
    else:
        vol = num.loc[janela].dropna()
        dias = sum(dias_no_mes(m) for m in vol.index)
        if unidade.por_dia:
            media = float(vol.sum() * unidade.fator / dias) if dias else None
        else:
            media = float(vol.mean() * unidade.fator) if len(vol) else None
    kpis.append({"id": "media_12m", "rotulo": "Média 12 meses", "valor": media,
                 "unidade": unidade.rotulo, "detalhe": f"{rotulo_mes(janela[0])} a {rotulo_mes(ref)}"})

    if variavel.tipo == "volume":
        pico_mes = int(validos.idxmax())
        pico = float(validos.max())
        kpis.append({"id": "pico", "rotulo": "Pico histórico", "valor": pico, "unidade": unidade.rotulo,
                     "mes": mes_iso(pico_mes), "detalhe": rotulo_mes(pico_mes),
                     "fracao_atual": (v_ref / pico) if pico > 0 else None})
        if unidade.acumulado_fator:
            ano = ref // 100
            meses_ano = [m for m in base.index if m // 100 == ano and m <= ref]
            incompleto = any(m in faltando for m in meses_ano)
            kpis.append({
                "id": "acumulado_ano", "rotulo": f"Acumulado {ano}",
                "valor": float(num.loc[meses_ano].sum()) * unidade.acumulado_fator,
                "unidade": unidade.acumulado_rotulo,
                "detalhe": f"jan a {rotulo_mes(ref).split('/')[0]}" + (" (com lacuna da fonte)" if incompleto else ""),
            })
            kpis.append({
                "id": "acumulado_total", "rotulo": "Acumulado histórico",
                "valor": float(num.sum()) * unidade.acumulado_fator,
                "unidade": unidade.acumulado_rotulo,
                "detalhe": f"desde {rotulo_mes(int(base.index.min()))}",
            })
    if "n_produtores" in base and ref in base.index:
        kpis.append({"id": "produtores", "rotulo": "Poços produtores", "valor": float(base.loc[ref, "n_produtores"]),
                     "unidade": "poços", "detalhe": rotulo_mes(ref)})
    return kpis


def _eixo(inicio: int, fim: int) -> list[int]:
    return qualidade.intervalo_meses(inicio, fim)


def _primeiro_mes(dados: pd.DataFrame) -> int | None:
    if dados.empty:
        return None
    vol = dados[_COLS].fillna(0).abs().sum(axis=1)
    com_dado = dados.loc[vol > 0, "mes"]
    return int(com_dado.min()) if not com_dado.empty else int(dados["mes"].min())


def _ranking(variavel: Variavel, dados: pd.DataFrame, chave: str) -> list:
    if dados.empty:
        return []
    if variavel.tipo == "razao":
        peso = dados[["oleo", "condensado", "agua", "gas_assoc", "gas_nao_assoc"]].fillna(0).sum(axis=1)
    else:
        num, _ = componentes(variavel, dados)
        peso = num
    return list(peso.groupby(dados[chave]).sum().sort_values(ascending=False).index)


def _dados_quebra(ctx: Contexto, grupo: Grupo, quebra: str, inicio: int, fim: int) -> tuple[pd.DataFrame, str, dict]:
    """Linhas no nível da quebra, com coluna 'unidade' e nomes legíveis."""
    cols = ", ".join(_COLS)
    if quebra == "campo":
        d = grupo.dados[(grupo.dados["mes"] >= inicio) & (grupo.dados["mes"] <= fim)].copy()
        d["unidade"] = d["campo_id"]
        nomes = {cid: ctx.info[cid].nome if cid in ctx.info else str(cid) for cid in d["unidade"].unique()}
        return d, "unidade", nomes
    if quebra == "instalacao":
        if grupo.tipo == "instalacao":
            raise ErroConsulta("Para uma instalação, use a quebra por campo ou por poço")
        d = _consulta_lotes(
            ctx.con,
            f"SELECT instalacao_id AS unidade, campo_id, mes, {cols} FROM producao_instalacao_mes "
            "WHERE campo_id IN ({ids}) AND mes BETWEEN ? AND ?",
            grupo.campos, [inicio, fim],
        )
        nomes = dict(ctx.con.execute("SELECT id, nome FROM instalacoes").fetchall())
        return d, "unidade", nomes
    if quebra == "poco":
        filtro_inst = ""
        extra: list = [inicio, fim]
        if grupo.tipo == "instalacao":
            iid, _ = _id_por_nome(ctx.con, "instalacoes", grupo.nome, "Instalação")
            filtro_inst = " AND instalacao_id = ?"
            extra.append(iid)
        d = _consulta_lotes(
            ctx.con,
            f"SELECT poco_id AS unidade, campo_id, mes, {', '.join(f'SUM({c}) AS {c}' for c in _COLS)} "
            "FROM producao WHERE campo_id IN ({ids}) AND mes BETWEEN ? AND ?" + filtro_inst +
            " GROUP BY poco_id, campo_id, mes",
            grupo.campos, extra,
        )
        ids = [int(x) for x in d["unidade"].unique()] if not d.empty else []
        nomes = {}
        for i in range(0, len(ids), 500):
            lote = ids[i : i + 500]
            nomes.update(dict(ctx.con.execute(
                f"SELECT id, nome FROM pocos WHERE id IN ({','.join('?' * len(lote))})", lote
            ).fetchall()))
        return d, "unidade", nomes
    raise ErroConsulta(f"Quebra desconhecida: {quebra}. Use {', '.join(QUEBRAS)}")


def serie(
    ctx: Contexto,
    alvos: list[str],
    variavel: str | None = None,
    unidade: str | None = None,
    inicio: int | None = None,
    fim: int | None = None,
    quebra: str | None = None,
    liquida_de: list[str] | None = None,
    top_n: int = 10,
) -> dict:
    """Série mensal para um ou mais alvos, com indicadores e lacunas."""
    ultimo = ctx.exigir_base()
    var = obter_variavel(variavel)
    un = obter_unidade(var, unidade)
    if not alvos:
        raise ErroConsulta("Escolha pelo menos um alvo (cluster, campo, poço...)")
    if quebra in ("", "nenhuma", "total"):
        quebra = None
    if quebra and len(alvos) > 1:
        raise ErroConsulta("A quebra por campo, poço ou instalação funciona com um alvo por vez")
    if quebra and quebra not in QUEBRAS:
        raise ErroConsulta(f"Quebra desconhecida: {quebra}. Use {', '.join(QUEBRAS)}")
    top_n = max(1, min(int(top_n or 10), 50))

    fatores, avisos = fatores_participacao(ctx, liquida_de)
    grupos = []
    for texto in dict.fromkeys(alvos):
        g = grupo_do_alvo(ctx, ler_alvo(texto))
        g.dados = _aplicar_participacao(g.dados, fatores)
        avisos.extend(g.avisos)
        grupos.append(g)
    if quebra == "poco" and grupos[0].tipo == "poco":
        raise ErroConsulta("Um poço não tem quebra; escolha um campo, cluster ou instalação")
    if quebra == "campo" and grupos[0].tipo == "campo":
        quebra = None

    fim = min(fim or ultimo, ultimo)
    primeiros = [p for p in (_primeiro_mes(g.dados) for g in grupos) if p]
    if not primeiros:
        raise ErroConsulta("Nenhum dado de produção para o alvo escolhido")
    inicio_dados = min(primeiros)
    inicio = max(inicio or inicio_dados, inicio_dados)
    if inicio > fim:
        raise ErroConsulta(f"Período vazio: {rotulo_mes(inicio)} depois de {rotulo_mes(fim)}")
    eixo = _eixo(inicio, fim)
    casas = un.casas

    series = []
    resumo_kpis = {}
    faltando_total: set[int] = set()
    for g in grupos:
        g_inicio = _primeiro_mes(g.dados) or inicio
        hist = _eixo(min(g_inicio, inicio), ultimo)
        falt = _meses_faltando(ctx, g.dados)
        faltando_total |= falt
        base = _base_mensal(g.dados, hist)
        valores, _, _ = _valores(var, un, base, falt)
        resumo_kpis[g.id] = indicadores(var, un, base.loc[[m for m in hist if m >= g_inicio]], falt)
        if not quebra:
            series.append({"id": g.id, "nome": g.nome, "tipo": g.tipo,
                           "valores": para_lista(valores.reindex(eixo), casas)})

    total = None
    if quebra:
        g = grupos[0]
        d, chave, nomes = _dados_quebra(ctx, g, quebra, inicio, fim)
        if quebra != "campo":  # a quebra por campo já parte dos dados ajustados
            d = _aplicar_participacao(d, fatores)
        ordem = _ranking(var, d, chave)
        principais = ordem[:top_n]
        for u in principais:
            parte = d[d[chave] == u]
            falt = _meses_faltando(ctx, parte, chave)
            base = _base_mensal(parte, eixo)
            valores, _, _ = _valores(var, un, base, falt)
            series.append({"id": f"{quebra}:{nomes.get(u, u)}", "nome": str(nomes.get(u, u)), "tipo": quebra,
                           "valores": para_lista(valores, casas)})
        resto = d[~d[chave].isin(principais)]
        if not resto.empty:
            falt = _meses_faltando(ctx, resto, chave)
            base = _base_mensal(resto, eixo)
            valores, _, _ = _valores(var, un, base, falt)
            series.append({"id": "outros", "nome": f"Outros ({len(ordem) - len(principais)})", "tipo": "outros",
                           "valores": para_lista(valores, casas)})
        falt_g = _meses_faltando(ctx, g.dados)
        base_g = _base_mensal(g.dados[(g.dados["mes"] >= inicio) & (g.dados["mes"] <= fim)], eixo)
        valores_g, _, _ = _valores(var, un, base_g, falt_g)
        total = {"id": g.id, "nome": g.nome, "valores": para_lista(valores_g, casas)}
        if var.tipo == "razao":
            avisos.append("Em razões (RGO, corte de água) as partes não somam o total; o gráfico mostra linhas.")

    lacunas_periodo = []
    for mes in sorted(m for m in faltando_total if inicio <= m <= fim):
        ambientes = [a for a in qualidade.AMBIENTES if mes in ctx.lacunas.get(a, {})]
        lacunas_periodo.append({"mes": mes_iso(mes), "rotulo": rotulo_mes(mes), "ambientes": ambientes,
                                "tipo": ", ".join(sorted({ctx.lacunas[a][mes] for a in ambientes}))})
    if lacunas_periodo:
        avisos.append(
            "A ANP não publicou (ou publicou incompletos) alguns meses usados aqui: "
            + ", ".join(f"{l['rotulo']} ({'/'.join(a.lower() for a in l['ambientes'])})" for l in lacunas_periodo[:6])
            + (" e outros" if len(lacunas_periodo) > 6 else "")
            + ". Esses meses aparecem vazios."
        )
    complementados = _complementados(ctx, grupos, inicio, fim)
    if complementados:
        avisos.append(
            _lista_pt([f"{c['rotulo']} ({'/'.join(a.lower() for a in c['ambientes'])})" for c in complementados])
            + (" foi completado" if len(complementados) == 1 else " foram completados")
            + " com o arquivo mensal de produção por zona da ANP, porque o arquivo por poço veio sem esses"
            " poços ou sem o mês inteiro."
        )

    return {
        "variavel": var.como_dict(),
        "unidade": un.como_dict(),
        "meses": [mes_iso(m) for m in eixo],
        "series": series,
        "total": total,
        "quebra": quebra,
        "alvos": [{"id": g.id, "nome": g.nome, "tipo": g.tipo, "campos": len(g.campos)} for g in grupos],
        "indicadores": resumo_kpis,
        "lacunas": lacunas_periodo,
        "complementados": complementados,
        "avisos": list(dict.fromkeys(avisos)),
        "periodo": {"inicio": mes_iso(inicio), "fim": mes_iso(fim), "inicio_dados": mes_iso(inicio_dados)},
        "ultimo_mes_base": mes_iso(ultimo),
        "liquida_de": liquida_de or [],
    }


# --------------------------------------------------------------------------- #
# Detalhe de um mês (poços, instalações ou campos)
# --------------------------------------------------------------------------- #

def detalhe(
    ctx: Contexto,
    alvo: str,
    quebra: str = "poco",
    mes: int | None = None,
    liquida_de: list[str] | None = None,
    limite: int | None = None,
) -> dict:
    ultimo = ctx.exigir_base()
    if quebra not in QUEBRAS:
        raise ErroConsulta(f"Quebra desconhecida: {quebra}. Use {', '.join(QUEBRAS)}")
    g = grupo_do_alvo(ctx, ler_alvo(alvo))
    if g.tipo == "poco":
        raise ErroConsulta("O detalhe é por campo, cluster ou instalação; para um poço use a série")
    fatores, avisos = fatores_participacao(ctx, liquida_de)
    avisos += g.avisos
    if mes is None:
        com_dado = g.dados.loc[g.dados[_COLS].fillna(0).abs().sum(axis=1) > 0, "mes"]
        mes = int(com_dado.max()) if not com_dado.empty else ultimo
    meses = [mes, somar_meses(mes, -1), somar_meses(mes, -12)]
    if quebra == "campo":
        d = g.dados[g.dados["mes"].isin(meses)].copy()
        d["unidade"] = d["campo_id"]
        nomes = {cid: ctx.info[cid].nome for cid in d["unidade"].unique() if cid in ctx.info}
    else:
        d, _, nomes = _dados_quebra(ctx, g, quebra, min(meses), mes)
        d = d[d["mes"].isin(meses)]
    d = _aplicar_participacao(d, fatores)

    campo_nome = {cid: c.nome for cid, c in ctx.info.items()}
    inst_por_poco: dict[int, str] = {}
    if quebra == "poco" and not d.empty:
        ids = [int(x) for x in d.loc[d["mes"] == mes, "unidade"].unique()]
        for i in range(0, len(ids), 500):
            lote = ids[i : i + 500]
            for pid, inst in ctx.con.execute(
                f"SELECT p.poco_id, GROUP_CONCAT(DISTINCT i.nome) FROM producao p "
                f"JOIN instalacoes i ON i.id = p.instalacao_id WHERE p.mes = ? AND p.poco_id IN "
                f"({','.join('?' * len(lote))}) GROUP BY p.poco_id",
                [mes, *lote],
            ):
                inst_por_poco[pid] = inst

    def taxas(m: int) -> pd.DataFrame:
        p = d[d["mes"] == m].groupby("unidade")[_COLS].sum()
        dias = dias_no_mes(m)
        petroleo = p["oleo"] + p["condensado"]
        gas = p["gas_assoc"] + p["gas_nao_assoc"]
        liquido = p["agua"] + petroleo
        return pd.DataFrame({
            "petroleo_bbl_d": petroleo * BBL_POR_M3 / dias,
            "gas_mil_m3_d": gas / dias,
            "agua_bbl_d": p["agua"] * BBL_POR_M3 / dias,
            "boe_d": (petroleo + gas) * BBL_POR_M3 / dias,
            "corte_agua_pct": (100 * p["agua"] / liquido.where(liquido > 0)),
            "rgo_m3_m3": gas * 1000 / petroleo.where(petroleo > 0),
            "inj_agua_m3_d": (p["inj_agua_rec"] + p["inj_agua_desc"]) / dias,
            "inj_gas_mil_m3_d": (p["inj_gas"] + p["inj_co2"]) / dias,
        })

    atual = taxas(mes)
    ant = taxas(meses[1]).reindex(atual.index)
    a12 = taxas(meses[2]).reindex(atual.index)

    def razao(a: pd.Series, b: pd.Series) -> pd.Series:
        return (a / b.where(b > 0)) - 1

    atual["var_mensal_petroleo"] = razao(atual["petroleo_bbl_d"], ant["petroleo_bbl_d"])
    atual["var_anual_petroleo"] = razao(atual["petroleo_bbl_d"], a12["petroleo_bbl_d"])
    atual["var_mensal_boe"] = razao(atual["boe_d"], ant["boe_d"])
    campos_por_unidade = d[d["mes"] == mes].groupby("unidade")["campo_id"].unique()

    def limpo(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v), 4)

    linhas = []
    for u, r in atual.iterrows():
        injeta = (r["inj_agua_m3_d"] or 0) + (r["inj_gas_mil_m3_d"] or 0)
        linha = {"nome": str(nomes.get(u, u)),
                 "situacao": "produtor" if r["boe_d"] > 0 else ("injetor" if injeta > 0 else "sem produção")}
        linha.update({k: limpo(v) for k, v in r.items()})
        if quebra != "campo":
            linha["campos"] = ", ".join(sorted({campo_nome.get(int(c), str(c)) for c in campos_por_unidade.get(u, [])}))
        if quebra == "poco":
            linha["instalacao"] = inst_por_poco.get(int(u), "")
        linhas.append(linha)
    total_boe = sum(l["boe_d"] for l in linhas) or 0.0
    for l in linhas:
        l["participacao_boe"] = (l["boe_d"] / total_boe) if total_boe > 0 else None
    linhas.sort(key=lambda l: (-l["boe_d"], -(l["inj_agua_m3_d"] + l["inj_gas_mil_m3_d"]), l["nome"]))
    total = {k: sum(l[k] for l in linhas) for k in ("petroleo_bbl_d", "gas_mil_m3_d", "agua_bbl_d", "boe_d", "inj_agua_m3_d", "inj_gas_mil_m3_d")}
    n_total = len(linhas)
    if limite:
        linhas = linhas[: int(limite)]
    if mes in _meses_faltando(ctx, g.dados):
        avisos.append(f"{rotulo_mes(mes)} está incompleto na fonte da ANP para parte destes campos")
    return {
        "alvo": {"id": g.id, "nome": g.nome, "tipo": g.tipo},
        "quebra": quebra,
        "mes": mes_iso(mes),
        "rotulo_mes": rotulo_mes(mes),
        "linhas": linhas,
        "total_linhas": n_total,
        "total": total,
        "avisos": list(dict.fromkeys(avisos)),
        "unidades": {"petroleo_bbl_d": "bbl/d", "gas_mil_m3_d": "mil m³/d", "agua_bbl_d": "bbl/d",
                     "boe_d": "boe/d", "corte_agua_pct": "%", "rgo_m3_m3": "m³/m³",
                     "inj_agua_m3_d": "m³/d", "inj_gas_mil_m3_d": "mil m³/d"},
    }


# --------------------------------------------------------------------------- #
# Busca de campos, filtros e revisões
# --------------------------------------------------------------------------- #

def _producao_no_mes(ctx: Contexto, mes: int) -> dict[int, tuple[float, float]]:
    dias = dias_no_mes(mes)
    resultado = {}
    for cid, petroleo, gas in ctx.con.execute(
        "SELECT campo_id, COALESCE(oleo,0)+COALESCE(condensado,0), COALESCE(gas_assoc,0)+COALESCE(gas_nao_assoc,0) "
        "FROM producao_campo_mes WHERE mes = ?",
        (mes,),
    ):
        resultado[cid] = (petroleo * BBL_POR_M3 / dias, gas / dias)
    return resultado


def buscar_campos(
    ctx: Contexto,
    busca: str = "",
    bacia: str | None = None,
    estado: str | None = None,
    operador: str | None = None,
    empresa: str | None = None,
    ambiente: str | None = None,
    situacao: str | None = None,
    somente_ativos: bool = False,
    limite: int = 50,
) -> dict:
    ultimo = ctx.ultimo_mes
    regra = mod_clusters.Regra.de_dict({
        "bacias": [bacia] if bacia else [], "estados": [estado] if estado else [],
        "operadores": [operador] if operador else [], "empresas": [empresa] if empresa else [],
        "ambientes": [ambiente] if ambiente else [], "situacoes": [situacao] if situacao else [],
        "somente_ativos": somente_ativos,
    })
    candidatos = [c for c in ctx.info.values() if regra.vazia() or regra.aceita(c, ultimo)]
    termo = chave_nome(busca)
    if termo:
        candidatos = [c for c in candidatos if termo in chave_nome(c.nome)]
    prod = _producao_no_mes(ctx, ultimo) if ultimo else {}

    def ordem(c: InfoCampo):
        nome = chave_nome(c.nome)
        boe = sum(prod.get(c.id, (0.0, 0.0))[i] * (1 if i == 0 else BBL_POR_M3) for i in (0, 1))
        return (0 if nome == termo else 1 if nome.startswith(termo) else 2, -boe, nome) if termo else (-boe, nome)

    candidatos.sort(key=ordem)
    total = len(candidatos)
    itens = []
    for c in candidatos[: max(1, min(int(limite or 50), 2000))]:
        d = c.como_dict()
        petroleo, gas = prod.get(c.id, (0.0, 0.0))
        d["primeiro_mes"] = mes_iso(c.primeiro_mes)
        d["ultimo_mes"] = mes_iso(c.ultimo_mes)
        d["petroleo_bbl_d"] = round(petroleo, 1)
        d["gas_mil_m3_d"] = round(gas, 2)
        d["boe_d"] = round(petroleo + gas * BBL_POR_M3, 1)
        itens.append(d)
    return {"total": total, "mes_referencia": mes_iso(ultimo), "campos": itens}


def opcoes_filtro(ctx: Contexto) -> dict:
    bacias, estados, operadores, situacoes, empresas = set(), set(), set(), set(), set()
    for c in ctx.info.values():
        if c.bacia:
            bacias.add(c.bacia)
        if c.estado:
            estados.add(c.estado)
        operadores.update(c.operadores)
        situacoes.update(c.situacoes)
        empresas.update(e for e, p in c.empresas.items() if p > 0)

    def ordenar(s):
        return sorted(s, key=chave_nome)

    return {
        "bacias": ordenar(bacias),
        "estados": ordenar(estados),
        "operadores": ordenar(operadores),
        "empresas": ordenar(empresas),
        "situacoes": ordenar(situacoes),
        "ambientes": ["MAR", "TERRA"],
    }


def revisoes(ctx: Contexto, atualizacao_id: int | None = None, somente_nao_vistas: bool = False, limite: int = 500) -> dict:
    filtros, params = [], []
    if atualizacao_id:
        filtros.append("r.atualizacao_id = ?")
        params.append(atualizacao_id)
    if somente_nao_vistas:
        filtros.append("r.vista = 0")
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    total = ctx.con.execute(f"SELECT COUNT(*) FROM revisoes r {where}", params).fetchone()[0]
    linhas = ctx.con.execute(
        f"SELECT r.id, r.atualizacao_id, r.mes, r.variavel, r.antes, r.depois, r.vista, c.nome AS campo, "
        f"a.concluida_em FROM revisoes r JOIN campos c ON c.id = r.campo_id "
        f"LEFT JOIN atualizacoes a ON a.id = r.atualizacao_id {where} "
        f"ORDER BY r.atualizacao_id DESC, ABS(r.depois - r.antes) DESC LIMIT ?",
        [*params, limite],
    ).fetchall()
    itens = []
    for r in linhas:
        dias = dias_no_mes(r["mes"])
        if r["variavel"] == "gas":
            fator, unidade = 1.0 / dias, "mil m³/d"
        else:
            fator, unidade = BBL_POR_M3 / dias, "bbl/d"
        antes, depois = r["antes"] * fator, r["depois"] * fator
        itens.append({
            "id": r["id"], "atualizacao_id": r["atualizacao_id"], "campo": r["campo"],
            "mes": mes_iso(r["mes"]), "rotulo_mes": rotulo_mes(r["mes"]),
            "variavel": {"petroleo": "Petróleo", "gas": "Gás natural", "agua": "Água"}[r["variavel"]],
            "antes": round(antes, 2), "depois": round(depois, 2), "unidade": unidade,
            "variacao": (depois / antes - 1) if antes else None, "vista": bool(r["vista"]),
            "detectada_em": r["concluida_em"],
        })
    return {"total": total, "itens": itens}
