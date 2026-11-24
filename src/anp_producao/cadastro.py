"""Cadastros de campos e de concessionários publicados pela ANP.

Os dois arquivos são pequenos (algumas centenas de linhas) e são substituídos
inteiros a cada atualização. A participação de cada empresa é a vigente na
data da publicação; a ANP não publica o histórico de participações.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from anp_producao.db import transacao
from anp_producao.leitor_csv import (
    FormatoInvalido,
    chave_nome,
    ler_tabela_simples,
    numero_brasileiro,
)


def _data_iso(texto: str | None) -> str | None:
    if not texto or texto.strip() in ("-", ""):
        return None
    partes = texto.strip().split("/")
    if len(partes) == 3 and all(p.isdigit() for p in partes):
        d, m, a = partes
        return f"{int(a):04d}-{int(m):02d}-{int(d):02d}"
    return texto.strip()


def _inteiro(texto: str | None) -> int | None:
    try:
        return int(str(texto).strip())
    except (TypeError, ValueError):
        return None


def _vazio_para_none(texto: str | None) -> str | None:
    if texto is None:
        return None
    t = texto.strip()
    return None if t in ("", "-") else t


def carregar_campos(con: sqlite3.Connection, dados: bytes) -> int:
    registros = ler_tabela_simples(dados)
    if not registros or "campo" not in registros[0]:
        raise FormatoInvalido("Cadastro de campos sem a coluna CAMPO")
    linhas = []
    for r in registros:
        nome = r.get("campo", "")
        if not nome:
            continue
        linhas.append(
            (
                _inteiro(r.get("codigo_campo")),
                nome,
                chave_nome(nome),
                _vazio_para_none(r.get("sigla_campo")),
                _vazio_para_none(r.get("bacia")),
                _vazio_para_none(r.get("estado")),
                _vazio_para_none(r.get("operador")),
                (_vazio_para_none(r.get("ambiente")) or "").upper() or None,
                _vazio_para_none(r.get("rodada")),
                _vazio_para_none(r.get("nome_contrato")),
                _vazio_para_none(r.get("situacao_campo")),
                _data_iso(r.get("data_inicio_producao")),
                _data_iso(r.get("data_termino_producao")),
            )
        )
    with transacao(con):
        con.execute("DELETE FROM cadastro_campos")
        con.executemany(
            "INSERT INTO cadastro_campos (codigo, nome, chave, sigla, bacia, estado, operador, "
            "ambiente, rodada, contrato, situacao, inicio_producao, termino_producao) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            linhas,
        )
    return len(linhas)


def carregar_concessionarios(con: sqlite3.Connection, dados: bytes) -> int:
    registros = ler_tabela_simples(dados)
    if not registros or "campo" not in registros[0]:
        raise FormatoInvalido("Cadastro de concessionários sem a coluna CAMPO")
    chave_empresa = next(
        (k for k in registros[0] if k.startswith("razao_social") or k == "concessionario"),
        None,
    )
    chave_part = next((k for k in registros[0] if k.startswith("participacao")), None)
    if not chave_empresa or not chave_part:
        raise FormatoInvalido("Cadastro de concessionários sem empresa ou participação")
    linhas = []
    for r in registros:
        nome = r.get("campo", "")
        empresa = r.get(chave_empresa, "")
        if not nome or not empresa:
            continue
        linhas.append(
            (
                _inteiro(r.get("codigo_campo")),
                nome,
                chave_nome(nome),
                empresa,
                _vazio_para_none(r.get("cnpj")),
                numero_brasileiro(r.get(chave_part)),
                1 if (r.get("operador") or "").strip().upper() in ("S", "SIM", "1") else 0,
                _vazio_para_none(r.get("rodada")),
                _vazio_para_none(r.get("situacao")),
                _vazio_para_none(r.get("bacia")),
                (_vazio_para_none(r.get("ambiente")) or "").upper() or None,
                _vazio_para_none(r.get("estado")),
            )
        )
    with transacao(con):
        con.execute("DELETE FROM concessionarios")
        con.executemany(
            "INSERT INTO concessionarios (codigo, campo, chave, empresa, cnpj, participacao, "
            "operador, rodada, situacao, bacia, ambiente, estado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            linhas,
        )
    return len(linhas)


@dataclass
class InfoCampo:
    id: int
    nome: str
    bacia: str | None
    estado: str | None
    ambientes: list[str]
    primeiro_mes: int | None
    ultimo_mes: int | None
    operadores: list[str] = field(default_factory=list)
    situacoes: list[str] = field(default_factory=list)
    empresas: dict[str, float] = field(default_factory=dict)  # empresa -> % normalizada
    codigo: int | None = None
    no_cadastro: bool = False

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "bacia": self.bacia,
            "estado": self.estado,
            "ambientes": self.ambientes,
            "primeiro_mes": self.primeiro_mes,
            "ultimo_mes": self.ultimo_mes,
            "operadores": self.operadores,
            "situacoes": self.situacoes,
            "empresas": [
                {"empresa": e, "participacao": round(p, 4)}
                for e, p in sorted(self.empresas.items(), key=lambda x: -x[1])
            ],
            "codigo_anp": self.codigo,
            "no_cadastro": self.no_cadastro,
        }


def participacoes_normalizadas(con: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Participação de cada empresa por campo (chave do nome), em %.

    Quando um campo tem mais de um contrato, as participações somam mais de
    100% no arquivo da ANP. Aqui elas são normalizadas para somar 100%, o que
    equivale a dar o mesmo peso a cada contrato.
    """
    brutas: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for chave, empresa, part in con.execute(
        "SELECT chave, empresa, participacao FROM concessionarios WHERE participacao IS NOT NULL"
    ):
        brutas[chave][empresa] += float(part)
    resultado: dict[str, dict[str, float]] = {}
    for chave, por_empresa in brutas.items():
        total = sum(por_empresa.values())
        if total <= 0:
            continue
        resultado[chave] = {e: 100.0 * v / total for e, v in por_empresa.items()}
    return resultado


