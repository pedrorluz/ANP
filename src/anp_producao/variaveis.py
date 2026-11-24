"""Variáveis do seletor e conversões de unidade.

Base interna (como a ANP publica): volume do mês em m³ para líquidos, mil m³
para gases e toneladas para vapor. Médias diárias dividem pelo número de dias
do mês.

Fatores usados pela ANP nos boletins mensais:

* 1 m³ de petróleo = 6,28981 bbl
* 1.000 m³ de gás = 6,28981 boe (equivalência de 1 m³ de óleo)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

BBL_POR_M3 = 6.28981
BOE_POR_MIL_M3_GAS = 6.28981
MMCF_POR_MIL_M3 = 0.0353147
SCF_BBL_POR_M3_M3 = 35.3147 / BBL_POR_M3  # 5,6146


@dataclass(frozen=True)
class Unidade:
    id: str
    rotulo: str
    fator: float  # multiplica o volume base do mês (ou a razão)
    por_dia: bool  # divide pelo número de dias do mês
    casas: int
    acumulado_rotulo: str | None = None
    acumulado_fator: float | None = None  # multiplica a soma dos volumes base

    def como_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Variavel:
    id: str
    nome: str
    grupo: str
    dimensao: str
    colunas: tuple[str, ...]
    tipo: str  # volume ou razao
    descricao: str

    def como_dict(self) -> dict:
        d = asdict(self)
        d["unidades"] = [u.como_dict() for u in UNIDADES[self.dimensao]]
        d["unidade_padrao"] = UNIDADES[self.dimensao][0].id
        return d


UNIDADES: dict[str, list[Unidade]] = {
    "liquido": [
        Unidade("bbl/d", "bbl/d", BBL_POR_M3, True, 0, "MMbbl", BBL_POR_M3 / 1e6),
        Unidade("mil bbl/d", "mil bbl/d", BBL_POR_M3 / 1e3, True, 1, "MMbbl", BBL_POR_M3 / 1e6),
        Unidade("m3/d", "m³/d", 1.0, True, 0, "mil m³", 1e-3),
        Unidade("m3/mes", "m³ no mês", 1.0, False, 0, "mil m³", 1e-3),
        Unidade("bbl/mes", "bbl no mês", BBL_POR_M3, False, 0, "MMbbl", BBL_POR_M3 / 1e6),
    ],
    "gas": [
        Unidade("mil m3/d", "mil m³/d", 1.0, True, 1, "MM m³", 1e-3),
        Unidade("MM m3/d", "MM m³/d", 1e-3, True, 2, "bi m³", 1e-6),
        Unidade("MMcf/d", "MMcf/d", MMCF_POR_MIL_M3, True, 2, "Bcf", MMCF_POR_MIL_M3 / 1e3),
        Unidade("boe/d", "boe/d", BOE_POR_MIL_M3_GAS, True, 0, "MMboe", BOE_POR_MIL_M3_GAS / 1e6),
        Unidade("mil m3/mes", "mil m³ no mês", 1.0, False, 0, "MM m³", 1e-3),
    ],
    "energia": [
        Unidade("boe/d", "boe/d", BBL_POR_M3, True, 0, "MMboe", BBL_POR_M3 / 1e6),
        Unidade("mil boe/d", "mil boe/d", BBL_POR_M3 / 1e3, True, 1, "MMboe", BBL_POR_M3 / 1e6),
        Unidade("m3oe/d", "m³oe/d", 1.0, True, 0, "mil m³oe", 1e-3),
    ],
    "massa": [
        Unidade("t/d", "t/d", 1.0, True, 0, "mil t", 1e-3),
        Unidade("t/mes", "t no mês", 1.0, False, 0, "mil t", 1e-3),
    ],
    "rgo": [
        Unidade("m3/m3", "m³/m³", 1.0, False, 0),
        Unidade("scf/bbl", "scf/bbl", SCF_BBL_POR_M3_M3, False, 0),
    ],
    "percentual": [Unidade("%", "%", 100.0, False, 1)],
}

VARIAVEIS: dict[str, Variavel] = {
    v.id: v
    for v in (
        Variavel("petroleo", "Petróleo (óleo + condensado)", "Produção", "liquido",
                 ("oleo", "condensado"), "volume",
                 "Definição da ANP: óleo mais condensado, sem LGN."),
        Variavel("oleo", "Óleo", "Produção", "liquido", ("oleo",), "volume", "Só óleo, sem condensado."),
        Variavel("condensado", "Condensado", "Produção", "liquido", ("condensado",), "volume",
                 "Condensado separado na superfície."),
        Variavel("gas", "Gás natural", "Produção", "gas", ("gas_assoc", "gas_nao_assoc"), "volume",
                 "Gás associado mais não associado. Inclui queima, reinjeção e consumo próprio."),
        Variavel("gas_assoc", "Gás associado", "Produção", "gas", ("gas_assoc",), "volume",
                 "Gás produzido junto com o óleo."),
        Variavel("gas_nao_assoc", "Gás não associado", "Produção", "gas", ("gas_nao_assoc",), "volume",
                 "Gás de reservatórios de gás."),
        Variavel("agua", "Água produzida", "Produção", "liquido", ("agua",), "volume",
                 "Água que chega à superfície com o óleo e o gás."),
        Variavel("boe", "Produção total (boe)", "Produção", "energia",
                 ("oleo", "condensado", "gas_assoc", "gas_nao_assoc"), "volume",
                 "Petróleo mais gás convertido pela ANP: 1.000 m³ de gás = 1 m³ de óleo equivalente."),
        Variavel("rgo", "RGO (gás / petróleo)", "Indicadores", "rgo",
                 ("oleo", "condensado", "gas_assoc", "gas_nao_assoc"), "razao",
                 "Razão gás total / petróleo, em condições de superfície."),
        Variavel("corte_agua", "Corte de água (BSW)", "Indicadores", "percentual",
                 ("oleo", "condensado", "agua"), "razao",
                 "Água / (água + petróleo) em volume."),
        Variavel("inj_agua", "Injeção de água (recuperação)", "Injeção", "liquido", ("inj_agua_rec",),
                 "volume", "Água injetada para recuperação secundária."),
        Variavel("inj_agua_desc", "Injeção de água (descarte)", "Injeção", "liquido", ("inj_agua_desc",),
                 "volume", "Água reinjetada para descarte."),
        Variavel("inj_gas", "Injeção de gás", "Injeção", "gas", ("inj_gas",), "volume",
                 "Gás natural injetado nos reservatórios."),
        Variavel("inj_co2", "Injeção de CO₂", "Injeção", "gas", ("inj_co2",), "volume",
                 "Gás carbônico reinjetado."),
        Variavel("inj_vapor", "Injeção de vapor", "Injeção", "massa", ("inj_vapor",), "volume",
                 "Vapor de água injetado (recuperação térmica)."),
    )
}

VARIAVEL_PADRAO = "petroleo"


def obter_variavel(variavel_id: str | None) -> Variavel:
    vid = (variavel_id or VARIAVEL_PADRAO).strip().lower()
    apelidos = {"oleo+condensado": "petroleo", "agua_produzida": "agua", "bsw": "corte_agua",
                "gas_natural": "gas", "gas_total": "gas", "total": "boe"}
    vid = apelidos.get(vid, vid)
    if vid not in VARIAVEIS:
        raise ValueError(
            f"Variável desconhecida: {variavel_id}. Opções: {', '.join(VARIAVEIS)}"
        )
    return VARIAVEIS[vid]


def obter_unidade(variavel: Variavel, unidade_id: str | None) -> Unidade:
    opcoes = UNIDADES[variavel.dimensao]
    if not unidade_id:
        return opcoes[0]
    normal = unidade_id.strip().replace("³", "3").replace("ê", "e")
    for u in opcoes:
        if normal.lower() in (u.id.lower(), u.rotulo.replace("³", "3").lower()):
            return u
    raise ValueError(
        f"Unidade '{unidade_id}' não serve para {variavel.nome}. "
        f"Opções: {', '.join(u.id for u in opcoes)}"
    )


def _soma(base: pd.DataFrame, colunas: tuple[str, ...]) -> pd.Series:
    total = pd.Series(0.0, index=base.index)
    for c in colunas:
        if c in base:
            total = total + base[c].fillna(0.0)
    return total


def componentes(variavel: Variavel, base: pd.DataFrame) -> tuple[pd.Series, pd.Series | None]:
    """Volume base do mês (volume) ou (numerador, denominador) para razões."""
    if variavel.id == "boe":
        # m³ de óleo equivalente: petróleo em m³ + gás em mil m³.
        return _soma(base, ("oleo", "condensado", "gas_assoc", "gas_nao_assoc")), None
    if variavel.id == "rgo":
        return _soma(base, ("gas_assoc", "gas_nao_assoc")) * 1000.0, _soma(base, ("oleo", "condensado"))
    if variavel.id == "corte_agua":
        agua = _soma(base, ("agua",))
        return agua, agua + _soma(base, ("oleo", "condensado"))
    return _soma(base, variavel.colunas), None


def converter(
    variavel: Variavel,
    unidade: Unidade,
    numerador: pd.Series,
    denominador: pd.Series | None,
    dias: pd.Series,
) -> pd.Series:
    """Aplica a unidade. Para razões, denominador zero vira vazio."""
    if variavel.tipo == "razao":
        den = denominador.where(denominador > 0)
        return (numerador / den) * unidade.fator
    valor = numerador * unidade.fator
    if unidade.por_dia:
        valor = valor / dias
    return valor


def catalogo() -> list[dict]:
    return [v.como_dict() for v in VARIAVEIS.values()]


def para_lista(serie: pd.Series, casas: int) -> list[float | None]:
    """Série -> lista JSON, com vazios como None e arredondamento estável."""
    arr = serie.to_numpy(dtype="float64")
    saida: list[float | None] = []
    for v in arr:
        saida.append(None if np.isnan(v) else round(float(v), max(casas, 0) + 4))
    return saida
