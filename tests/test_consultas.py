"""Contas conferidas à mão com os números redondos do site simulado (ver conftest)."""

import pytest

from anp_producao.servicos import Servicos
from anp_producao.variaveis import BBL_POR_M3

PETROBRAS = "PETRÓLEO BRASILEIRO S.A. - PETROBRAS"


@pytest.fixture
def svc(config, base_carregada):
    return Servicos(config)


def kpi(resultado, alvo_id, kpi_id):
    return next(k for k in resultado["indicadores"][alvo_id] if k["id"] == kpi_id)


def test_serie_de_um_campo_em_bbl_d(svc):
    r = svc.serie(["campo:alfa"], "petroleo", "bbl/d", inicio="2026-01")
    assert r["meses"][0] == "2026-01" and r["meses"][-1] == "2026-09"
    assert r["series"][0]["nome"] == "ALFA"
    assert r["series"][0]["valores"] == pytest.approx([100 * BBL_POR_M3] * 9)
    assert r["unidade"]["rotulo"] == "bbl/d"


def test_indicadores_do_campo(svc):
    r = svc.serie(["campo:ALFA"], "petroleo", "bbl/d")
    aid = r["alvos"][0]["id"]
    assert kpi(r, aid, "ultimo")["valor"] == pytest.approx(628.981)
    assert kpi(r, aid, "ultimo")["mes"] == "2026-09"
    assert kpi(r, aid, "var_mensal")["valor"] == pytest.approx(0.0)
    assert kpi(r, aid, "var_anual")["valor"] == pytest.approx(0.0)
    assert kpi(r, aid, "media_12m")["valor"] == pytest.approx(628.981)
    assert kpi(r, aid, "pico")["valor"] == pytest.approx(628.981)
    ano = kpi(r, aid, "acumulado_ano")
    assert ano["valor"] == pytest.approx(27300 * BBL_POR_M3 / 1e6) and ano["unidade"] == "MMbbl"
    assert kpi(r, aid, "acumulado_total")["valor"] == pytest.approx(100300 * BBL_POR_M3 / 1e6)
    assert kpi(r, aid, "produtores")["valor"] == 2


def test_gas_boe_rgo_e_corte_de_agua(svc):
    gas = svc.serie(["campo:ALFA"], "gas", "mil m3/d", inicio="2026-09")
    assert gas["series"][0]["valores"] == pytest.approx([1000 / 30])
    mmcf = svc.serie(["campo:ALFA"], "gas", "MMcf/d", inicio="2026-09")
    assert mmcf["series"][0]["valores"] == pytest.approx([1000 / 30 * 0.0353147])
    boe = svc.serie(["campo:ALFA"], "boe", "boe/d", inicio="2026-09")
    assert boe["series"][0]["valores"] == pytest.approx([(100 + 1000 / 30) * BBL_POR_M3])
    rgo = svc.serie(["campo:ALFA"], "rgo", "m3/m3", inicio="2026-09")
    assert rgo["series"][0]["valores"] == pytest.approx([1_000_000 / 3000])
    scf = svc.serie(["campo:ALFA"], "rgo", "scf/bbl", inicio="2026-09")
    assert scf["series"][0]["valores"] == pytest.approx([1_000_000 / 3000 * 35.3147 / BBL_POR_M3])
    corte = svc.serie(["campo:ALFA"], "corte_agua", inicio="2026-09")
    assert corte["series"][0]["valores"] == pytest.approx([100 * 18 / 3018], rel=1e-4)
    aid = corte["alvos"][0]["id"]
    assert kpi(corte, aid, "var_mensal")["tipo"] == "pontos"
    assert not any(k["id"] == "pico" for k in corte["indicadores"][aid])


def test_volume_mensal_e_condensado(svc):
    r = svc.serie(["campo:BETA"], "petroleo", "m3/mes", inicio="2026-09")
    assert r["series"][0]["valores"] == pytest.approx([41.0])
    c = svc.serie(["campo:BETA"], "condensado", "m3/mes", inicio="2026-09")
    assert c["series"][0]["valores"] == pytest.approx([10.0])


