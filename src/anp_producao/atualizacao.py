"""Atualização da base a partir do site da ANP.

Fluxo de uma atualização:

1. Lê a página de dados abertos e descobre os arquivos (nomes não têm padrão).
2. Atualiza os cadastros de campos e de concessionários.
3. Para cada arquivo de produção, do mais novo para o mais antigo:
   * arquivos novos, com erro anterior ou dentro da janela de revisão (ano
     corrente e anterior, por padrão) são baixados sempre;
   * arquivos antigos só são baixados se o tamanho no servidor mudou;
   * o SHA-256 decide se o conteúdo mudou de fato.
4. Cada arquivo alterado é gravado numa transação própria: apaga as linhas
   antigas dele, grava as novas, recalcula os agregados dos meses afetados e
   registra as revisões (meses que já existiam e mudaram de valor).
5. Arquivos que sumiram da página têm suas linhas removidas.
6. Meses que faltam ou vieram incompletos no arquivo por poço são conferidos e
   completados com o arquivo mensal de produção por zona (complemento.py).

Uma trava de arquivo impede duas atualizações ao mesmo tempo, mesmo vindas de
processos diferentes (site, linha de comando e agente).
"""

from __future__ import annotations

import gzip
import logging
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from anp_producao import cadastro
from anp_producao.config import Config
from anp_producao.db import COLUNAS_VOLUME, conectar_anp, gravar_meta, transacao
from anp_producao.fonte_anp import ArquivoProducao, ClienteANP, ErroFonte
from anp_producao.leitor_csv import FormatoInvalido, chave_nome, ler_producao

log = logging.getLogger(__name__)

VARIAVEIS_REVISAO = ("petroleo", "gas", "agua")


class AtualizacaoEmAndamento(RuntimeError):
    """Já existe uma atualização rodando (neste ou em outro processo)."""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _lotes(itens: list, tamanho: int = 400):
    for i in range(0, len(itens), tamanho):
        yield itens[i : i + tamanho]


# --------------------------------------------------------------------------- #
# Trava entre processos
# --------------------------------------------------------------------------- #

NO_WINDOWS = os.name == "nt"
if NO_WINDOWS:
    import msvcrt
else:
    import fcntl


def _travar(arquivo) -> None:
    """Trava exclusiva sem esperar: flock no Mac e Linux, msvcrt no Windows (primeiro byte)."""
    if NO_WINDOWS:
        arquivo.seek(0)
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _destravar(arquivo) -> None:
    if NO_WINDOWS:
        arquivo.seek(0)
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)


class Trava:
    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._arquivo = None

    def __enter__(self) -> "Trava":
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo = open(self.caminho, "a+", encoding="utf-8")
        try:
            _travar(self._arquivo)
        except OSError as exc:  # BlockingIOError no Mac e Linux, OSError no Windows
            self._arquivo.close()
            self._arquivo = None
            raise AtualizacaoEmAndamento("Já existe uma atualização da base em andamento") from exc
        try:  # o número do processo é só informativo
            self._arquivo.seek(0)
            self._arquivo.truncate()
            self._arquivo.write(str(os.getpid()))
            self._arquivo.flush()
        except OSError:
            pass
        return self

    def __exit__(self, *exc) -> None:
        if self._arquivo:
            try:
                _destravar(self._arquivo)
            finally:
                self._arquivo.close()
                self._arquivo = None


def atualizacao_em_andamento(config: Config) -> bool:
    try:
        with Trava(config.arquivo_trava):
            return False
    except AtualizacaoEmAndamento:
        return True


