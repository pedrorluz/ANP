"""Meses que faltam no arquivo por poço, completados com o arquivo mensal por zona.

No site simulado, como no real em nov/2026, os arquivos de terra por poço de 2026
não trazem abril e julho e março vem só com 3 dos 10 poços de GAMA. Os arquivos
por zona trazem os 10 poços em todos os meses, em média diária.
"""

import dataclasses

import pytest

from anp_producao import qualidade
from anp_producao.atualizacao import atualizar, reprocessar_brutos
from anp_producao.db import conectar_anp
from anp_producao.fonte_anp import ClienteANP
from anp_producao.formatos import dias_no_mes
from anp_producao.servicos import Servicos
from conftest import ZONA, csv_anp, linha, zona_do_mes


def carregar(config, site):
    with ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0) as cliente:
        return atualizar(config, origem="teste", cliente=cliente)


def agregado(config, campo, mes):
    con = conectar_anp(config)
    try:
        return con.execute(
            "SELECT p.* FROM producao_campo_mes p JOIN campos c ON c.id = p.campo_id "
            "WHERE c.nome = ? AND p.mes = ?", (campo, mes)
        ).fetchone()
    finally:
        con.close()


def consultar(config, funcao):
    con = conectar_anp(config)
    try:
        return funcao(con)
    finally:
        con.close()


def linhas_gama(meses_e_pocos, oleo_dia=1.0) -> list[str]:
    linhas = []
    for mes, n in meses_e_pocos:
        dias = dias_no_mes(mes)
        for i in range(1, n + 1):
            linhas.append(linha(mes, "GAMA", f"7-GAM-{i}-BA", estado="Bahia", bacia="Recôncavo", ambiente="TERRA",
                                instalacao="ESTAÇÃO GAMA", oleo=oleo_dia * dias, gas_assoc=0.1 * dias, agua=2.0 * dias))
    return linhas


def test_meses_que_faltam_em_terra_vem_do_arquivo_por_zona(config, site_zona):
    r = carregar(config, site_zona)
    assert r["status"] == "ok", r
    abril = agregado(config, "GAMA", 202604)
    assert abril["oleo"] == pytest.approx(10 * 1.0 * 30)
    assert abril["gas_assoc"] == pytest.approx(10 * 0.1 * 30)
    assert abril["agua"] == pytest.approx(10 * 2.0 * 30)
    assert abril["n_linhas"] == 10  # as duas zonas do poço 1 viram uma linha
    assert agregado(config, "GAMA", 202607)["oleo"] == pytest.approx(10 * 31)
    marco = agregado(config, "GAMA", 202603)
    assert marco["oleo"] == pytest.approx(10 * 31) and marco["n_produtores"] == 10
    # mar já vinha completo e não recebe nada
    alfa = agregado(config, "ALFA", 202601)
    assert alfa["oleo"] == pytest.approx(3100.0) and alfa["n_linhas"] == 3

    assert consultar(config, qualidade.lacunas)["TERRA"] == {}
    brutas = consultar(config, lambda con: qualidade.lacunas(con, efetivas=False))
    assert brutas["TERRA"] == {202603: "parcial", 202604: "ausente", 202607: "ausente"}
    comp = consultar(config, qualidade.complementos)
    assert comp["TERRA"] == {
        202603: {"pocos": 7, "motivo": "parcial"},
        202604: {"pocos": 10, "motivo": "ausente"},
        202607: {"pocos": 10, "motivo": "ausente"},
    }
    assert comp["MAR"] == {}
    instalacoes = consultar(config, lambda con: [x[0] for x in con.execute(
        "SELECT DISTINCT i.nome FROM producao p JOIN instalacoes i ON i.id = p.instalacao_id "
        "WHERE p.mes IN (202603, 202604, 202607) AND p.campo_id = (SELECT id FROM campos WHERE nome = 'GAMA')")])
    assert instalacoes == ["ESTAÇÃO GAMA"]  # herdada do poço no mês vizinho
    assert "produção por zona" in r["mensagem"]


