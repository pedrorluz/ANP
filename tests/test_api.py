import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from anp_producao.api import criar_app

H = {"X-ANP": "1"}


@pytest.fixture
def cliente(config, base_carregada):
    return TestClient(criar_app(config), base_url="http://127.0.0.1")


def test_status_e_variaveis(cliente):
    s = cliente.get("/api/status").json()
    assert s["ultimo_mes"] == "2026-09"
    v = cliente.get("/api/variaveis").json()
    ids = [x["id"] for x in v]
    assert ids[:3] == ["petroleo", "oleo", "condensado"] and "corte_agua" in ids
    gas = next(x for x in v if x["id"] == "gas")
    assert gas["unidade_padrao"] == "mil m3/d"


def test_serie_com_varios_alvos(cliente):
    r = cliente.get("/api/serie", params={"alvo": ["campo:ALFA", "campo:BETA"], "unidade": "m3/mes",
                                          "inicio": "2026-09"})
    assert r.status_code == 200
    assert [s["valores"][0] for s in r.json()["series"]] == [pytest.approx(3000.0), pytest.approx(41.0)]


def test_erros_viram_codigos_http(cliente):
    r = cliente.get("/api/serie", params={"alvo": "campo:NAO EXISTE"})
    assert r.status_code == 404 and "não encontrado" in r.json()["erro"]
    r = cliente.get("/api/serie", params={"alvo": "campo:ALFA", "variavel": "xyz"})
    assert r.status_code == 400 and "Variável desconhecida" in r.json()["erro"]
    assert cliente.get("/api/clusters/inexistente").status_code == 404


def test_clusters_crud_com_protecao(cliente):
    novo = {"nome": "Polo Mar", "regra": {"ambientes": ["MAR"]}, "cor": "#C47A3D"}
    assert cliente.post("/api/clusters", json=novo).status_code == 403  # sem o cabeçalho
    r = cliente.post("/api/clusters", json=novo, headers=H)
    assert r.status_code == 201 and r.json()["campos_resolvidos"] == ["ALFA", "BETA"]
    r = cliente.put("/api/clusters/Polo Mar", json={"remover": ["BETA"]}, headers=H)
    assert r.json()["campos_resolvidos"] == ["ALFA"]
    lista = cliente.get("/api/clusters").json()
    assert [c["nome"] for c in lista] == ["Polo Mar"] and lista[0]["n_campos"] == 1
    previa = cliente.post("/api/clusters/previa", json={"regra": {"bacias": ["Recôncavo"]}}, headers=H).json()
    assert previa["campos"] == ["GAMA"]
    exportado = cliente.get("/api/clusters/exportar")
    assert "attachment" in exportado.headers["content-disposition"]
    assert cliente.delete("/api/clusters/Polo Mar", headers=H).status_code == 200
    r = cliente.post("/api/clusters/importar", json=exportado.json(), headers=H)
    assert r.json()["criados"] == 1
    assert cliente.post("/api/clusters", json={"nome": "x"}, headers=H).status_code == 400


def test_exportar_excel_e_csv(cliente):
    r = cliente.get("/api/exportar", params={"tipo": "serie", "formato": "xlsx", "alvo": "todos", "inicio": "2026-01"})
    assert r.status_code == 200
    assert "filename*=UTF-8''anp_brasil" in r.headers["content-disposition"]
    assert load_workbook(io.BytesIO(r.content)).sheetnames[0] == "Série"
    r = cliente.get("/api/exportar", params={"tipo": "detalhe", "formato": "csv", "alvo": "campo:ALFA",
                                             "quebra": "poco"})
    assert r.status_code == 200 and "7-ALF-1-RJS" in r.content.decode("utf-8")


def test_detalhe_campos_filtros_e_busca(cliente):
    d = cliente.get("/api/detalhe", params={"alvo": "campo:ALFA"}).json()
    assert d["mes"] == "2026-09" and d["total_linhas"] == 3
    assert cliente.get("/api/campos", params={"busca": "bet"}).json()["campos"][0]["nome"] == "BETA"
    assert "Santos" in cliente.get("/api/filtros").json()["bacias"]
    assert cliente.get("/api/instalacoes", params={"busca": "FPSO"}).json()[0]["nome"] == "FPSO ALFA"
    assert cliente.get("/api/pocos", params={"busca": "ALF-1"}).json() == ["7-ALF-1-RJS"]
    assert len(cliente.get("/api/empresas", params={"busca": "petro"}).json()) == 2


def test_atualizar_em_segundo_plano(cliente, monkeypatch):
    from anp_producao import atualizacao

    chamadas = []
    monkeypatch.setattr(atualizacao.subprocess, "Popen", lambda *a, **k: chamadas.append(a))
    r = cliente.post("/api/atualizar", json={"completa": False}, headers=H)
    assert r.status_code == 200 and r.json() == {"iniciada": True} and chamadas


def test_revisoes_vazias_e_marcar_vistas(cliente):
    assert cliente.get("/api/revisoes").json() == {"total": 0, "itens": []}
    assert cliente.post("/api/revisoes/vistas", headers=H).json() == {"marcadas": 0}


def test_host_estranho_e_recusado(config, base_carregada):
    c = TestClient(criar_app(config), base_url="http://site-malicioso.com")
    assert c.get("/api/status").status_code == 400


def test_documentacao_openapi(cliente):
    esquema = cliente.get("/openapi.json").json()
    assert "/api/serie" in esquema["paths"] and "/api/clusters" in esquema["paths"]