def iniciar_em_segundo_plano(config: Config, origem: str = "web", completa: bool = False) -> dict:
    """Dispara a atualização num processo separado, que continua mesmo se quem pediu fechar."""
    config.garantir_pastas()
    if atualizacao_em_andamento(config):
        return {"iniciada": False, "motivo": "Já existe uma atualização em andamento"}
    comando = [sys.executable, "-m", "anp_producao", "atualizar", "--origem", origem]
    if completa:
        comando.append("--completa")
    ambiente = os.environ.copy()
    ambiente["ANP_DIR_DADOS"] = str(config.dir_dados)
    ambiente["ANP_URL_PAGINA"] = config.url_pagina
    ambiente["PYTHONIOENCODING"] = "utf-8"  # no Windows a saída redirecionada seria cp1252
    if NO_WINDOWS:
        # Processo sem janela e fora do grupo do site: continua se a janela do site fechar.
        destacado = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) | getattr(subprocess, "DETACHED_PROCESS", 0x8)
        opcoes = {"creationflags": destacado}
    else:
        opcoes = {"start_new_session": True}
    with open(config.dir_logs / "atualizacao.log", "ab") as saida:
        subprocess.Popen(
            comando,
            stdout=saida,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=ambiente,
            **opcoes,
        )
    return {"iniciada": True}


# --------------------------------------------------------------------------- #
# Progresso
# --------------------------------------------------------------------------- #

class Progresso:
    def __init__(self, con: sqlite3.Connection, atualizacao_id: int, callback: Callable | None):
        self.con = con
        self.id = atualizacao_id
        self.callback = callback

    def registrar(self, etapa: str, progresso: float, mensagem: str) -> None:
        self.con.execute(
            "UPDATE atualizacoes SET etapa = ?, progresso = ?, mensagem = ? WHERE id = ?",
            (etapa, round(progresso, 1), mensagem, self.id),
        )
        log.info("[%5.1f%%] %s", progresso, mensagem)
        if self.callback:
            self.callback(etapa, progresso, mensagem)


# --------------------------------------------------------------------------- #
# Identificadores (nome -> id) com cache
# --------------------------------------------------------------------------- #

class Identificadores:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.recarregar()

    def recarregar(self) -> None:
        self.campos = {n: i for i, n in self.con.execute("SELECT id, nome FROM campos")}
        self.pocos = {n: i for i, n in self.con.execute("SELECT id, nome FROM pocos")}
        self.instalacoes = {n: i for i, n in self.con.execute("SELECT id, nome FROM instalacoes")}

    def garantir(self, tabela: str, nomes) -> dict[str, int]:
        cache = getattr(self, tabela)
        for nome in nomes:
            if nome in cache:
                continue
            if tabela == "campos":
                cur = self.con.execute(
                    "INSERT INTO campos (nome, chave) VALUES (?, ?)", (nome, chave_nome(nome))
                )
            else:
                cur = self.con.execute(f"INSERT INTO {tabela} (nome) VALUES (?)", (nome,))
            cache[nome] = cur.lastrowid
        return cache


# --------------------------------------------------------------------------- #
# Processamento de um arquivo
# --------------------------------------------------------------------------- #

@dataclass
class ResultadoArquivo:
    linhas: int = 0
    descartadas: int = 0
    meses: list[int] = field(default_factory=list)
    revisoes: int = 0
    avisos: list[str] = field(default_factory=list)


_SOMAS = ", ".join(f"SUM({c})" for c in COLUNAS_VOLUME)
_COLS = ", ".join(COLUNAS_VOLUME)
_PRODUZ = "COALESCE(oleo,0)+COALESCE(condensado,0)+COALESCE(gas_assoc,0)+COALESCE(gas_nao_assoc,0)"
_INJETA = (
    "COALESCE(inj_gas,0)+COALESCE(inj_agua_rec,0)+COALESCE(inj_agua_desc,0)+COALESCE(inj_co2,0)"
    "+COALESCE(inj_n2,0)+COALESCE(inj_vapor,0)+COALESCE(inj_polimeros,0)+COALESCE(inj_outros,0)"
)
_CONTAGENS = (
    f"COUNT(DISTINCT CASE WHEN {_PRODUZ} > 0 THEN poco_id END), "
    f"COUNT(DISTINCT CASE WHEN {_INJETA} > 0 THEN poco_id END), COUNT(*)"
)


