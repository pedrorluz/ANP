"""Leitura dos CSVs de produção por poço publicados pela ANP.

Os arquivos não seguem um padrão único ao longo dos anos. Este leitor trata o
que foi encontrado nos arquivos reais (nov/2026):

* codificação UTF-8 com BOM na maioria, mas Windows-1252 em alguns anos;
* cabeçalho com ou sem colchetes, por exemplo ``[Campo]`` e ``Campo``;
* ordem de colunas diferente em alguns trimestres (2022 T4 e 2025 T4);
* linhas inteiras embrulhadas entre aspas, com as aspas internas dobradas;
* mês em ``MM/AAAA`` ou abreviado em português, por exemplo ``out/2025``;
* números no padrão brasileiro (``3.127,441``) e colunas vazias quando o
  poço não tem aquele fluido.

As colunas são reconhecidas pelo nome, nunca pela posição.

O mesmo leitor serve para o arquivo mensal "Produção por zona" (ver
``ler_producao_zona``), que traz média diária por poço e zona em vez de volume
do mês.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from anp_producao.db import COLUNAS_VOLUME
from anp_producao.formatos import dias_no_mes

MESES_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

COLUNAS_TEXTO = ("campo", "poco", "instalacao", "estado", "bacia", "ambiente")

# (nome interno, padrão aplicado ao cabeçalho normalizado). A ordem importa:
# "injeção de gás carbônico" precisa ser testado antes de "injeção de gás".
PADROES_CABECALHO: tuple[tuple[str, str], ...] = (
    ("ano", r"^ano$"),
    ("mes", r"^(mes|periodo|data)\b"),
    ("estado", r"^(estado|uf)$"),
    ("bacia", r"^bacia"),
    ("campo", r"^campo$"),
    ("poco", r"^(nome )?poco"),
    ("ambiente", r"^(ambiente|localizacao|terra/mar)"),
    ("instalacao", r"^instalacao"),
    ("oleo", r"^producao de oleo"),
    ("condensado", r"^producao de condensado"),
    ("gas_assoc", r"^producao de gas associado"),
    ("gas_nao_assoc", r"^producao de gas nao associado"),
    ("agua", r"^producao de agua"),
    ("inj_co2", r"^injecao de (gas carbonico|co2)"),
    ("inj_gas", r"^injecao de gas"),
    ("inj_agua_rec", r"^injecao de agua.*recuperacao"),
    ("inj_agua_desc", r"^injecao de agua.*descarte"),
    ("inj_n2", r"^injecao de nitrogenio"),
    ("inj_vapor", r"^injecao de vapor"),
    ("inj_polimeros", r"^injecao de polimero"),
    ("inj_outros", r"^injecao de outros"),
)

# Arquivo "Produção por zona": médias diárias (m³/d, mil m³/d e t/d) por poço e zona.
# "Petróleo" e "Gás total" são somas de outras colunas e ficam de fora. A água
# produzida vem depois das águas injetadas porque todas começam com "água". Até
# jun/2025 o arquivo traz colunas de geologia (Pré-sal, Período, Época, Idade,
# Grupo, Formação, Membro); por isso o mês é só a coluna "Data", já que
# "Período" ali é o período geológico.
PADROES_ZONA: tuple[tuple[str, str], ...] = (
    ("mes", r"^data\b"),
    ("campo", r"^campo$"),
    ("poco", r"^(nome )?poco"),
    ("bacia", r"^bacia"),
    ("ambiente", r"^ambiente"),
    ("oleo", r"^oleo\b"),
    ("condensado", r"^condensado\b"),
    ("gas_assoc", r"^gas associado\b"),
    ("gas_nao_assoc", r"^gas nao associado\b"),
    ("inj_agua_desc", r"^agua injetada para descarte"),
    ("inj_agua_rec", r"^agua injetada para rec"),
    ("agua", r"^agua\b"),
    ("inj_vapor", r"^vapor injetado"),
    ("inj_co2", r"^(co2|gas carbonico) injetado"),
    ("inj_n2", r"^(n2|nitrogenio) injetado"),
    ("inj_gas", r"^gas injetado"),
    ("inj_outros", r"^outros fluidos injetados"),
)

# Colunas cujo padrão da ANP é mil m³ (Mm³). As demais vêm em m³ (vapor em t).
COLUNAS_GAS = {"gas_assoc", "gas_nao_assoc", "inj_gas", "inj_co2", "inj_n2"}


class FormatoInvalido(ValueError):
    """O arquivo não tem a estrutura mínima esperada."""


@dataclass
class ResultadoLeitura:
    dados: pd.DataFrame
    codificacao: str
    linhas_lidas: int
    linhas_descartadas: int
    mes_min: int | None
    mes_max: int | None
    avisos: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Normalização de textos
# --------------------------------------------------------------------------- #

def sem_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def limpar_nome(texto: str | None) -> str:
    """Tira espaços sobrando sem mexer em maiúsculas e acentos."""
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip()


def chave_nome(texto: str | None) -> str:
    """Chave para casar nomes vindos de arquivos diferentes (sem acento, maiúsculas)."""
    return re.sub(r"\s+", " ", sem_acentos(limpar_nome(texto)).upper()).strip()


def normalizar_cabecalho(texto: str) -> str:
    t = texto.replace("﻿", "").replace("[", "").replace("]", "")
    t = t.replace("³", "3").replace("²", "2")
    t = sem_acentos(t).lower()
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- #
# Codificação e estrutura
# --------------------------------------------------------------------------- #

def decodificar(dados: bytes) -> tuple[str, str]:
    """Devolve (texto, codificação). Tenta UTF-8 e cai para Windows-1252."""
    if dados.startswith(b"\xef\xbb\xbf"):
        return dados[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return dados.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return dados.decode("cp1252", errors="replace"), "cp1252"


def detectar_separador(primeira_linha: str) -> str:
    fora_de_aspas = re.sub(r'"[^"]*"', "", primeira_linha)
    return ";" if fora_de_aspas.count(";") > fora_de_aspas.count(",") else ","


def mapear_cabecalho(
    cabecalho: list[str], padroes: tuple[tuple[str, str], ...] = PADROES_CABECALHO
) -> tuple[dict[str, int], dict[str, float], list[str], set[str]]:
    """Liga cada coluna interna ao índice no arquivo.

    Devolve também os fatores de unidade e as colunas em média diária (``/d``)."""
    indices: dict[str, int] = {}
    fatores: dict[str, float] = {}
    avisos: list[str] = []
    diarias: set[str] = set()
    for i, bruto in enumerate(cabecalho):
        nome = normalizar_cabecalho(bruto)
        for interno, padrao in padroes:
            if interno in indices:
                continue
            if re.search(padrao, nome):
                indices[interno] = i
                fatores[interno] = _fator_unidade(interno, nome, avisos, diarias)
                break
    obrigatorias = {"mes", "campo", "poco"}
    faltando = obrigatorias - indices.keys()
    if faltando:
        raise FormatoInvalido(
            "Cabeçalho sem as colunas obrigatórias "
            f"{sorted(faltando)}. Cabeçalho lido: {cabecalho}"
        )
    if not any(c in indices for c in ("oleo", "condensado", "gas_assoc", "gas_nao_assoc", "agua")):
        raise FormatoInvalido(f"Nenhuma coluna de produção reconhecida em {cabecalho}")
    return indices, fatores, avisos, diarias


def _fator_unidade(interno: str, cabecalho: str, avisos: list[str], diarias: set[str] | None = None) -> float:
    """Converte para a unidade interna: m³ (líquidos), mil m³ (gases) e t (vapor).

    Unidade por dia (``m³/d``) marca a coluna em ``diarias``; a conversão para o
    volume do mês, que depende do número de dias, é feita depois de ler o mês."""
    if interno not in COLUNAS_VOLUME:
        return 1.0
    unidade = re.search(r"\(([^)]*)\)", cabecalho)
    u = unidade.group(1).replace(" ", "") if unidade else ""
    if u.endswith("/d"):
        u = u[:-2]
        if diarias is not None:
            diarias.add(interno)
    if interno in COLUNAS_GAS:
        if u in ("mm3", "milm3", ""):
            return 1.0
        if u == "m3":
            avisos.append(f"{interno}: cabeçalho em m³, convertido para mil m³")
            return 0.001
        if u in ("mmm3", "milhoesm3"):
            return 1000.0
    elif interno == "inj_vapor":
        return 1.0
    else:
        if u in ("m3", ""):
            return 1.0
        if u in ("mm3", "milm3"):
            avisos.append(f"{interno}: cabeçalho em mil m³, convertido para m³")
            return 1000.0
    avisos.append(f"{interno}: unidade '{u}' não reconhecida, valor mantido")
    return 1.0


# --------------------------------------------------------------------------- #
# Meses e números
# --------------------------------------------------------------------------- #

_RE_MM_AAAA = re.compile(r"^(\d{1,2})[/\-.](\d{4})$")
_RE_AAAA_MM = re.compile(r"^(\d{4})[/\-.](\d{1,2})$")
_RE_NOME_MES = re.compile(r"^([a-zç]{3})[a-zç.]*[/\-. ]?(\d{2}|\d{4})$")
_RE_AAAAMM = re.compile(r"^(\d{4})(\d{2})$")
_RE_DATA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


def interpretar_mes(valor: str) -> int | None:
    """Converte um texto de mês da ANP em inteiro AAAAMM. Devolve None se inválido."""
    v = sem_acentos(str(valor)).strip().lower()
    if not v:
        return None
    ano = mes = None
    if m := _RE_MM_AAAA.match(v):
        mes, ano = int(m.group(1)), int(m.group(2))
    elif m := _RE_AAAA_MM.match(v):
        ano, mes = int(m.group(1)), int(m.group(2))
    elif m := _RE_NOME_MES.match(v):
        mes = MESES_PT.get(m.group(1).replace("ç", "c"))
        ano = int(m.group(2))
        if ano < 100:
            ano += 1900 if ano >= 40 else 2000
    elif m := _RE_AAAAMM.match(v):
        ano, mes = int(m.group(1)), int(m.group(2))
    elif m := _RE_DATA.match(v):
        mes, ano = int(m.group(2)), int(m.group(3))
    if not mes or not ano or not (1 <= mes <= 12) or not (1900 <= ano <= 2100):
        return None
    return ano * 100 + mes


def _formato_brasileiro(colunas: list[pd.Series]) -> bool:
    """True se os números usam vírgula decimal (padrão da ANP)."""
    tem_ponto = False
    for s in colunas:
        if s.str.contains(",", regex=False).any():
            return True
        tem_ponto = tem_ponto or bool(s.str.contains(".", regex=False).any())
    if not tem_ponto:
        return True
    # Só pontos: é milhar brasileiro se todos tiverem grupos de três dígitos.
    for s in colunas:
        com_ponto = s[s.str.contains(".", regex=False)]
        if not com_ponto.str.fullmatch(r"-?\d{1,3}(\.\d{3})+").all():
            return False
    return True


def para_numero(serie: pd.Series, brasileiro: bool) -> np.ndarray:
    s = serie.str.strip()
    if brasileiro:
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    s = s.where(~s.isin(["", "-", "--"]), None)
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")


# --------------------------------------------------------------------------- #
# Leitura principal
# --------------------------------------------------------------------------- #

def ler_producao(
    dados: bytes,
    ambiente_padrao: str | None = None,
    *,
    padroes: tuple[tuple[str, str], ...] = PADROES_CABECALHO,
) -> ResultadoLeitura:
    """Lê um CSV de produção por poço e devolve um DataFrame padronizado.

    Colunas do resultado: ``mes`` (AAAAMM), ``campo``, ``poco``, ``instalacao``,
    ``estado``, ``bacia``, ``ambiente`` e as colunas de volume de
    ``COLUNAS_VOLUME`` (m³, mil m³ para gases e t para vapor). Colunas em média
    diária são multiplicadas pelos dias do mês.
    """
    texto, codificacao = decodificar(dados)
    if not texto.strip():
        raise FormatoInvalido("Arquivo vazio")
    primeira = texto.split("\n", 1)[0]
    sep = detectar_separador(primeira)

    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=sep, quotechar='"')
    try:
        cabecalho = next(leitor)
    except StopIteration as exc:  # pragma: no cover - texto vazio já tratado
        raise FormatoInvalido("Arquivo sem cabeçalho") from exc
    indices, fatores, avisos, diarias = mapear_cabecalho(cabecalho, padroes)
    n_col = len(cabecalho)
    minimo = max(indices["mes"], indices["campo"], indices["poco"]) + 1

    linhas: list[list[str]] = []
    descartadas = 0
    for linha in leitor:
        if not linha or (len(linha) == 1 and not linha[0].strip()):
            continue
        if len(linha) == 1 and sep in linha[0]:
            # Linha inteira entre aspas: o conteúdo é a linha verdadeira.
            linha = next(csv.reader([linha[0]], delimiter=sep, quotechar='"'))
        if len(linha) < minimo:
            descartadas += 1
            continue
        if len(linha) < n_col:
            linha = linha + [""] * (n_col - len(linha))
        elif len(linha) > n_col:
            if any(x.strip() for x in linha[n_col:]):
                descartadas += 1
                continue
            linha = linha[:n_col]
        linhas.append(linha)

    if not linhas:
        raise FormatoInvalido("Arquivo sem linhas de dados")

    bruto = pd.DataFrame(linhas, columns=list(range(n_col)), dtype=object)
    df = pd.DataFrame(index=bruto.index)

    meses_texto = bruto[indices["mes"]].astype(str)
    mapa_meses = {v: interpretar_mes(v) for v in meses_texto.unique()}
    df["mes"] = meses_texto.map(mapa_meses)

    for col in COLUNAS_TEXTO:
        if col in indices:
            df[col] = bruto[indices[col]].astype(str).map(limpar_nome)
        else:
            df[col] = ""

    df.loc[df["campo"] == "", "campo"] = "SEM CAMPO"
    df.loc[df["poco"] == "", "poco"] = "SEM POÇO"
    df.loc[df["instalacao"] == "", "instalacao"] = "Não Informado"
    amb = df["ambiente"].map(lambda a: sem_acentos(a).upper())
    amb = amb.where(~amb.isin(["M", "MARITIMO"]), "MAR")
    amb = amb.where(~amb.isin(["T", "TERRESTRE"]), "TERRA")
    if ambiente_padrao:
        amb = amb.where(amb.isin(["MAR", "TERRA"]), ambiente_padrao)
    df["ambiente"] = amb

    colunas_num = [bruto[indices[c]].astype(str) for c in COLUNAS_VOLUME if c in indices]
    brasileiro = _formato_brasileiro(colunas_num) if colunas_num else True
    dias = None
    if diarias:
        dias = df["mes"].map(lambda m: dias_no_mes(m) if m else np.nan).to_numpy(dtype="float64")
    for c in COLUNAS_VOLUME:
        if c in indices:
            valores = para_numero(bruto[indices[c]].astype(str), brasileiro)
            df[c] = valores * fatores.get(c, 1.0) * (dias if c in diarias else 1.0)
        else:
            df[c] = np.nan

    invalidos = df["mes"].isna()
    if invalidos.any():
        exemplos = meses_texto[invalidos].unique()[:3].tolist()
        avisos.append(f"{int(invalidos.sum())} linhas com mês inválido descartadas (ex.: {exemplos})")
        descartadas += int(invalidos.sum())
        df = df[~invalidos]
    df["mes"] = df["mes"].astype("int64")
    if not brasileiro:
        avisos.append("Números com ponto decimal (padrão internacional)")
    df = df.reset_index(drop=True)

    return ResultadoLeitura(
        dados=df,
        codificacao=codificacao,
        linhas_lidas=len(df),
        linhas_descartadas=descartadas,
        mes_min=int(df["mes"].min()) if len(df) else None,
        mes_max=int(df["mes"].max()) if len(df) else None,
        avisos=avisos,
    )


def ler_producao_zona(dados: bytes) -> ResultadoLeitura:
    """Lê o arquivo mensal "Produção por zona" e devolve o volume do mês por poço.

    O arquivo traz uma linha por poço e zona produtora, em média diária (m³/d,
    mil m³/d e t/d). Aqui as zonas de cada poço são somadas e a média vira volume
    do mês (média x dias do mês), no mesmo formato de ``ler_producao``. O arquivo
    não tem instalação nem estado. Em nov/2026 os totais conferem com os arquivos
    por poço em até 0,1% nos meses completos.
    """
    leitura = ler_producao(dados, padroes=PADROES_ZONA)
    df = leitura.dados
    chaves = ["mes", "campo", "poco", "ambiente", "bacia", "estado", "instalacao"]
    por_poco = (
        df.groupby(chaves, sort=False, dropna=False)[list(COLUNAS_VOLUME)]
        .sum(min_count=1)
        .reset_index()
    )
    leitura.dados = por_poco
    return leitura


def ler_tabela_simples(dados: bytes) -> list[dict[str, str]]:
    """Lê CSVs pequenos de cadastro (campos e concessionários) como dicionários.

    As chaves são os nomes do cabeçalho normalizados (sem acento, minúsculas).
    """
    texto, _ = decodificar(dados)
    primeira = texto.split("\n", 1)[0]
    sep = detectar_separador(primeira)
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=sep, quotechar='"')
    cabecalho = [normalizar_cabecalho(c) for c in next(leitor)]
    registros = []
    for linha in leitor:
        if not linha or not any(x.strip() for x in linha):
            continue
        registros.append({cabecalho[i]: limpar_nome(v) for i, v in enumerate(linha[: len(cabecalho)])})
    return registros


def numero_brasileiro(texto: str | None) -> float | None:
    """Converte um único número no padrão brasileiro. Vazio ou traço vira None."""
    if texto is None:
        return None
    t = str(texto).strip()
    if t in ("", "-", "--"):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None