def test_segunda_atualizacao_nao_duplica_nem_baixa_de_novo(config, site_zona):
    carregar(config, site_zona)
    abril = ZONA + "producao_zona_04-2026.csv"
    assert site_zona.downloads[abril] == 1
    r = carregar(config, site_zona)
    assert r["status"] == "ok"
    assert agregado(config, "GAMA", 202604)["oleo"] == pytest.approx(300.0)
    assert agregado(config, "GAMA", 202603)["oleo"] == pytest.approx(310.0)
    assert site_zona.downloads[abril] == 1  # conferido só pelo tamanho


def test_quando_a_anp_publica_o_mes_no_arquivo_por_poco_o_complemento_sai(config, site_zona):
    carregar(config, site_zona)
    conteudo = csv_anp(linhas_gama([(202604, 10)], oleo_dia=2.0) + linhas_gama([(202605, 10), (202606, 10)]))
    site_zona.publicar("pt/2026/producao_por_poco_terra_2026_2_trim.csv", conteudo, "2º trimestre")
    carregar(config, site_zona)
    abril = agregado(config, "GAMA", 202604)
    assert abril["oleo"] == pytest.approx(10 * 2.0 * 30) and abril["n_linhas"] == 10
    comp = consultar(config, qualidade.complementos)
    assert 202604 not in comp["TERRA"] and 202607 in comp["TERRA"]
    assert consultar(config, lambda con: con.execute("SELECT COUNT(*) FROM revisoes").fetchone()[0]) == 0


def test_mes_incompleto_que_a_contagem_de_linhas_nao_pega_e_conferido_pela_zona(config, site_zona):
    # maio com 8 dos 10 poços: 80% das linhas, acima do limiar de mês parcial
    site_zona.publicar("pt/2026/producao_por_poco_terra_2026_2_trim.csv",
                       csv_anp(linhas_gama([(202605, 8), (202606, 10)])), "2º trimestre")
    cfg = dataclasses.replace(config, janela_revisao_anos=100)  # 2026 dentro da janela em qualquer ano
    carregar(cfg, site_zona)
    assert 202605 not in consultar(cfg, lambda con: qualidade.lacunas(con, efetivas=False))["TERRA"]
    assert consultar(cfg, qualidade.complementos)["TERRA"][202605] == {"pocos": 2, "motivo": "incompleto"}
    assert agregado(cfg, "GAMA", 202605)["oleo"] == pytest.approx(10 * 31)
    assert agregado(cfg, "GAMA", 202606)["n_linhas"] == 10  # mês completo continua só com o arquivo por poço


def test_arquivo_por_zona_com_outro_mes_dentro_e_ignorado(config, site_zona):
    site_zona.publicar_zona("producao_zona_07-2026.csv", zona_do_mes(202606), "Julho")
    r = carregar(config, site_zona)
    assert agregado(config, "GAMA", 202607) is None
    assert any("jul/2026" in a for a in r["avisos"])
    assert consultar(config, qualidade.lacunas)["TERRA"] == {202607: "ausente"}


def test_falha_ao_baixar_a_zona_mantem_a_lacuna(config, site_zona):
    site_zona.falhas[ZONA + "producao_zona_04-2026.csv"] = 3
    r = carregar(config, site_zona)
    assert r["status"] == "ok_com_avisos"
    assert any("producao_zona_04-2026.csv" in e for e in r["erros"])
    assert agregado(config, "GAMA", 202604) is None
    assert agregado(config, "GAMA", 202607)["oleo"] == pytest.approx(310.0)
    assert consultar(config, qualidade.lacunas)["TERRA"] == {202604: "ausente"}


def test_reprocessar_do_cache_mantem_o_complemento(config, site_zona):
    carregar(config, site_zona)
    reprocessar_brutos(config)
    assert agregado(config, "GAMA", 202604)["oleo"] == pytest.approx(300.0)
    assert agregado(config, "GAMA", 202603)["oleo"] == pytest.approx(310.0)
    assert set(consultar(config, qualidade.complementos)["TERRA"]) == {202603, 202604, 202607}