def recalcular_agregados(con: sqlite3.Connection, meses: list[int]) -> None:
    for lote in _lotes(sorted(set(meses))):
        ph = ",".join("?" * len(lote))
        con.execute(f"DELETE FROM producao_campo_mes WHERE mes IN ({ph})", lote)
        con.execute(
            f"INSERT INTO producao_campo_mes (campo_id, mes, {_COLS}, n_produtores, n_injetores, n_linhas) "
            f"SELECT campo_id, mes, {_SOMAS}, {_CONTAGENS} FROM producao "
            f"WHERE mes IN ({ph}) GROUP BY campo_id, mes",
            lote,
        )
        con.execute(f"DELETE FROM producao_instalacao_mes WHERE mes IN ({ph})", lote)
        con.execute(
            f"INSERT INTO producao_instalacao_mes (instalacao_id, campo_id, mes, {_COLS}, "
            f"n_produtores, n_injetores, n_linhas) "
            f"SELECT instalacao_id, campo_id, mes, {_SOMAS}, {_CONTAGENS} FROM producao "
            f"WHERE mes IN ({ph}) GROUP BY instalacao_id, campo_id, mes",
            lote,
        )


def _foto_agregados(con: sqlite3.Connection, meses: list[int]) -> dict[tuple[int, int], tuple]:
    """Petróleo, gás e água por campo e mês, só dos arquivos por poço.

    O complemento por zona fica de fora: ele sai e volta quando o arquivo por
    poço é regravado, e isso não é revisão da ANP."""
    foto = {}
    for lote in _lotes(sorted(set(meses))):
        ph = ",".join("?" * len(lote))
        for r in con.execute(
            "SELECT p.campo_id, p.mes, SUM(COALESCE(p.oleo,0)+COALESCE(p.condensado,0)), "
            "SUM(COALESCE(p.gas_assoc,0)+COALESCE(p.gas_nao_assoc,0)), SUM(COALESCE(p.agua,0)) "
            "FROM producao p JOIN arquivos_fonte a ON a.id = p.arquivo_id AND a.tipo = 'poco' "
            f"WHERE p.mes IN ({ph}) GROUP BY p.campo_id, p.mes",
            lote,
        ):
            foto[(r[0], r[1])] = (r[2], r[3], r[4])
    return foto


def _registrar_revisoes(
    con: sqlite3.Connection, atualizacao_id: int, antes: dict, depois: dict
) -> int:
    linhas = []
    for chave in set(antes) | set(depois):
        a = antes.get(chave, (0.0, 0.0, 0.0))
        d = depois.get(chave, (0.0, 0.0, 0.0))
        for i, variavel in enumerate(VARIAVEIS_REVISAO):
            dif = abs(d[i] - a[i])
            if dif > 1.0 and dif > 0.001 * max(abs(a[i]), abs(d[i])):
                linhas.append((atualizacao_id, chave[0], chave[1], variavel, a[i], d[i]))
    con.executemany(
        "INSERT INTO revisoes (atualizacao_id, campo_id, mes, variavel, antes, depois) "
        "VALUES (?,?,?,?,?,?)",
        linhas,
    )
    return len(linhas)


def _soltar_complementos(con: sqlite3.Connection, ambiente: str, meses) -> None:
    """Tira o complemento por zona dos meses que um arquivo por poço acabou de regravar.

    Roda dentro da transação de quem chama, para nunca somar os dois. Os meses
    ficam 'pendente' e o fim da atualização confere de novo (complemento.py)."""
    for lote in _lotes(sorted(set(meses))):
        ph = ",".join("?" * len(lote))
        alvo = [r[0] for r in con.execute(
            f"SELECT id FROM arquivos_fonte WHERE tipo = 'zona' AND ambiente = ? AND mes_min IN ({ph})",
            [ambiente, *lote],
        )]
        if not alvo:
            continue
        ph_ids = ",".join("?" * len(alvo))
        con.execute(f"DELETE FROM producao WHERE mes IN ({ph}) AND arquivo_id IN ({ph_ids})", [*lote, *alvo])
        con.execute(f"DELETE FROM cobertura WHERE mes IN ({ph}) AND arquivo_id IN ({ph_ids})", [*lote, *alvo])
        con.execute(
            f"UPDATE arquivos_fonte SET status = 'pendente', linhas = 0 WHERE id IN ({ph_ids}) AND status = 'ok'",
            alvo,
        )


