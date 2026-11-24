"""Bancos SQLite.

São dois arquivos separados de propósito:

* ``anp.sqlite`` guarda só o que vem da ANP. Pode ser apagado e reconstruído
  a qualquer momento com uma atualização completa.
* ``usuario.sqlite`` guarda o que é seu (clusters). Nunca é tocado pela
  atualização da base.

Os dois usam WAL, então o site, a linha de comando e o servidor MCP podem
ler ao mesmo tempo enquanto uma atualização grava.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from anp_producao.config import Config

VERSAO_ESQUEMA_ANP = 2
VERSAO_ESQUEMA_USUARIO = 1

COLUNAS_VOLUME = (
    "oleo",
    "condensado",
    "gas_assoc",
    "gas_nao_assoc",
    "agua",
    "inj_gas",
    "inj_agua_rec",
    "inj_agua_desc",
    "inj_co2",
    "inj_n2",
    "inj_vapor",
    "inj_polimeros",
    "inj_outros",
)

_COLS_SQL = ", ".join(f"{c} REAL" for c in COLUNAS_VOLUME)

ESQUEMA_ANP = f"""
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS arquivos_fonte (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    ambiente TEXT NOT NULL,
    ano_ref INTEGER,
    sub_periodo INTEGER,
    tamanho INTEGER,
    sha256 TEXT,
    verificado_em TEXT,
    processado_em TEXT,
    linhas INTEGER,
    linhas_descartadas INTEGER,
    mes_min INTEGER,
    mes_max INTEGER,
    codificacao TEXT,
    status TEXT,
    erro TEXT,
    tipo TEXT NOT NULL DEFAULT 'poco'
);
CREATE INDEX IF NOT EXISTS ix_arquivos_tipo ON arquivos_fonte(tipo, ambiente, mes_min);

CREATE TABLE IF NOT EXISTS campos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    chave TEXT NOT NULL,
    bacia TEXT,
    estado TEXT,
    ambientes TEXT,
    primeiro_mes INTEGER,
    ultimo_mes INTEGER
);
CREATE INDEX IF NOT EXISTS ix_campos_chave ON campos(chave);

CREATE TABLE IF NOT EXISTS pocos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS instalacoes (id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE);

CREATE TABLE IF NOT EXISTS producao (
    arquivo_id INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    campo_id INTEGER NOT NULL,
    poco_id INTEGER NOT NULL,
    instalacao_id INTEGER NOT NULL,
    {_COLS_SQL}
);
CREATE INDEX IF NOT EXISTS ix_producao_campo_mes ON producao(campo_id, mes);
CREATE INDEX IF NOT EXISTS ix_producao_mes ON producao(mes);