def test_lacunas_da_fonte_ficam_vazias_e_nao_zeradas(svc):
    r = svc.serie(["campo:GAMA"], "petroleo", "m3/d", inicio="2026-01", fim="2026-09")
    valores = r["series"][0]["valores"]
    assert valores == [pytest.approx(10.0), pytest.approx(10.0), None, None, pytest.approx(10.0),
                       pytest.approx(10.0), None, pytest.approx(10.0), pytest.approx(10.0)]
    assert [l["mes"] for l in r["lacunas"]] == ["2026-03", "2026-04", "2026-07"]
    assert any("não publicou" in a for a in r["avisos"])
    aid = r["alvos"][0]["id"]
    assert "lacuna" in kpi(r, aid, "acumulado_ano")["detalhe"]


def test_campo_que_nao_e_afetado_pela_lacuna_de_terra(svc):
    r = svc.serie(["campo:ALFA"], "petroleo", "m3/d", inicio="2026-03", fim="2026-04")
    assert None not in r["series"][0]["valores"]
    assert r["lacunas"] == []


def test_cluster_por_regra_e_quebra_por_campo(svc):
    svc.cluster_criar(nome="Polo Petrobras", regra={"operadores": ["petrobras"]})
    r = svc.serie(["cluster:Polo Petrobras"], "petroleo", "m3/mes", inicio="2026-09", quebra="campo")
    nomes = {s["nome"]: s["valores"][0] for s in r["series"]}
    assert nomes == {"ALFA": pytest.approx(3000.0), "BETA": pytest.approx(41.0)}
    assert r["total"]["valores"] == [pytest.approx(3041.0)]


def test_quebra_por_poco_com_outros(svc):
    r = svc.serie(["campo:ALFA"], "petroleo", "m3/mes", inicio="2026-09", quebra="poco", top_n=1)
    assert len(r["series"]) == 2
    assert r["series"][0]["valores"] == [pytest.approx(1500.0)]
    assert r["series"][1]["nome"] == "Outros (2)" and r["series"][1]["valores"] == [pytest.approx(1500.0)]


def test_quebra_por_instalacao(svc):
    r = svc.serie(["todos"], "petroleo", "m3/mes", inicio="2026-09", quebra="instalacao")
    nomes = {s["nome"]: s["valores"][0] for s in r["series"]}
    assert nomes["FPSO ALFA"] == pytest.approx(3000.0)
    assert nomes["PLATAFORMA BETA"] == pytest.approx(41.0)
    assert nomes["ESTAÇÃO GAMA"] == pytest.approx(300.0)


def test_comparacao_de_varios_alvos(svc):
    r = svc.serie(["campo:ALFA", "bacia:Campos", "ambiente:TERRA"], "petroleo", "m3/mes", inicio="2026-09")
    assert [s["valores"][0] for s in r["series"]] == [pytest.approx(3000.0), pytest.approx(41.0), pytest.approx(300.0)]
    assert len(r["indicadores"]) == 3


def test_alvos_poco_e_instalacao(svc):
    p = svc.serie(["poco:7-alf-1-rjs"], "petroleo", "m3/mes", inicio="2026-09")
    assert p["series"][0]["valores"] == [pytest.approx(1500.0)]
    i = svc.serie(["instalacao:fpso alfa"], "inj_agua", "m3/mes", inicio="2026-09")
    assert i["series"][0]["valores"] == [pytest.approx(3000.0)]


def test_producao_liquida_pela_participacao(svc):
    r = svc.serie(["todos"], "petroleo", "m3/mes", inicio="2026-09", liquida_de=[PETROBRAS])
    assert r["series"][0]["valores"] == [pytest.approx(0.6 * 3000 + 41)]
    assert any("participação atual" in a for a in r["avisos"])
    shell = svc.serie(["empresa:shell brasil petróleo ltda"], "petroleo", "m3/mes", inicio="2026-09")
    assert shell["series"][0]["valores"] == [pytest.approx(3000.0)]  # bruto do campo onde é sócia


def test_alvo_sem_prefixo_procura_cluster_e_depois_campo(svc):
    svc.cluster_criar(nome="Meu Polo", incluir=["BETA"])
    assert svc.serie(["Meu Polo"], inicio="2026-09")["alvos"][0]["tipo"] == "cluster"
    assert svc.serie(["gama"], inicio="2026-09")["alvos"][0]["tipo"] == "campo"