def _remover_linhas_de_arquivo(con: sqlite3.Connection, arquivo_id: int, meses: list[int]) -> None:
    for lote in _lotes(sorted(set(meses))):
        ph = ",".join("?" * len(lote))
        con.execute(f"DELETE FROM producao WHERE arquivo_id = ? AND mes IN ({ph})", [arquivo_id, *lote])
        con.execute(f"DELETE FROM cobertura WHERE arquivo_id = ? AND mes IN ({ph})", [arquivo_id, *lote])


def processar_arquivo(
    con: sqlite3.Connection,
    arquivo: ArquivoProducao,
    dados: bytes,
    tamanho: int,
    sha256: str,
    ids: Identificadores,
    atualizacao_id: int,
) -> ResultadoArquivo:
    """Grava um arquivo de produção (substitui o que havia dele antes)."""
    leitura = ler_producao(dados, ambiente_padrao=arquivo.ambiente)
    df = leitura.dados
    res = ResultadoArquivo(descartadas=leitura.linhas_descartadas, avisos=list(leitura.avisos))
    try:
        with transacao(con):
            reg = con.execute(
                "SELECT id FROM arquivos_fonte WHERE url = ?", (arquivo.url,)
            ).fetchone()
            if reg:
                arquivo_id = reg["id"]
            else:
                arquivo_id = con.execute(
                    "INSERT INTO arquivos_fonte (url, nome, ambiente, ano_ref, sub_periodo, status) "
                    "VALUES (?,?,?,?,?, 'processando')",
                    (arquivo.url, arquivo.nome, arquivo.ambiente, arquivo.ano_ref, arquivo.sub_periodo),
                ).lastrowid

            # Mês repetido em outro arquivo do mesmo ambiente: vale o arquivo mais recente.
            meses_novos = sorted(int(m) for m in df["mes"].unique())
            descartar_meses: set[int] = set()
            afetados: set[int] = set()
            for lote in _lotes(meses_novos):
                ph = ",".join("?" * len(lote))
                for c in con.execute(
                    "SELECT c.mes, c.arquivo_id, a.nome, a.ano_ref, a.sub_periodo FROM cobertura c "
                    "JOIN arquivos_fonte a ON a.id = c.arquivo_id AND a.tipo = 'poco' "
                    f"WHERE c.ambiente = ? AND c.arquivo_id != ? AND c.mes IN ({ph})",
                    [arquivo.ambiente, arquivo_id, *lote],
                ).fetchall():
                    outro = (c["ano_ref"] or 0, c["sub_periodo"] or 0)
                    if arquivo.ordem >= outro:
                        _remover_linhas_de_arquivo(con, c["arquivo_id"], [c["mes"]])
                        afetados.add(c["mes"])
                        res.avisos.append(
                            f"{c['mes']} também estava em {c['nome']}; mantido o arquivo mais recente"
                        )
                    else:
                        descartar_meses.add(c["mes"])
                        res.avisos.append(
                            f"{c['mes']} ignorado aqui; já vem de {c['nome']}, que é mais recente"
                        )
            if descartar_meses:
                df = df[~df["mes"].isin(descartar_meses)]

            meses_antigos = [
                r[0] for r in con.execute(
                    "SELECT DISTINCT mes FROM cobertura WHERE arquivo_id = ?", (arquivo_id,)
                )
            ]
            antes = _foto_agregados(con, meses_antigos) if meses_antigos else {}
            _remover_linhas_de_arquivo(con, arquivo_id, meses_antigos)

            mapa_campos = ids.garantir("campos", df["campo"].unique())
            mapa_pocos = ids.garantir("pocos", df["poco"].unique())
            mapa_inst = ids.garantir("instalacoes", df["instalacao"].unique())
            campo_id = df["campo"].map(mapa_campos)
            poco_id = df["poco"].map(mapa_pocos)
            inst_id = df["instalacao"].map(mapa_inst)
            volumes = df[list(COLUNAS_VOLUME)].astype(object)
            volumes = volumes.where(pd.notna(volumes), None)
            registros = zip(
                [arquivo_id] * len(df),
                df["mes"].tolist(),
                campo_id.tolist(),
                poco_id.tolist(),
                inst_id.tolist(),
                *[volumes[c].tolist() for c in COLUNAS_VOLUME],
            )
            con.executemany(
                f"INSERT INTO producao (arquivo_id, mes, campo_id, poco_id, instalacao_id, {_COLS}) "
                f"VALUES (?,?,?,?,?,{','.join('?' * len(COLUNAS_VOLUME))})",
                registros,
            )
            con.executemany(
                "INSERT OR IGNORE INTO poco_campo (poco_id, campo_id) VALUES (?, ?)",
                set(zip(poco_id.tolist(), campo_id.tolist())),
            )
            cobertura = df.groupby(["ambiente", "mes"]).size()
            con.executemany(
                "INSERT INTO cobertura (ambiente, mes, arquivo_id, linhas) VALUES (?,?,?,?) "
                "ON CONFLICT(ambiente, mes, arquivo_id) DO UPDATE SET linhas = excluded.linhas",
                [(a, int(m), arquivo_id, int(n)) for (a, m), n in cobertura.items()],
            )

            afetados |= set(meses_antigos) | {int(m) for m in df["mes"].unique()}
            _soltar_complementos(con, arquivo.ambiente, afetados)
            recalcular_agregados(con, sorted(afetados))
            if antes:
                depois = _foto_agregados(con, meses_antigos)
                res.revisoes = _registrar_revisoes(con, atualizacao_id, antes, depois)

            _atualizar_metadados_campos(con, df, mapa_campos)
            con.execute(
                "UPDATE arquivos_fonte SET nome = ?, ambiente = ?, ano_ref = ?, sub_periodo = ?, "
                "tamanho = ?, sha256 = ?, verificado_em = ?, processado_em = ?, linhas = ?, "
                "linhas_descartadas = ?, mes_min = ?, mes_max = ?, codificacao = ?, status = 'ok', "
                "erro = NULL WHERE id = ?",
                (
                    arquivo.nome, arquivo.ambiente, arquivo.ano_ref, arquivo.sub_periodo,
                    tamanho, sha256, agora(), agora(), len(df), res.descartadas,
                    int(df["mes"].min()) if len(df) else None,
                    int(df["mes"].max()) if len(df) else None,
                    leitura.codificacao, arquivo_id,
                ),
            )
    except BaseException:
        ids.recarregar()  # ids criados na transação desfeita não existem mais
        raise
    res.linhas = len(df)
    res.meses = sorted(afetados)
    return res


