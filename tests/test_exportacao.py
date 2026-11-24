import io

import pytest
from openpyxl import load_workbook

from anp_producao.servicos import Servicos


@pytest.fixture
def svc(config, base_carregada):
    return Servicos(config)


def test_csv_no_padrao_brasileiro(svc):
    conteudo, nome, mime = svc.exportar("serie", "csv", alvos=["campo:ALFA"], variavel="petroleo",
                                         unidade="bbl/d", inicio="2026-08")
    texto = conteudo.decode("utf-8")
    assert texto.startswith("﻿")
    linhas = texto.strip().split("\r\n")
    assert linhas[1] == "Mês;ALFA"
    assert linhas[2] == "2026-08;628,981"
    assert nome == "anp_alfa_petroleo_2026-09.csv" and mime.startswith("text/csv")


def test_csv_internacional_e_vazios(svc):
    conteudo, _, _ = svc.exportar("serie", "csv", padrao_csv="intl", alvos=["campo:GAMA"],
                                   variavel="petroleo", unidade="m3/d", inicio="2026-03", fim="2026-05")
    linhas = conteudo.decode("utf-8").strip().split("\r\n")
    assert linhas[1:] == ["Mês,GAMA", "2026-03,", "2026-04,", "2026-05,10"]


def test_excel_da_serie(svc):
    conteudo, nome, _ = svc.exportar("serie", "xlsx", alvos=["campo:GAMA"], variavel="petroleo",
                                      unidade="bbl/d", inicio="2026-01", quebra=None)
    wb = load_workbook(io.BytesIO(conteudo))
    assert wb.sheetnames == ["Série", "Indicadores", "Lacunas da fonte", "Parâmetros"]
    ws = wb["Série"]
    assert ws["A2"].value == "Mês" and ws["B2"].value == "GAMA"
    assert ws["A3"].value.year == 2026 and ws["A3"].number_format == "mmm/yyyy"
    assert ws["B3"].value == pytest.approx(62.8981)
    assert ws["B5"].value is None  # mar/2026 parcial na fonte
    params = {r[0].value: r[1].value for r in wb["Parâmetros"].iter_rows(min_row=2)}
    assert params["Unidade"] == "bbl/d" and "ANP" in params["Fonte"]
    assert nome.endswith(".xlsx")


def test_excel_do_detalhe(svc):
    conteudo, nome, _ = svc.exportar("detalhe", "xlsx", alvo="campo:ALFA", quebra="poco", mes="2026-09")
    wb = load_workbook(io.BytesIO(conteudo))
    ws = wb["Detalhe"]
    cab = [c.value for c in ws[2]]
    assert cab[:2] == ["Nome", "Situação"] and "Petróleo (bbl/d)" in cab
    assert ws.max_row == 2 + 3
    assert nome == "anp_alfa_pocos_2026-09.xlsx"


def test_exportar_para_arquivo(svc, config):
    caminho = svc.exportar_para_arquivo("serie", "xlsx", alvos=["todos"], inicio="2026-01")
    assert caminho.parent == config.dir_exportacoes.resolve() and caminho.exists()


def test_exportacao_nao_sobrescreve_arquivo_alheio(svc, tmp_path):
    existente = tmp_path / "meu.xlsx"
    existente.write_text("importante")
    with pytest.raises(ValueError, match="Já existe"):
        svc.exportar_para_arquivo("serie", "xlsx", str(existente), alvos=["todos"], inicio="2026-01")
    assert existente.read_text() == "importante"
