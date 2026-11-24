import json

import pytest

from anp_producao.clusters import ClusterNaoEncontrado, ErroCluster
from anp_producao.servicos import Servicos


@pytest.fixture
def svc(config, base_carregada):
    return Servicos(config)


def test_criar_manual_e_por_regra(svc):
    a = svc.cluster_criar(nome="Manual", incluir=["alfa", "Gama", "ALFA"], cor="#c47a3d", descricao="teste")
    assert a["incluir"] == ["ALFA", "GAMA"] and a["campos_resolvidos"] == ["ALFA", "GAMA"]
    assert a["cor"] == "#C47A3D"
    b = svc.cluster_criar(nome="Sócios Shell", regra={"empresas": ["SHELL BRASIL PETRÓLEO LTDA"]})
    assert b["campos_resolvidos"] == ["ALFA"]
    c = svc.cluster_criar(nome="Mar menos Alfa", regra={"ambientes": ["mar"]}, excluir=["ALFA"])
    assert c["campos_resolvidos"] == ["BETA"]
    d = svc.cluster_criar(nome="Ativos", regra={"somente_ativos": True, "bacias": ["Santos", "Recôncavo"]})
    assert d["campos_resolvidos"] == ["ALFA", "GAMA"]


def test_campo_inexistente_fica_listado(svc):
    c = svc.cluster_criar(nome="Com erro", incluir=["ALFA", "CAMPO FANTASMA"])
    assert c["nao_encontrados"] == ["CAMPO FANTASMA"] and c["n_campos"] == 1


def test_validacoes(svc):
    with pytest.raises(ErroCluster, match="pelo menos"):
        svc.cluster_criar(nome="Vazio")
    with pytest.raises(ErroCluster, match="nome"):
        svc.cluster_criar(nome="  ", incluir=["ALFA"])
    with pytest.raises(ErroCluster, match=":"):
        svc.cluster_criar(nome="a:b", incluir=["ALFA"])
    with pytest.raises(ErroCluster, match="Cor"):
        svc.cluster_criar(nome="Cor ruim", incluir=["ALFA"], cor="vermelho")
    with pytest.raises(ErroCluster, match="Critérios desconhecidos"):
        svc.cluster_criar(nome="Regra ruim", regra={"cor_do_ceu": ["azul"]})
    svc.cluster_criar(nome="Único", incluir=["ALFA"])
    with pytest.raises(ErroCluster, match="Já existe"):
        svc.cluster_criar(nome="único", incluir=["BETA"])


def test_editar_adicionar_remover_renomear(svc):
    svc.cluster_criar(nome="Polo", incluir=["ALFA"])
    c = svc.cluster_editar("polo", adicionar=["BETA", "GAMA"])
    assert c["campos_resolvidos"] == ["ALFA", "BETA", "GAMA"]
    c = svc.cluster_editar("Polo", remover=["gama"], novo_nome="Polo Mar", descricao="só mar")
    assert c["nome"] == "Polo Mar" and c["campos_resolvidos"] == ["ALFA", "BETA"] and c["descricao"] == "só mar"
    with pytest.raises(ErroCluster, match="vazio"):
        svc.cluster_editar("Polo Mar", remover=["ALFA", "BETA"])


def test_remover_campo_puxado_por_regra_vira_exclusao(svc):
    svc.cluster_criar(nome="Petrobras", regra={"operadores": ["PETROBRAS"]})
    c = svc.cluster_editar("Petrobras", remover=["BETA"])
    assert c["excluir"] == ["BETA"] and c["campos_resolvidos"] == ["ALFA"]
    c = svc.cluster_editar("Petrobras", adicionar=["BETA"])
    assert c["excluir"] == [] and c["campos_resolvidos"] == ["ALFA", "BETA"]


def test_apagar_e_nao_encontrado(svc):
    svc.cluster_criar(nome="Temporário", incluir=["ALFA"])
    svc.cluster_apagar("temporário")
    with pytest.raises(ClusterNaoEncontrado):
        svc.cluster_obter("Temporário")


def test_backup_automatico_e_importacao(svc, config):
    svc.cluster_criar(nome="A", incluir=["ALFA"])
    svc.cluster_criar(nome="B", regra={"bacias": ["Campos"]})
    salvo = json.loads(config.arquivo_clusters_json.read_text(encoding="utf-8"))
    assert [c["nome"] for c in salvo["clusters"]] == ["A", "B"]
    assert len(list(config.dir_backups.glob("clusters-*.json"))) >= 2
    exportado = svc.clusters_exportar()
    svc.cluster_apagar("A")
    svc.cluster_editar("B", regra={"bacias": ["Santos"]})
    r = svc.clusters_importar(exportado)
    assert r == {"criados": 1, "atualizados": 1, "erros": []}
    assert svc.cluster_obter("B")["campos_resolvidos"] == ["BETA"]
    r = svc.clusters_importar({"clusters": [{"nome": "C", "incluir": ["GAMA"]}]}, substituir=True)
    assert [c["nome"] for c in svc.clusters_listar()] == ["C"]


def test_importacao_invalida(svc):
    with pytest.raises(ErroCluster):
        svc.clusters_importar({"outra": []})
    r = svc.clusters_importar({"clusters": [{"nome": "", "incluir": ["ALFA"]}, {"nome": "Ok", "incluir": ["ALFA"]}]})
    assert r["criados"] == 1 and len(r["erros"]) == 1


def test_previa_do_editor(svc):
    p = svc.cluster_previa(regra={"estados": ["bahia"]}, incluir=["ALFA"])
    assert p["campos"] == ["ALFA", "GAMA"] and p["n_campos"] == 2


def test_clusters_sobrevivem_a_base_apagada(svc, config):
    svc.cluster_criar(nome="Fica", incluir=["ALFA"])
    config.banco_anp.unlink()
    for extra in config.dir_dados.glob("anp.sqlite-*"):
        extra.unlink()
    assert [c["nome"] for c in Servicos(config).clusters_listar()] == ["Fica"]