def _atualizar_metadados_campos(con: sqlite3.Connection, df: pd.DataFrame, mapa: dict) -> None:
    resumo = df.groupby("campo").agg(
        bacia=("bacia", "first"), estado=("estado", "first"), ambientes=("ambiente", lambda s: set(s))
    )
    for nome, linha in resumo.iterrows():
        cid = mapa[nome]
        atual = con.execute(
            "SELECT bacia, estado, ambientes FROM campos WHERE id = ?", (cid,)
        ).fetchone()
        ambientes = set(filter(None, (atual["ambientes"] or "").split(","))) | linha["ambientes"]
        con.execute(
            "UPDATE campos SET bacia = COALESCE(bacia, NULLIF(?, '')), "
            "estado = COALESCE(estado, NULLIF(?, '')), ambientes = ? WHERE id = ?",
            (linha["bacia"], linha["estado"], ",".join(sorted(ambientes)), cid),
        )


def _remover_arquivo(con: sqlite3.Connection, arquivo_id: int) -> None:
    with transacao(con):
        meses = [r[0] for r in con.execute("SELECT DISTINCT mes FROM cobertura WHERE arquivo_id = ?", (arquivo_id,))]
        ambiente = con.execute("SELECT ambiente FROM arquivos_fonte WHERE id = ?", (arquivo_id,)).fetchone()[0]
        _remover_linhas_de_arquivo(con, arquivo_id, meses)
        con.execute("DELETE FROM producao WHERE arquivo_id = ?", (arquivo_id,))
        _soltar_complementos(con, ambiente, meses)
        recalcular_agregados(con, meses)
        con.execute("UPDATE arquivos_fonte SET status = 'removido' WHERE id = ?", (arquivo_id,))


def _fechar_intervalos_campos(con: sqlite3.Connection) -> None:
    con.execute(
        "UPDATE campos SET primeiro_mes = (SELECT MIN(mes) FROM producao_campo_mes p WHERE p.campo_id = campos.id), "
        "ultimo_mes = (SELECT MAX(mes) FROM producao_campo_mes p WHERE p.campo_id = campos.id)"
    )