CREATE TABLE IF NOT EXISTS poco_campo (
    poco_id INTEGER NOT NULL,
    campo_id INTEGER NOT NULL,
    PRIMARY KEY (poco_id, campo_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS producao_campo_mes (
    campo_id INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    {_COLS_SQL},
    n_produtores INTEGER,
    n_injetores INTEGER,
    n_linhas INTEGER,
    PRIMARY KEY (campo_id, mes)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pcm_mes ON producao_campo_mes(mes);

CREATE TABLE IF NOT EXISTS producao_instalacao_mes (
    instalacao_id INTEGER NOT NULL,
    campo_id INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    {_COLS_SQL},
    n_produtores INTEGER,
    n_injetores INTEGER,
    n_linhas INTEGER,
    PRIMARY KEY (instalacao_id, campo_id, mes)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pim_campo ON producao_instalacao_mes(campo_id, mes);
CREATE INDEX IF NOT EXISTS ix_pim_mes ON producao_instalacao_mes(mes);

CREATE TABLE IF NOT EXISTS cobertura (
    ambiente TEXT NOT NULL,
    mes INTEGER NOT NULL,
    arquivo_id INTEGER NOT NULL,
    linhas INTEGER NOT NULL,
    PRIMARY KEY (ambiente, mes, arquivo_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS cadastro_campos (
    codigo INTEGER,
    nome TEXT,
    chave TEXT,
    sigla TEXT,
    bacia TEXT,
    estado TEXT,
    operador TEXT,
    ambiente TEXT,
    rodada TEXT,
    contrato TEXT,
    situacao TEXT,
    inicio_producao TEXT,
    termino_producao TEXT
);
CREATE INDEX IF NOT EXISTS ix_cadastro_chave ON cadastro_campos(chave);

CREATE TABLE IF NOT EXISTS concessionarios (
    codigo INTEGER,
    campo TEXT,
    chave TEXT,
    empresa TEXT,
    cnpj TEXT,
    participacao REAL,
    operador INTEGER,
    rodada TEXT,
    situacao TEXT,
    bacia TEXT,
    ambiente TEXT,
    estado TEXT
);
CREATE INDEX IF NOT EXISTS ix_concessionarios_chave ON concessionarios(chave);
CREATE INDEX IF NOT EXISTS ix_concessionarios_empresa ON concessionarios(empresa);

CREATE TABLE IF NOT EXISTS atualizacoes (
    id INTEGER PRIMARY KEY,
    iniciada_em TEXT NOT NULL,
    concluida_em TEXT,
    origem TEXT,
    completa INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    etapa TEXT,
    progresso REAL,
    mensagem TEXT,
    arquivos_total INTEGER,
    arquivos_verificados INTEGER,
    arquivos_alterados INTEGER,
    linhas INTEGER,
    mes_max_antes INTEGER,
    mes_max_depois INTEGER,
    revisoes INTEGER,
    pagina_atualizada_em TEXT,
    erro TEXT,
    pid INTEGER
);

CREATE TABLE IF NOT EXISTS revisoes (
    id INTEGER PRIMARY KEY,
    atualizacao_id INTEGER NOT NULL,
    campo_id INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    variavel TEXT NOT NULL,
    antes REAL,
    depois REAL,
    vista INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_revisoes_atualizacao ON revisoes(atualizacao_id);
"""

ESQUEMA_USUARIO = """
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descricao TEXT NOT NULL DEFAULT '',
    cor TEXT,
    regra TEXT NOT NULL DEFAULT '{}',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_campos (
    cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    campo TEXT NOT NULL,
    modo TEXT NOT NULL DEFAULT 'incluir' CHECK (modo IN ('incluir', 'excluir')),
    PRIMARY KEY (cluster_id, campo, modo)
);

CREATE TABLE IF NOT EXISTS preferencias (chave TEXT PRIMARY KEY, valor TEXT);
"""


def _conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: nós controlamos as transações (ver ``transacao``).
    con = sqlite3.connect(
        caminho, timeout=30, isolation_level=None, check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-65536")
    return con


def _migrar(con: sqlite3.Connection, esquema: str, versao: int) -> None:
    atual = con.execute("PRAGMA user_version").fetchone()[0]
    if atual >= versao:
        return
    con.executescript(esquema)
    con.execute(f"PRAGMA user_version={versao}")


# Passos para bases criadas por versões anteriores (a chave é a versão de destino).
# arquivos_fonte.tipo: 'poco' (arquivos de produção por poço) ou 'zona' (complementos
# vindos do arquivo mensal de produção por zona, ver complemento.py).
MIGRACOES_ANP = {
    2: ("ALTER TABLE arquivos_fonte ADD COLUMN tipo TEXT NOT NULL DEFAULT 'poco'",),
}


def _migrar_anp(con: sqlite3.Connection) -> None:
    atual = con.execute("PRAGMA user_version").fetchone()[0]
    if atual >= VERSAO_ESQUEMA_ANP:
        return
    if atual > 0:
        for versao in range(atual + 1, VERSAO_ESQUEMA_ANP + 1):
            for comando in MIGRACOES_ANP.get(versao, ()):
                try:
                    con.execute(comando)
                except sqlite3.OperationalError as exc:
                    # Outro processo (site, linha de comando ou agente) migrou antes.
                    if "duplicate column" not in str(exc):
                        raise
    con.executescript(ESQUEMA_ANP)
    con.execute(f"PRAGMA user_version={VERSAO_ESQUEMA_ANP}")


def conectar_anp(config: Config) -> sqlite3.Connection:
    con = _conectar(config.banco_anp)
    _migrar_anp(con)
    return con


def conectar_usuario(config: Config) -> sqlite3.Connection:
    con = _conectar(config.banco_usuario)
    _migrar(con, ESQUEMA_USUARIO, VERSAO_ESQUEMA_USUARIO)
    return con


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação explícita. Tudo dentro do bloco grava junto ou nada grava."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def ler_meta(con: sqlite3.Connection, chave: str, padrao: str | None = None) -> str | None:
    linha = con.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return linha[0] if linha else padrao


def gravar_meta(con: sqlite3.Connection, chave: str, valor: str | None) -> None:
    con.execute(
        "INSERT INTO meta (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )
