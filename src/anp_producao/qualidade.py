"""Cobertura da fonte: meses que faltam ou vieram incompletos nos arquivos da ANP.

A ANP publica terra e mar em arquivos separados. Em nov/2026, por exemplo, os
arquivos trimestrais de terra de 2026 não traziam abril e julho, e março veio
com cerca de 80% dos poços. Um campo sem linhas num mês desses não está parado,
o dado simplesmente não veio. Por isso esses meses aparecem como lacuna (valor
vazio) e não como zero.

A contagem considera só os arquivos por poço. Quando o arquivo mensal por zona
completa ou confere um mês (ver complemento.py), ele deixa de ser lacuna e passa
a constar em ``complementos``.
"""

from __future__ import annotations

import sqlite3
from statistics import median

AMBIENTES = ("MAR", "TERRA")
LIMIAR_PARCIAL = 0.6  # menos de 60% das linhas usuais = mês parcial
MIN_LINHAS_PARCIAL = 100  # com poucos poços (anos 1940 a 1960) a contagem oscila e não indica falha


def mes_seguinte(mes: int, passos: int = 1) -> int:
    ano, m = divmod(mes, 100)
    total = ano * 12 + (m - 1) + passos
    return (total // 12) * 100 + (total % 12) + 1


def intervalo_meses(inicio: int, fim: int) -> list[int]:
    meses = []
    m = inicio
    while m <= fim:
        meses.append(m)
        m = mes_seguinte(m)
    return meses


def lacunas(con: sqlite3.Connection, efetivas: bool = True) -> dict[str, dict[int, str]]:
    """Por ambiente, meses 'ausente' ou 'parcial' entre o primeiro dado e o último mês da base.

    A conta usa só os arquivos por poço. Com ``efetivas`` (padrão), saem os meses
    que o arquivo por zona completou ou conferiu."""
    contagem: dict[str, dict[int, int]] = {a: {} for a in AMBIENTES}
    for amb, mes, linhas in con.execute(
        "SELECT c.ambiente, c.mes, SUM(c.linhas) FROM cobertura c "
        "JOIN arquivos_fonte a ON a.id = c.arquivo_id AND a.tipo = 'poco' "
        "GROUP BY c.ambiente, c.mes"
    ):
        if amb in contagem:
            contagem[amb][mes] = linhas
    todos = [m for c in contagem.values() for m in c]
    if not todos:
        return {a: {} for a in AMBIENTES}
    ultimo_global = max(todos)
    resultado: dict[str, dict[int, str]] = {}
    for amb, por_mes in contagem.items():
        resultado[amb] = {}
        if not por_mes:
            continue
        recentes: list[int] = []
        for mes in intervalo_meses(min(por_mes), ultimo_global):
            n = por_mes.get(mes, 0)
            if n == 0:
                resultado[amb][mes] = "ausente"
                continue
            referencia = median(recentes[-6:]) if len(recentes) >= 3 else 0
            if referencia >= MIN_LINHAS_PARCIAL and n < LIMIAR_PARCIAL * referencia:
                resultado[amb][mes] = "parcial"
            else:
                recentes.append(n)
    if efetivas:
        for amb, mes in meses_conferidos(con):
            resultado.get(amb, {}).pop(mes, None)
    return resultado


def meses_conferidos(con: sqlite3.Connection) -> set[tuple[str, int]]:
    """(ambiente, mês) completados ou conferidos com o arquivo mensal por zona."""
    return {
        (amb, mes)
        for amb, mes in con.execute(
            "SELECT ambiente, mes_min FROM arquivos_fonte "
            "WHERE tipo = 'zona' AND status = 'ok' AND mes_min IS NOT NULL"
        )
    }


def complementos(con: sqlite3.Connection) -> dict[str, dict[int, dict]]:
    """Por ambiente, meses que receberam poços do arquivo por zona.

    ``motivo`` é 'ausente' ou 'parcial' quando a contagem de linhas já apontava a
    falha, e 'incompleto' quando só a comparação com o arquivo por zona mostrou
    que faltavam poços."""
    brutas = lacunas(con, efetivas=False)
    resultado: dict[str, dict[int, dict]] = {a: {} for a in AMBIENTES}
    for amb, mes, linhas in con.execute(
        "SELECT ambiente, mes_min, SUM(linhas) FROM arquivos_fonte "
        "WHERE tipo = 'zona' AND status = 'ok' AND linhas > 0 GROUP BY ambiente, mes_min"
    ):
        if amb in resultado:
            motivo = brutas.get(amb, {}).get(mes, "incompleto")
            resultado[amb][mes] = {"pocos": int(linhas), "motivo": motivo}
    return resultado


def resumo_complementos(comp: dict[str, dict[int, dict]], desde: int | None = None) -> list[dict]:
    itens = []
    for amb, meses in comp.items():
        for mes, info in sorted(meses.items()):
            if desde is None or mes >= desde:
                itens.append({"ambiente": amb, "mes": mes, **info})
    return itens


def resumo_lacunas(lac: dict[str, dict[int, str]], desde: int | None = None) -> list[dict]:
    """Lista legível de lacunas, opcionalmente só a partir de um mês."""
    itens = []
    for amb, meses in lac.items():
        for mes, tipo in sorted(meses.items()):
            if desde is None or mes >= desde:
                itens.append({"ambiente": amb, "mes": mes, "tipo": tipo})
    return itens