def _guardar_bruto(config: Config, arquivo: ArquivoProducao, dados: bytes) -> None:
    pasta = config.dir_brutos / arquivo.ambiente.lower()
    pasta.mkdir(parents=True, exist_ok=True)
    with gzip.open(pasta / f"{arquivo.nome}.gz", "wb", compresslevel=6) as f:
        f.write(dados)


# --------------------------------------------------------------------------- #
# Atualização completa
# --------------------------------------------------------------------------- #

def atualizar(
    config: Config,
    origem: str = "cli",
    completa: bool = False,
    cliente: ClienteANP | None = None,
    ao_progresso: Callable[[str, float, str], None] | None = None,
) -> dict:
    """Executa uma atualização. Devolve um resumo. Levanta AtualizacaoEmAndamento se já houver uma."""
    config.garantir_pastas()
    with Trava(config.arquivo_trava):
        con = conectar_anp(config)
        try:
            con.execute(
                "UPDATE atualizacoes SET status = 'interrompida', concluida_em = ? WHERE status = 'executando'",
                (agora(),),
            )
            mes_antes = con.execute("SELECT MAX(mes) FROM producao_campo_mes").fetchone()[0]
            atualizacao_id = con.execute(
                "INSERT INTO atualizacoes (iniciada_em, origem, completa, status, etapa, progresso, "
                "mensagem, mes_max_antes, pid) VALUES (?, ?, ?, 'executando', 'inicio', 0, ?, ?, ?)",
                (agora(), origem, int(completa), "Iniciando", mes_antes, os.getpid()),
            ).lastrowid
            progresso = Progresso(con, atualizacao_id, ao_progresso)
            proprio = cliente is None
            cliente = cliente or ClienteANP(config)
            try:
                resumo = _executar(con, config, cliente, progresso, completa, atualizacao_id)
            except Exception as exc:
                log.exception("Atualização falhou")
                con.execute(
                    "UPDATE atualizacoes SET status = 'erro', concluida_em = ?, erro = ?, "
                    "mensagem = ? WHERE id = ?",
                    (agora(), str(exc), f"Falhou: {exc}", atualizacao_id),
                )
                raise
            finally:
                if proprio:
                    cliente.fechar()
            resumo["id"] = atualizacao_id
            return resumo
        finally:
            con.close()