def info_campos(con: sqlite3.Connection) -> dict[int, InfoCampo]:
    """Todos os campos com produção na base, enriquecidos com o cadastro da ANP."""
    cad_oper: dict[str, set[str]] = defaultdict(set)
    cad_sit: dict[str, set[str]] = defaultdict(set)
    cad_bacia: dict[str, str] = {}
    cad_estado: dict[str, str] = {}
    cad_codigo: dict[str, int] = {}
    for r in con.execute(
        "SELECT chave, operador, situacao, bacia, estado, codigo FROM cadastro_campos"
    ):
        if r["operador"]:
            cad_oper[r["chave"]].add(r["operador"])
        if r["situacao"]:
            cad_sit[r["chave"]].add(r["situacao"])
        if r["bacia"]:
            cad_bacia.setdefault(r["chave"], r["bacia"])
        if r["estado"]:
            cad_estado.setdefault(r["chave"], r["estado"])
        if r["codigo"] is not None:
            cad_codigo.setdefault(r["chave"], r["codigo"])
    # Operador também aparece no arquivo de concessionários (OPERADOR = S).
    for r in con.execute("SELECT chave, empresa FROM concessionarios WHERE operador = 1"):
        if not cad_oper.get(r["chave"]):
            cad_oper[r["chave"]].add(r["empresa"])
    parts = participacoes_normalizadas(con)

    campos: dict[int, InfoCampo] = {}
    for r in con.execute(
        "SELECT id, nome, chave, bacia, estado, ambientes, primeiro_mes, ultimo_mes FROM campos"
    ):
        ch = r["chave"]
        campos[r["id"]] = InfoCampo(
            id=r["id"],
            nome=r["nome"],
            bacia=r["bacia"] or cad_bacia.get(ch),
            estado=r["estado"] or cad_estado.get(ch),
            ambientes=[a for a in (r["ambientes"] or "").split(",") if a],
            primeiro_mes=r["primeiro_mes"],
            ultimo_mes=r["ultimo_mes"],
            operadores=sorted(cad_oper.get(ch, ())),
            situacoes=sorted(cad_sit.get(ch, ())),
            empresas=parts.get(ch, {}),
            codigo=cad_codigo.get(ch),
            no_cadastro=ch in cad_bacia or ch in cad_oper or ch in parts,
        )
    return campos


def empresas(con: sqlite3.Connection) -> list[dict]:
    """Empresas com participação em algum campo, com contagem de campos."""
    linhas = con.execute(
        "SELECT empresa, COUNT(DISTINCT chave) AS campos, MAX(operador) AS opera, "
        "GROUP_CONCAT(DISTINCT cnpj) AS cnpjs FROM concessionarios GROUP BY empresa ORDER BY empresa"
    ).fetchall()
    return [
        {"empresa": r["empresa"], "campos": r["campos"], "opera_algum": bool(r["opera"]), "cnpj": r["cnpjs"]}
        for r in linhas
    ]
