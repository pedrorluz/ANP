"""Exportação para CSV e Excel do que está na tela (série ou detalhe)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from anp_producao.formatos import slug

NAVY = "0F1C2E"
COBRE = "C47A3D"
CINZA = "B8C0CC"
FONTE_ANP = (
    "ANP, dados abertos de produção de petróleo e gás natural por poço "
    "(gov.br/anp, Fase de Desenvolvimento e Produção)"
)


def _numero_csv(valor, padrao: str) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float):
        texto = f"{valor:.6f}".rstrip("0").rstrip(".")
    else:
        texto = str(valor)
    return texto.replace(".", ",") if padrao == "br" else texto


def _csv(linhas: list[list], padrao: str) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";" if padrao == "br" else ",", lineterminator="\r\n")
    for linha in linhas:
        escritor.writerow([_numero_csv(v, padrao) if isinstance(v, (int, float)) and not isinstance(v, bool) else ("" if v is None else v) for v in linha])
    # BOM ajuda o Excel a abrir com acentos corretos
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _tabela_serie(resultado: dict) -> tuple[list[str], list[list]]:
    series = list(resultado["series"])
    if resultado.get("total"):
        series = series + [{**resultado["total"], "nome": f"Total {resultado['total']['nome']}"}]
    cabecalho = ["Mês"] + [s["nome"] for s in series]
    linhas = []
    for i, mes in enumerate(resultado["meses"]):
        linhas.append([mes] + [s["valores"][i] for s in series])
    return cabecalho, linhas


def nome_arquivo(resultado: dict, tipo: str, extensao: str) -> str:
    if tipo == "serie":
        alvos = "-".join(slug(a["nome"]) for a in resultado["alvos"])[:60]
        return f"anp_{alvos}_{resultado['variavel']['id']}_{resultado['periodo']['fim']}.{extensao}"
    return f"anp_{slug(resultado['alvo']['nome'])}_{resultado['quebra']}s_{resultado['mes']}.{extensao}"


def serie_csv(resultado: dict, padrao: str = "br") -> bytes:
    cabecalho, linhas = _tabela_serie(resultado)
    unidade = resultado["unidade"]["rotulo"]
    titulo = [f"{resultado['variavel']['nome']} ({unidade})"]
    return _csv([titulo, cabecalho, *linhas], padrao)


_COLUNAS_DETALHE = [
    ("nome", "Nome"), ("situacao", "Situação"), ("campos", "Campo(s)"), ("instalacao", "Instalação"),
    ("petroleo_bbl_d", "Petróleo (bbl/d)"), ("gas_mil_m3_d", "Gás (mil m³/d)"), ("agua_bbl_d", "Água (bbl/d)"),
    ("boe_d", "Total (boe/d)"), ("participacao_boe", "Participação no total"),
    ("corte_agua_pct", "Corte de água (%)"), ("rgo_m3_m3", "RGO (m³/m³)"),
    ("var_mensal_petroleo", "Var. mensal petróleo"), ("var_anual_petroleo", "Var. anual petróleo"),
    ("inj_agua_m3_d", "Injeção de água (m³/d)"), ("inj_gas_mil_m3_d", "Injeção de gás (mil m³/d)"),
]


def _colunas_presentes(resultado: dict) -> list[tuple[str, str]]:
    chaves = set().union(*[l.keys() for l in resultado["linhas"]]) if resultado["linhas"] else set()
    return [(k, r) for k, r in _COLUNAS_DETALHE if k in chaves]


def detalhe_csv(resultado: dict, padrao: str = "br") -> bytes:
    colunas = _colunas_presentes(resultado)
    linhas = [[f"{resultado['alvo']['nome']} por {resultado['quebra']}, {resultado['rotulo_mes']}"],
              [r for _, r in colunas]]
    for l in resultado["linhas"]:
        linhas.append([l.get(k) for k, _ in colunas])
    return _csv(linhas, padrao)


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #

def _estilo_cabecalho(ws, linha: int, n_colunas: int) -> None:
    for c in range(1, n_colunas + 1):
        cel = ws.cell(row=linha, column=c)
        cel.font = Font(bold=True, color="F2F2F0")
        cel.fill = PatternFill("solid", fgColor=NAVY)
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _larguras(ws, larguras: list[int]) -> None:
    for i, w in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _formato_numero(casas: int) -> str:
    return "#,##0" if casas <= 0 else "#,##0." + "0" * casas


def _aba_parametros(wb: Workbook, pares: list[tuple[str, str]]) -> None:
    ws = wb.create_sheet("Parâmetros")
    ws.append(["Parâmetro", "Valor"])
    _estilo_cabecalho(ws, 1, 2)
    for k, v in pares:
        ws.append([k, v])
    _larguras(ws, [28, 100])


def serie_xlsx(resultado: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Série"
    unidade = resultado["unidade"]
    casas = unidade["casas"]
    cabecalho, linhas = _tabela_serie(resultado)
    ws.append([f"{resultado['variavel']['nome']} ({unidade['rotulo']})"])
    ws["A1"].font = Font(bold=True, size=13, color=NAVY)
    ws.append(cabecalho)
    _estilo_cabecalho(ws, 2, len(cabecalho))
    faltando = {l["mes"] for l in resultado.get("lacunas", [])}
    for linha in linhas:
        ano, mes = (int(x) for x in linha[0].split("-"))
        ws.append([date(ano, mes, 1), *linha[1:]])
        r = ws.max_row
        ws.cell(row=r, column=1).number_format = "mmm/yyyy"
        for c in range(2, len(cabecalho) + 1):
            ws.cell(row=r, column=c).number_format = _formato_numero(casas)
        if linha[0] in faltando:
            ws.cell(row=r, column=1).font = Font(italic=True, color=COBRE)
    ws.freeze_panes = "B3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(cabecalho))}{ws.max_row}"
    _larguras(ws, [12] + [max(14, min(36, len(str(c)) + 4)) for c in cabecalho[1:]])

    # Indicadores
    wi = wb.create_sheet("Indicadores")
    wi.append(["Alvo", "Indicador", "Valor", "Unidade", "Referência"])
    _estilo_cabecalho(wi, 1, 5)
    nomes = {a["id"]: a["nome"] for a in resultado["alvos"]}
    for alvo_id, kpis in resultado["indicadores"].items():
        for k in kpis:
            valor = k.get("valor")
            unidade_kpi = k.get("unidade", "")
            if k.get("tipo") == "percentual" and valor is not None:
                unidade_kpi = "%"
                valor = valor * 100
            elif k.get("tipo") == "pontos":
                unidade_kpi = "p.p."
            wi.append([nomes.get(alvo_id, alvo_id), k["rotulo"], valor, unidade_kpi, k.get("detalhe", "")])
            wi.cell(row=wi.max_row, column=3).number_format = "#,##0.0"
    _larguras(wi, [34, 22, 16, 12, 28])

    if resultado.get("lacunas") or resultado.get("complementados"):
        wl = wb.create_sheet("Lacunas da fonte")
        wl.append(["Mês", "Ambiente", "Situação"])
        _estilo_cabecalho(wl, 1, 3)
        for l in resultado.get("lacunas", []):
            wl.append([l["rotulo"], ", ".join(l["ambientes"]), f"sem dado ({l['tipo']})"])
        for c in resultado.get("complementados", []):
            wl.append([c["rotulo"], ", ".join(c["ambientes"]),
                       f"completado com o arquivo de produção por zona ({c['pocos']} poços)"])
        _larguras(wl, [12, 16, 56])

    _aba_parametros(wb, [
        ("Alvos", ", ".join(a["nome"] for a in resultado["alvos"])),
        ("Variável", resultado["variavel"]["nome"]),
        ("Definição", resultado["variavel"]["descricao"]),
        ("Unidade", unidade["rotulo"]),
        ("Quebra", resultado.get("quebra") or "total"),
        ("Período", f"{resultado['periodo']['inicio']} a {resultado['periodo']['fim']}"),
        ("Produção líquida de", ", ".join(resultado.get("liquida_de") or []) or "bruta (100%)"),
        ("Último mês na base", resultado["ultimo_mes_base"]),
        ("Avisos", " | ".join(resultado.get("avisos") or []) or "nenhum"),
        ("Conversões", "1 m³ = 6,28981 bbl; 1.000 m³ de gás = 6,28981 boe (critério da ANP)"),
        ("Fonte", FONTE_ANP),
        ("Gerado em", datetime.now().strftime("%d/%m/%Y %H:%M")),
    ])
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def detalhe_xlsx(resultado: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Detalhe"
    colunas = _colunas_presentes(resultado)
    ws.append([f"{resultado['alvo']['nome']} por {resultado['quebra']}, {resultado['rotulo_mes']}"])
    ws["A1"].font = Font(bold=True, size=13, color=NAVY)
    ws.append([r for _, r in colunas])
    _estilo_cabecalho(ws, 2, len(colunas))
    percentuais = {"participacao_boe", "var_mensal_petroleo", "var_anual_petroleo"}
    for l in resultado["linhas"]:
        ws.append([l.get(k) for k, _ in colunas])
        r = ws.max_row
        for i, (k, _) in enumerate(colunas, start=1):
            if k in percentuais:
                ws.cell(row=r, column=i).number_format = "0.0%"
            elif k in ("corte_agua_pct",):
                ws.cell(row=r, column=i).number_format = "0.0"
            elif k not in ("nome", "situacao", "campos", "instalacao"):
                ws.cell(row=r, column=i).number_format = "#,##0.0"
    ws.freeze_panes = "B3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(colunas))}{ws.max_row}"
    _larguras(ws, [24 if k in ("nome", "campos", "instalacao") else 15 for k, _ in colunas])
    _aba_parametros(wb, [
        ("Alvo", resultado["alvo"]["nome"]),
        ("Quebra", resultado["quebra"]),
        ("Mês", resultado["rotulo_mes"]),
        ("Avisos", " | ".join(resultado.get("avisos") or []) or "nenhum"),
        ("Fonte", FONTE_ANP),
        ("Gerado em", datetime.now().strftime("%d/%m/%Y %H:%M")),
    ])
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