def _executar(
    con: sqlite3.Connection,
    config: Config,
    cliente: ClienteANP,
    progresso: Progresso,
    completa: bool,
    atualizacao_id: int,
) -> dict:
    progresso.registrar("pagina", 0, "Lendo a página de dados abertos da ANP")
    pagina = cliente.pagina()
    gravar_meta(con, "pagina_atualizada_em", pagina.atualizada_em)
    gravar_meta(con, "url_pagina", config.url_pagina)

    avisos: list[str] = []
    progresso.registrar("cadastro", 1, "Atualizando cadastros de campos e concessionários")
    for url, carregar, nome in (
        (pagina.url_campos, cadastro.carregar_campos, "campos"),
        (pagina.url_concessionarios, cadastro.carregar_concessionarios, "concessionários"),
    ):
        if not url:
            avisos.append(f"Link do cadastro de {nome} não encontrado na página")
            continue
        try:
            carregar(con, cliente.baixar_bytes(url))
        except (ErroFonte, FormatoInvalido) as exc:
            avisos.append(f"Cadastro de {nome} não atualizado: {exc}")

    arquivos = pagina.arquivos
    registros = {r["url"]: dict(r) for r in con.execute("SELECT * FROM arquivos_fonte WHERE tipo = 'poco'")}
    ano_limite = datetime.now().year - config.janela_revisao_anos + 1
    ids = Identificadores(con)
    total = len(arquivos)
    alterados = linhas = revisoes = verificados = 0
    erros: list[str] = []

    for i, arq in enumerate(arquivos):
        pct = 2 + 96 * i / max(total, 1)
        reg = registros.get(arq.url)
        precisa = (
            completa
            or reg is None
            or reg.get("status") != "ok"
            or arq.ano_ref >= ano_limite
        )
        if not precisa:
            progresso.registrar("verificando", pct, f"Conferindo {arq.nome} ({i + 1}/{total})")
            tamanho_remoto = cliente.tamanho_remoto(arq.url)
            if tamanho_remoto is not None and tamanho_remoto == reg.get("tamanho"):
                con.execute(
                    "UPDATE arquivos_fonte SET verificado_em = ? WHERE id = ?", (agora(), reg["id"])
                )
                verificados += 1
                continue
        progresso.registrar("baixando", pct, f"Baixando {arq.nome} ({i + 1}/{total})")
        try:
            caminho, tamanho, sha = cliente.baixar(arq.url, config.dir_brutos / "tmp")
        except ErroFonte as exc:
            erros.append(f"{arq.nome}: {exc}")
            continue
        try:
            verificados += 1
            if reg and reg.get("status") == "ok" and reg.get("sha256") == sha:
                con.execute(
                    "UPDATE arquivos_fonte SET verificado_em = ?, tamanho = ? WHERE id = ?",
                    (agora(), tamanho, reg["id"]),
                )
                continue
            progresso.registrar("gravando", pct, f"Gravando {arq.nome} ({i + 1}/{total})")
            dados = caminho.read_bytes()
            resultado = processar_arquivo(con, arq, dados, tamanho, sha, ids, atualizacao_id)
            _guardar_bruto(config, arq, dados)
            alterados += 1
            linhas += resultado.linhas
            revisoes += resultado.revisoes
            avisos.extend(f"{arq.nome}: {a}" for a in resultado.avisos)
        except FormatoInvalido as exc:
            erros.append(f"{arq.nome}: formato não reconhecido ({exc})")
            con.execute(
                "INSERT INTO arquivos_fonte (url, nome, ambiente, ano_ref, sub_periodo, status, erro) "
                "VALUES (?,?,?,?,?, 'erro', ?) ON CONFLICT(url) DO UPDATE SET status = 'erro', "
                "erro = excluded.erro",
                (arq.url, arq.nome, arq.ambiente, arq.ano_ref, arq.sub_periodo, str(exc)),
            )
        finally:
            caminho.unlink(missing_ok=True)

    # Arquivos que sumiram da página: só remove se a página trouxe uma lista plausível.
    urls_pagina = {a.url for a in arquivos}
    antigos_ok = [r for r in registros.values() if r.get("status") == "ok"]
    sumidos = [r for r in antigos_ok if r["url"] not in urls_pagina]
    if sumidos and len(urls_pagina) >= 0.8 * len(antigos_ok):
        for r in sumidos:
            progresso.registrar("limpando", 98, f"Removendo {r['nome']}, que saiu da página da ANP")
            _remover_arquivo(con, r["id"])
            avisos.append(f"{r['nome']} saiu da página da ANP e foi removido da base")
    elif sumidos:
        avisos.append(
            f"{len(sumidos)} arquivos não apareceram na página; mantidos por segurança"
        )

    from anp_producao import complemento

    progresso.registrar("complemento", 98, "Conferindo meses com o arquivo de produção por zona")
    comp = complemento.complementar(
        con,
        pagina.zonas,
        complemento.obter_online(config, cliente, completa),
        ids,
        ano_limite,
        ao_progresso=lambda msg: progresso.registrar("complemento", 98, msg),
        forcar=completa,
    )
    erros.extend(comp.erros)
    avisos.extend(comp.avisos)

    progresso.registrar("finalizando", 99, "Finalizando")
    _fechar_intervalos_campos(con)
    mes_depois = con.execute("SELECT MAX(mes) FROM producao_campo_mes").fetchone()[0]
    status = "ok" if not erros else "ok_com_avisos"
    if erros and not alterados and not verificados:
        status = "erro"
    mensagem = _mensagem_final(alterados, mes_depois, revisoes, erros, len(comp.meses_completados))
    con.execute(
        "UPDATE atualizacoes SET status = ?, concluida_em = ?, etapa = 'concluida', progresso = 100, "
        "mensagem = ?, arquivos_total = ?, arquivos_verificados = ?, arquivos_alterados = ?, "
        "linhas = ?, mes_max_depois = ?, revisoes = ?, pagina_atualizada_em = ?, erro = ? WHERE id = ?",
        (
            status, agora(), mensagem, total, verificados, alterados, linhas, mes_depois,
            revisoes, pagina.atualizada_em, "\n".join(erros) or None, atualizacao_id,
        ),
    )
    if progresso.callback:
        progresso.callback("concluida", 100, mensagem)
    return {
        "status": status,
        "mensagem": mensagem,
        "arquivos_total": total,
        "arquivos_verificados": verificados,
        "arquivos_alterados": alterados,
        "linhas": linhas,
        "revisoes": revisoes,
        "ultimo_mes": mes_depois,
        "pagina_atualizada_em": pagina.atualizada_em,
        "meses_completados": comp.meses_completados,
        "avisos": avisos,
        "erros": erros,
    }