def test_revisao_do_arquivo_por_poco_nao_conta_o_complemento(config, site_zona):
    carregar(config, site_zona)
    # a ANP republica o 1º trimestre com janeiro revisado; março continua com 3 poços
    site_zona.publicar("pt/2026/producao_por_poco_terra_2026_1_trim.csv",
                       csv_anp(linhas_gama([(202601, 10)], oleo_dia=1.5) + linhas_gama([(202602, 10), (202603, 3)])),
                       "1º trimestre")
    carregar(config, site_zona)
    revisoes = consultar(config, lambda con: {(x[0], x[1]) for x in con.execute(
        "SELECT c.nome, r.mes FROM revisoes r JOIN campos c ON c.id = r.campo_id")})
    assert revisoes == {("GAMA", 202601)}
    assert agregado(config, "GAMA", 202603)["oleo"] == pytest.approx(310.0)


def test_serie_e_status_mostram_os_meses_completados(config, site_zona):
    carregar(config, site_zona)
    svc = Servicos(config)
    r = svc.serie(["campo:GAMA"], "petroleo", "m3/d", inicio="2026-01", fim="2026-09")
    assert r["series"][0]["valores"] == pytest.approx([10.0] * 9)
    assert r["lacunas"] == []
    assert [c["mes"] for c in r["complementados"]] == ["2026-03", "2026-04", "2026-07"]
    assert any("produção por zona" in a for a in r["avisos"])
    alfa = svc.serie(["campo:ALFA"], "petroleo", "m3/d", inicio="2026-01", fim="2026-09")
    assert alfa["complementados"] == []
    s = svc.status()
    assert [l for l in s["lacunas_recentes"] if l["ambiente"] == "TERRA"] == []  # mar 2019-2024 não existe no site simulado
    assert {"ambiente": "TERRA", "mes": "2026-04", "rotulo": "abr/2026", "pocos": 10,
            "motivo": "ausente"} in s["complementos_recentes"]


def test_base_antiga_ganha_a_coluna_tipo(config):
    import sqlite3

    config.banco_anp.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.banco_anp)
    con.executescript(
        "CREATE TABLE arquivos_fonte (id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, nome TEXT NOT NULL, "
        "ambiente TEXT NOT NULL, ano_ref INTEGER, sub_periodo INTEGER, tamanho INTEGER, sha256 TEXT, "
        "verificado_em TEXT, processado_em TEXT, linhas INTEGER, linhas_descartadas INTEGER, mes_min INTEGER, "
        "mes_max INTEGER, codificacao TEXT, status TEXT, erro TEXT);"
        "INSERT INTO arquivos_fonte (url, nome, ambiente, status) VALUES ('u', 'n', 'MAR', 'ok');"
        "PRAGMA user_version=1;"
    )
    con.close()
    con = conectar_anp(config)
    try:
        assert con.execute("SELECT tipo FROM arquivos_fonte").fetchone()[0] == "poco"
        assert con.execute("PRAGMA user_version").fetchone()[0] >= 2
    finally:
        con.close()


def test_planilha_e_linha_de_comando_mostram_os_meses_completados(config, site_zona, capsys):
    import io

    from openpyxl import load_workbook

    from anp_producao.cli import main

    carregar(config, site_zona)
    svc = Servicos(config)
    conteudo, _, _ = svc.exportar("serie", "xlsx", alvos=["campo:GAMA"], variavel="petroleo",
                                   unidade="m3/d", inicio="2026-01", fim="2026-09")
    wb = load_workbook(io.BytesIO(conteudo))
    linhas = [[c.value for c in r] for r in wb["Lacunas da fonte"].iter_rows(min_row=2)]
    assert linhas[0][:2] == ["mar/2026", "TERRA"] and "produção por zona" in linhas[0][2]
    assert len(linhas) == 3
    assert wb["Série"]["B5"].value == pytest.approx(10.0)  # mar/2026 deixa de ficar vazio

    assert main(["--dados", str(config.dir_dados), "status"]) == 0
    saida = capsys.readouterr().out
    assert "Completados com o arquivo de produção por zona" in saida and "abr/2026 (terra, 10 poços)" in saida
