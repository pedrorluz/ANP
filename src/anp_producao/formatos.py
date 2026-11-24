"""Meses e números no formato usado no app (pt-BR)."""

from __future__ import annotations

import calendar
import math
import re

MESES_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def rotulo_mes(mes: int | None) -> str:
    """202609 -> 'set/2026'."""
    if not mes:
        return ""
    ano, m = divmod(int(mes), 100)
    return f"{MESES_ABREV[m - 1]}/{ano}"


def mes_iso(mes: int | None) -> str | None:
    """202609 -> '2026-09'."""
    if not mes:
        return None
    ano, m = divmod(int(mes), 100)
    return f"{ano:04d}-{m:02d}"


def ler_mes(texto: str | int | None) -> int | None:
    """Aceita '2026-09', '2026/09', '09/2026', 'set/2026' ou 202609. Vazio vira None."""
    if texto is None or texto == "":
        return None
    if isinstance(texto, int):
        valor = texto
    else:
        t = str(texto).strip().lower()
        if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})", t):
            valor = int(m.group(1)) * 100 + int(m.group(2))
        elif m := re.fullmatch(r"(\d{1,2})[-/.](\d{4})", t):
            valor = int(m.group(2)) * 100 + int(m.group(1))
        elif m := re.fullmatch(r"([a-zç]{3})[a-zç]*[-/. ](\d{4})", t):
            abrev = m.group(1).replace("ç", "c")
            if abrev not in MESES_ABREV:
                raise ValueError(f"Mês inválido: {texto}")
            valor = int(m.group(2)) * 100 + MESES_ABREV.index(abrev) + 1
        elif re.fullmatch(r"\d{6}", t):
            valor = int(t)
        else:
            raise ValueError(f"Mês inválido: {texto}. Use AAAA-MM, por exemplo 2026-09")
    if not (1 <= valor % 100 <= 12) or not (1900 <= valor // 100 <= 2100):
        raise ValueError(f"Mês inválido: {texto}")
    return valor


def dias_no_mes(mes: int) -> int:
    ano, m = divmod(int(mes), 100)
    return calendar.monthrange(ano, m)[1]


def somar_meses(mes: int, n: int) -> int:
    ano, m = divmod(int(mes), 100)
    total = ano * 12 + (m - 1) + n
    return (total // 12) * 100 + (total % 12) + 1


def numero_br(valor: float | None, casas: int = 0) -> str:
    """1234567.8 -> '1.234.567,8'."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "n/d"
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def percentual_br(valor: float | None, casas: int = 1, sinal: bool = True) -> str:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "n/d"
    prefixo = "+" if sinal and valor > 0 else ""
    return f"{prefixo}{numero_br(valor * 100, casas)}%"


def slug(texto: str) -> str:
    from anp_producao.leitor_csv import sem_acentos

    t = sem_acentos(texto).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:60] or "dados"