def test_erros_claros(svc):
    from anp_producao.consultas import ErroConsulta, NaoEncontrado

    with pytest.raises(NaoEncontrado, match="Talvez: ALFA"):
        svc.serie(["campo:ALFAX"])
    with pytest.raises(ValueError, match="Unidade"):
        svc.serie(["campo:ALFA"], "gas", "bbl/d")
    with pytest.raises(ErroConsulta, match="um alvo por vez"):
        svc.serie(["campo:ALFA", "campo:BETA"], quebra="poco")
    with pytest.raises(NaoEncontrado):
        svc.serie(["todos"], liquida_de=["EMPRESA QUE NAO EXISTE"])


def test_base_vazia_orienta_a_atualizar(config):
    from anp_producao.consultas import ErroConsulta

    with pytest.raises(ErroConsulta, match="Atualizar base"):
        Servicos(config).serie(["todos"])


def test_detalhe_por_poco(svc):
    d = svc.detalhe("campo:ALFA", "poco", "2026-09")
    assert d["mes"] == "2026-09" and d["total_linhas"] == 3
    p1 = next(l for l in d["linhas"] if l["nome"] == "7-ALF-1-RJS")
    assert p1["petroleo_bbl_d"] == pytest.approx(50 * BBL_POR_M3, rel=1e-4)
    assert p1["instalacao"] == "FPSO ALFA" and p1["situacao"] == "produtor"
    inj = next(l for l in d["linhas"] if l["nome"] == "8-ALF-9-RJS")
    assert inj["situacao"] == "injetor" and inj["inj_agua_m3_d"] == pytest.approx(100.0)
    assert sum(l["participacao_boe"] or 0 for l in d["linhas"]) == pytest.approx(1.0)
    assert d["linhas"][-1]["nome"] == "8-ALF-9-RJS"


def test_detalhe_por_campo_de_um_cluster_e_mes_padrao(svc):
    svc.cluster_criar(nome="Todos os campos", regra={"ambientes": ["MAR", "TERRA"]})
    d = svc.detalhe("cluster:Todos os campos", "campo")
    assert d["mes"] == "2026-09"
    assert [l["nome"] for l in d["linhas"]] == ["ALFA", "GAMA", "BETA"]


def test_busca_de_campos_e_filtros(svc):
    r = svc.campos(busca="alf")
    assert r["campos"][0]["nome"] == "ALFA"
    assert r["campos"][0]["petroleo_bbl_d"] == pytest.approx(628.98, rel=1e-3)
    assert r["campos"][0]["operadores"] == ["PETROBRAS"]
    assert [c["nome"] for c in svc.campos(bacia="Campos")["campos"]] == ["BETA"]
    assert [c["nome"] for c in svc.campos()["campos"]] == ["ALFA", "GAMA", "BETA"]  # por produção
    f = svc.filtros()
    assert f["bacias"] == ["Campos", "Recôncavo", "Santos"]
    assert f["operadores"] == ["PETROBRAS", "TERRA FORTE"]
    assert len(f["empresas"]) == 3


def test_status(svc):
    s = svc.status()
    assert s["ultimo_mes"] == "2026-09" and s["ultimo_mes_rotulo"] == "set/2026"
    assert s["base_vazia"] is False and s["atualizacao_em_andamento"] is False
    assert s["proxima_publicacao_estimada"].startswith("início de dez/2026")
    assert s["ultima_atualizacao"]["status"] == "ok"
    assert {"ambiente": "TERRA", "mes": "2026-04", "tipo": "ausente", "rotulo": "abr/2026"} in s["lacunas_recentes"]


def test_link_do_painel(svc):
    url = svc.link_painel(["cluster:Polo X", "campo:BÚZIOS"], "gas", "mil m3/d", "2020-01")
    assert url.startswith("http://127.0.0.1:8799/#alvos=cluster:Polo%20X,campo:B%C3%9AZIOS")
    assert "var=gas" in url and "ini=2020-01" in url
