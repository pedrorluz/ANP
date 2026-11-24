"""Meses que faltam nos arquivos por poço, completados com o arquivo mensal por zona.

Por que existe
--------------
Em nov/2026 os arquivos trimestrais de terra por poço de 2026 não traziam abril
e julho, e março veio sem cerca de 1.400 poços (Siririzinho, Riachuelo, Rabo
Branco, parte de Carmópolis e outros de Sergipe), uns 14% do petróleo de terra
do mês. A mesma página da ANP publica, mês a mês desde out/2014, a "Produção por
zona", com a média diária de cada poço em cada zona produtora. Nos meses
completos os dois conjuntos batem em até 0,1%, e somar a março os poços que
faltavam reconstitui o mês com 0,01% de diferença.

Regras
------
* O arquivo por poço continua sendo a base. O arquivo por zona só acrescenta
  poços que faltam, nunca troca linhas.
* Mês sem nenhuma linha por poço num ambiente recebe todos os poços desse
  ambiente no arquivo por zona.
* Mês com linhas recebe os poços que faltam (com algum volume), desde que o
  petróleo e o gás do arquivo por poço fiquem mais de 0,5% abaixo do arquivo por
  zona. Nos meses completos os poços que só aparecem no arquivo por zona não têm
  petróleo nem gás, só um pouco de água, e entrar com eles seria ruído.
* A instalação de cada poço acrescentado vem do arquivo por poço no mês mais
  próximo (o anterior primeiro). Poço sem histórico fica "Não Informado".
* São conferidos os meses que a contagem de linhas aponta como lacuna, os meses
  da janela de revisão (os que a ANP mais republica) e os meses cujo arquivo por
  poço mudou desde a última conferência.
* Quando um arquivo por poço é regravado, o complemento dos meses dele sai na
  mesma transação (``atualizacao.processar_arquivo``) e é refeito aqui no fim da
  atualização, já sem os poços que chegaram.

Cada conferência fica em ``arquivos_fonte`` com tipo 'zona', uma linha por
arquivo e ambiente (``<url>#MAR`` e ``<url>#TERRA``): ``mes_min`` é o mês
conferido, ``linhas`` é o número de poços acrescentados (zero quando o mês já
estava completo) e ``status`` é 'ok', 'pendente' (o arquivo por poço mudou),
'sem_dados' (o arquivo por zona não tem aquele ambiente), 'substituido' ou
'erro'. Só 'ok' tira o mês da lista de lacunas.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from anp_producao import qualidade
from anp_producao.atualizacao import (
    Identificadores,
    _atualizar_metadados_campos,
    _lotes,
    agora,
    recalcular_agregados,
)
from anp_producao.config import Config
from anp_producao.db import COLUNAS_VOLUME, transacao
from anp_producao.fonte_anp import TRECHO_ZONA, ArquivoZona, ClienteANP, ErroFonte
from anp_producao.formatos import rotulo_mes, somar_meses
from anp_producao.leitor_csv import FormatoInvalido, chave_nome, ler_producao_zona

log = logging.getLogger(__name__)

LIMIAR_FALTA = 0.005  # petróleo + gás do arquivo por poço mais de 0,5% abaixo do arquivo por zona
VIZINHOS = (-1, -2, -3, 1, 2, 3)  # onde procurar a instalação do poço, em ordem de preferência
NAO_INFORMADO = "Não Informado"
_HC = ["oleo", "condensado", "gas_assoc", "gas_nao_assoc"]


@dataclass
class Obtido:
    dados: bytes
    tamanho: int
    sha256: str


Obter = Callable[[ArquivoZona, "int | None"], Obtido]


@dataclass
class ResultadoComplemento:
    meses_completados: list[int] = field(default_factory=list)  # meses regravados com poços acrescentados
    meses_conferidos: int = 0
    avisos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Onde buscar o arquivo por zona (site da ANP ou cache local)
# --------------------------------------------------------------------------- #

def caminho_cache(config: Config, url: str) -> Path:
    cauda = url.split(TRECHO_ZONA, 1)[1] if TRECHO_ZONA in url else url.rsplit("/", 1)[-1]
    return config.dir_brutos / "zona" / (cauda.replace("/", "__") + ".gz")


def _do_cache(caminho: Path) -> Obtido:
    dados = gzip.decompress(caminho.read_bytes())
    return Obtido(dados, len(dados), hashlib.sha256(dados).hexdigest())


def obter_online(config: Config, cliente: ClienteANP, completa: bool = False) -> Obter:
    """Usa o cache quando o tamanho no servidor não mudou; senão baixa e guarda no cache."""

    def obter(arq: ArquivoZona, tamanho_conhecido: int | None) -> Obtido:
        cache = caminho_cache(config, arq.url)
        if not completa and tamanho_conhecido and cache.exists():
            remoto = cliente.tamanho_remoto(arq.url)
            if remoto is None or remoto == tamanho_conhecido:
                return _do_cache(cache)
        try:
            caminho, tamanho, sha = cliente.baixar(arq.url, config.dir_brutos / "tmp")
        except ErroFonte:
            if cache.exists():
                log.warning("Download de %s falhou; usando a cópia local", arq.nome)
                return _do_cache(cache)
            raise
        try:
            dados = caminho.read_bytes()
        finally:
            caminho.unlink(missing_ok=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(cache, "wb", compresslevel=6) as f:
            f.write(dados)
        return Obtido(dados, tamanho, sha)

    return obter


def obter_do_cache(config: Config) -> Obter:
    def obter(arq: ArquivoZona, tamanho_conhecido: int | None) -> Obtido:
        cache = caminho_cache(config, arq.url)
        if not cache.exists():
            raise ErroFonte(f"{arq.nome} não está no cache local")
        return _do_cache(cache)

    return obter


def zonas_registradas(con: sqlite3.Connection) -> list[ArquivoZona]:
    """Arquivos por zona já usados, para refazer o complemento sem a página da ANP."""
    vistos: dict[str, ArquivoZona] = {}
    for url, nome, mes in con.execute(
        "SELECT url, nome, mes_min FROM arquivos_fonte WHERE tipo = 'zona' AND mes_min IS NOT NULL"
    ):
        base = url.split("#", 1)[0]
        vistos.setdefault(base, ArquivoZona(url=base, nome=nome, mes=mes))
    return sorted(vistos.values(), key=lambda z: z.mes, reverse=True)


# --------------------------------------------------------------------------- #
# Conferência
# --------------------------------------------------------------------------- #

def _registros(con: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    regs: dict[str, dict[str, dict]] = {}
    for r in con.execute("SELECT * FROM arquivos_fonte WHERE tipo = 'zona'"):
        base, _, amb = r["url"].partition("#")
        regs.setdefault(base, {})[amb] = dict(r)
    return regs


def complementar(
    con: sqlite3.Connection,
    zonas: list[ArquivoZona],
    obter: Obter,
    ids: Identificadores,
    ano_limite: int,
    ao_progresso: Callable[[str], None] | None = None,
    forcar: bool = False,
) -> ResultadoComplemento:
    """Confere com o arquivo por zona os meses suspeitos e acrescenta os poços que faltam."""
    res = ResultadoComplemento()
    if not zonas:
        return res
    ultimo = con.execute(
        "SELECT MAX(c.mes) FROM cobertura c JOIN arquivos_fonte a ON a.id = c.arquivo_id AND a.tipo = 'poco'"
    ).fetchone()[0]
    if ultimo is None:
        return res
    brutas = qualidade.lacunas(con, efetivas=False)
    registros = _registros(con)
    por_mes: dict[int, list[ArquivoZona]] = {}
    for z in zonas:
        por_mes.setdefault(z.mes, []).append(z)

    alvo = {m for meses in brutas.values() for m in meses}
    alvo |= {m for m in por_mes if m // 100 >= ano_limite}
    alvo |= {r["mes_min"] for regs in registros.values() for r in regs.values() if r["status"] != "ok"}
    meses = sorted((m for m in alvo if m and m <= ultimo and m in por_mes), reverse=True)

    nomes = _Nomes(con)
    for i, mes in enumerate(meses):
        if ao_progresso:
            ao_progresso(f"Conferindo {rotulo_mes(mes)} com o arquivo de produção por zona ({i + 1}/{len(meses)})")
        for arq in por_mes[mes]:
            regs = registros.get(arq.url, {})
            tamanho = next((r["tamanho"] for r in regs.values() if r.get("tamanho")), None)
            try:
                obtido = obter(arq, tamanho)
            except ErroFonte as exc:
                res.erros.append(f"{arq.nome}: {exc}")
                continue
            if (
                not forcar
                and set(regs) == set(qualidade.AMBIENTES)
                and all(r["status"] in ("ok", "sem_dados") and r["sha256"] == obtido.sha256 for r in regs.values())
            ):
                res.meses_conferidos += 1  # nada mudou desde a última conferência
                break
            try:
                leitura = ler_producao_zona(obtido.dados)
            except FormatoInvalido as exc:
                res.erros.append(f"{arq.nome}: formato não reconhecido ({exc})")
                continue
            do_mes = leitura.dados[leitura.dados["mes"] == mes]
            if do_mes.empty:
                achados = ", ".join(rotulo_mes(int(m)) for m in sorted(set(leitura.dados["mes"])))
                res.avisos.append(
                    f"{arq.nome} deveria trazer {rotulo_mes(mes)} e traz {achados or 'nenhum mês'}; arquivo ignorado"
                )
                _gravar_mes(con, arq, mes, {}, obtido, leitura.codificacao, ids, nomes,
                            status_padrao="erro", erro=f"o arquivo não traz {rotulo_mes(mes)}")
                continue
            acrescentados = _conferir_mes(con, arq, mes, do_mes, obtido, leitura.codificacao, ids, nomes)
            res.meses_conferidos += 1
            if acrescentados:
                res.meses_completados.append(mes)
            break
    res.meses_completados.sort()
    return res


def _presentes(con: sqlite3.Connection, mes: int) -> tuple[set[str], dict[str, float], dict[str, int]]:
    """Poços do arquivo por poço no mês (chave do nome) e petróleo + gás por ambiente."""
    pocos: set[str] = set()
    hc: dict[str, float] = {}
    linhas: dict[str, int] = {}
    for nome, amb, valor in con.execute(
        "SELECT po.nome, a.ambiente, COALESCE(p.oleo,0)+COALESCE(p.condensado,0)"
        "+COALESCE(p.gas_assoc,0)+COALESCE(p.gas_nao_assoc,0) "
        "FROM producao p JOIN arquivos_fonte a ON a.id = p.arquivo_id AND a.tipo = 'poco' "
        "JOIN pocos po ON po.id = p.poco_id WHERE p.mes = ?",
        (mes,),
    ):
        pocos.add(chave_nome(nome))
        hc[amb] = hc.get(amb, 0.0) + float(valor)
        linhas[amb] = linhas.get(amb, 0) + 1
    return pocos, hc, linhas


def _conferir_mes(
    con: sqlite3.Connection,
    arq: ArquivoZona,
    mes: int,
    do_mes: pd.DataFrame,
    obtido: Obtido,
    codificacao: str,
    ids: Identificadores,
    nomes: "_Nomes",
) -> int:
    presentes, hc_poco, linhas_poco = _presentes(con, mes)
    novos_por_amb: dict[str, pd.DataFrame | None] = {}
    for amb in qualidade.AMBIENTES:
        z = do_mes[do_mes["ambiente"] == amb]
        if z.empty:
            novos_por_amb[amb] = None  # o arquivo por zona não tem este ambiente: nada a conferir
            continue
        if not linhas_poco.get(amb):
            novos_por_amb[amb] = z
            continue
        hc_zona = float(z[_HC].fillna(0).to_numpy().sum())
        falta = (hc_zona - hc_poco.get(amb, 0.0)) / hc_zona if hc_zona > 0 else 0.0
        if falta <= LIMIAR_FALTA:
            novos_por_amb[amb] = z.iloc[0:0]
            continue
        faltando = z[~z["poco"].map(chave_nome).isin(presentes)]
        com_volume = faltando[list(COLUNAS_VOLUME)].fillna(0).ne(0).any(axis=1)
        novos_por_amb[amb] = faltando[com_volume]
    _gravar_mes(con, arq, mes, novos_por_amb, obtido, codificacao, ids, nomes)
    return sum(len(n) for n in novos_por_amb.values() if n is not None)


# --------------------------------------------------------------------------- #
# Gravação
# --------------------------------------------------------------------------- #

class _Nomes:
    """Casa nomes do arquivo por zona com os já gravados (sem acento, maiúsculas)."""

    def __init__(self, con: sqlite3.Connection):
        self.campos = {chave: nome for nome, chave in con.execute("SELECT nome, chave FROM campos")}
        self.pocos = {chave_nome(nome): nome for (nome,) in con.execute("SELECT nome FROM pocos")}

    def padronizar(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["campo"] = df["campo"].map(lambda n: self.campos.setdefault(chave_nome(n), n))
        df["poco"] = df["poco"].map(lambda n: self.pocos.setdefault(chave_nome(n), n))
        return df


def _instalacoes_vizinhas(con: sqlite3.Connection, mes: int, pocos: set[int]) -> dict[int, int]:
    """Instalação de cada poço no arquivo por poço, no mês mais próximo (o anterior primeiro)."""
    meses = [somar_meses(mes, k) for k in VIZINHOS]
    ph = ",".join("?" * len(meses))
    por_poco: dict[int, dict[int, int]] = {}
    for poco_id, m, inst in con.execute(
        "SELECT p.poco_id, p.mes, p.instalacao_id FROM producao p "
        "JOIN arquivos_fonte a ON a.id = p.arquivo_id AND a.tipo = 'poco' "
        f"WHERE p.mes IN ({ph})",
        meses,
    ):
        if poco_id in pocos:
            por_poco.setdefault(poco_id, {}).setdefault(m, inst)
    escolhida: dict[int, int] = {}
    for poco_id, achados in por_poco.items():
        for m in meses:
            if m in achados:
                escolhida[poco_id] = achados[m]
                break
    return escolhida


def _registrar(
    con: sqlite3.Connection, arq: ArquivoZona, amb: str, mes: int, obtido: Obtido,
    codificacao: str | None, linhas: int, status: str, erro: str | None,
) -> int:
    url = f"{arq.url}#{amb}"
    momento = agora()
    con.execute(
        "INSERT INTO arquivos_fonte (url, nome, ambiente, ano_ref, sub_periodo, tamanho, sha256, verificado_em, "
        "processado_em, linhas, linhas_descartadas, mes_min, mes_max, codificacao, status, erro, tipo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,'zona') ON CONFLICT(url) DO UPDATE SET "
        "nome = excluded.nome, ambiente = excluded.ambiente, ano_ref = excluded.ano_ref, "
        "sub_periodo = excluded.sub_periodo, tamanho = excluded.tamanho, sha256 = excluded.sha256, "
        "verificado_em = excluded.verificado_em, processado_em = excluded.processado_em, "
        "linhas = excluded.linhas, mes_min = excluded.mes_min, mes_max = excluded.mes_max, "
        "codificacao = excluded.codificacao, status = excluded.status, erro = excluded.erro, tipo = 'zona'",
        (url, arq.nome, amb, mes // 100, mes % 100, obtido.tamanho, obtido.sha256, momento, momento,
         linhas, mes, mes, codificacao, status, erro),
    )
    return con.execute("SELECT id FROM arquivos_fonte WHERE url = ?", (url,)).fetchone()[0]


def _gravar_mes(
    con: sqlite3.Connection,
    arq: ArquivoZona,
    mes: int,
    novos_por_amb: dict[str, pd.DataFrame | None],
    obtido: Obtido,
    codificacao: str | None,
    ids: Identificadores,
    nomes: _Nomes,
    status_padrao: str = "ok",
    erro: str | None = None,
) -> None:
    """Troca o complemento do mês pelo novo, numa transação só.

    Com ``status_padrao`` diferente de 'ok' (arquivo com problema) só sai o que
    este mesmo arquivo tinha gravado; o complemento vindo de outro arquivo fica."""
    try:
        with transacao(con):
            antigos = con.execute(
                "SELECT id, url FROM arquivos_fonte WHERE tipo = 'zona' AND mes_min = ?", (mes,)
            ).fetchall()
            proprios = [r["id"] for r in antigos if r["url"].split("#", 1)[0] == arq.url]
            outros = [r["id"] for r in antigos if r["url"].split("#", 1)[0] != arq.url]
            remover = proprios + (outros if status_padrao == "ok" else [])
            for lote in _lotes(remover):
                ph = ",".join("?" * len(lote))
                con.execute(f"DELETE FROM producao WHERE mes = ? AND arquivo_id IN ({ph})", [mes, *lote])
                con.execute(f"DELETE FROM cobertura WHERE mes = ? AND arquivo_id IN ({ph})", [mes, *lote])
            if status_padrao == "ok":
                for lote in _lotes(outros):
                    con.execute(
                        f"UPDATE arquivos_fonte SET status = 'substituido', linhas = 0 "
                        f"WHERE id IN ({','.join('?' * len(lote))})",
                        lote,
                    )

            gravados = []
            for amb in qualidade.AMBIENTES:
                novos = novos_por_amb.get(amb)
                if status_padrao != "ok":
                    status = status_padrao
                elif novos is None:
                    status = "sem_dados"
                else:
                    status = "ok"
                n = 0 if novos is None else len(novos)
                arquivo_id = _registrar(con, arq, amb, mes, obtido, codificacao, n, status, erro)
                if n:
                    gravados.append(_inserir(con, arquivo_id, amb, mes, nomes.padronizar(novos), ids))
            recalcular_agregados(con, [mes])
            for df, mapa in gravados:
                _atualizar_metadados_campos(con, df, mapa)
    except BaseException:
        ids.recarregar()
        raise


def _inserir(
    con: sqlite3.Connection, arquivo_id: int, amb: str, mes: int, df: pd.DataFrame, ids: Identificadores
) -> tuple[pd.DataFrame, dict]:
    mapa_campos = ids.garantir("campos", df["campo"].unique())
    mapa_pocos = ids.garantir("pocos", df["poco"].unique())
    nao_informado = ids.garantir("instalacoes", [NAO_INFORMADO])[NAO_INFORMADO]
    campo_id = df["campo"].map(mapa_campos)
    poco_id = df["poco"].map(mapa_pocos)
    herdadas = _instalacoes_vizinhas(con, mes, set(poco_id.tolist()))
    inst_id = poco_id.map(lambda p: herdadas.get(p, nao_informado))
    volumes = df[list(COLUNAS_VOLUME)].astype(object)
    volumes = volumes.where(pd.notna(volumes), None)
    cols = ", ".join(COLUNAS_VOLUME)
    con.executemany(
        f"INSERT INTO producao (arquivo_id, mes, campo_id, poco_id, instalacao_id, {cols}) "
        f"VALUES (?,?,?,?,?,{','.join('?' * len(COLUNAS_VOLUME))})",
        zip(
            [arquivo_id] * len(df),
            [mes] * len(df),
            campo_id.tolist(),
            poco_id.tolist(),
            inst_id.tolist(),
            *[volumes[c].tolist() for c in COLUNAS_VOLUME],
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO poco_campo (poco_id, campo_id) VALUES (?, ?)",
        set(zip(poco_id.tolist(), campo_id.tolist())),
    )
    con.execute(
        "INSERT INTO cobertura (ambiente, mes, arquivo_id, linhas) VALUES (?,?,?,?) "
        "ON CONFLICT(ambiente, mes, arquivo_id) DO UPDATE SET linhas = excluded.linhas",
        (amb, mes, arquivo_id, len(df)),
    )
    df = df.assign(ambiente=amb)
    return df, mapa_campos