def _mensagem_final(
    alterados: int, mes: int | None, revisoes: int, erros: list[str], completados: int = 0
) -> str:
    from anp_producao.formatos import rotulo_mes

    partes = []
    if alterados:
        partes.append(f"{alterados} arquivo(s) novo(s) ou alterado(s)")
    elif not completados:
        partes.append("Nenhuma novidade na ANP")
    if mes:
        partes.append(f"dados até {rotulo_mes(mes)}")
    if revisoes:
        partes.append(f"{revisoes} revisão(ões) de meses anteriores")
    if completados:
        partes.append(f"{completados} mês(es) completados com o arquivo de produção por zona")
    if erros:
        partes.append(f"{len(erros)} arquivo(s) com problema")
    texto = "; ".join(partes)
    return texto[:1].upper() + texto[1:]


def reprocessar_brutos(config: Config, ao_progresso: Callable | None = None) -> dict:
    """Regrava a base a partir do cache local de arquivos brutos, sem baixar nada."""
    config.garantir_pastas()
    with Trava(config.arquivo_trava):
        con = conectar_anp(config)
        try:
            atualizacao_id = con.execute(
                "INSERT INTO atualizacoes (iniciada_em, origem, completa, status, etapa, progresso, pid) "
                "VALUES (?, 'reprocessamento', 1, 'executando', 'inicio', 0, ?)",
                (agora(), os.getpid()),
            ).lastrowid
            progresso = Progresso(con, atualizacao_id, ao_progresso)
            ids = Identificadores(con)
            regs = con.execute(
                "SELECT * FROM arquivos_fonte WHERE status IN ('ok', 'erro') AND tipo = 'poco' "
                "ORDER BY ano_ref DESC"
            ).fetchall()
            feitos = 0
            for i, r in enumerate(regs):
                bruto = config.dir_brutos / r["ambiente"].lower() / f"{r['nome']}.gz"
                if not bruto.exists():
                    continue
                progresso.registrar("gravando", 100 * i / max(len(regs), 1), f"Regravando {r['nome']}")
                dados = gzip.decompress(bruto.read_bytes())
                arq = ArquivoProducao(r["url"], r["nome"], r["ambiente"], r["ano_ref"] or 0, r["sub_periodo"] or 0)
                processar_arquivo(con, arq, dados, len(dados), r["sha256"] or "", ids, atualizacao_id)
                feitos += 1
            from anp_producao import complemento

            progresso.registrar("complemento", 99, "Refazendo o complemento por zona com o cache local")
            comp = complemento.complementar(
                con,
                complemento.zonas_registradas(con),
                complemento.obter_do_cache(config),
                ids,
                datetime.now().year - config.janela_revisao_anos + 1,
            )
            _fechar_intervalos_campos(con)
            mensagem = f"{feitos} arquivo(s) regravados do cache local"
            if comp.meses_completados:
                mensagem += f"; {len(comp.meses_completados)} mês(es) completados com o arquivo de produção por zona"
            con.execute(
                "UPDATE atualizacoes SET status = 'ok', concluida_em = ?, progresso = 100, etapa = 'concluida', "
                "mensagem = ?, arquivos_alterados = ? WHERE id = ?",
                (agora(), mensagem, feitos, atualizacao_id),
            )
            return {"status": "ok", "arquivos": feitos, "meses_completados": comp.meses_completados}
        finally:
            con.close()
