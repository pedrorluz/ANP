import asyncio
import json
import os
import sys

import pytest

from anp_producao.cli import main


@pytest.fixture
def anp(config, base_carregada, capsys):
    def rodar(*args):
        codigo = main(["--dados", str(config.dir_dados), *args])
        saida = capsys.readouterr()
        return codigo, saida.out, saida.err
    return rodar


def test_status_e_campos_em_json(anp):
    codigo, out, _ = anp("status", "--json")
    assert codigo == 0 and json.loads(out)["ultimo_mes"] == "2026-09"
    codigo, out, _ = anp("campos", "--busca", "gam", "--json")
    assert json.loads(out)["campos"][0]["nome"] == "GAMA"


def test_serie_em_texto_csv_e_json(anp):
    codigo, out, _ = anp("serie", "campo:ALFA", "--unidade", "m3/d", "--inicio", "2026-08")
    assert codigo == 0 and "Petróleo" in out and "set/2026" in out and "Último mês" in out
    codigo, out, _ = anp("serie", "campo:ALFA", "--unidade", "m3/d", "--inicio", "2026-09", "--csv")
    assert out.strip().splitlines() == ["mes;ALFA", "2026-09;100,00"]
    codigo, out, _ = anp("serie", "todos", "--quebra", "campo", "--inicio", "2026-09", "--json")
    assert {s["nome"] for s in json.loads(out)["series"]} == {"ALFA", "BETA", "GAMA"}


def test_clusters_pela_linha_de_comando(anp, tmp_path):
    codigo, out, _ = anp("clusters", "criar", "Polo Bahia", "--estado", "Bahia", "--ativos")
    assert codigo == 0 and "GAMA" in out
    codigo, out, _ = anp("clusters", "editar", "Polo Bahia", "--adicionar", "BETA", "--json")
    assert json.loads(out)["campos_resolvidos"] == ["BETA", "GAMA"]
    arquivo = tmp_path / "c.json"
    anp("clusters", "exportar", "--saida", str(arquivo))
    anp("clusters", "apagar", "Polo Bahia")
    codigo, out, _ = anp("clusters", "importar", str(arquivo))
    assert codigo == 0 and "1 criados" in out
    codigo, out, _ = anp("clusters", "listar", "--json")
    assert [c["nome"] for c in json.loads(out)] == ["Polo Bahia"]


def test_erros_viram_codigos_de_saida(anp):
    codigo, _, err = anp("serie", "campo:INEXISTENTE")
    assert codigo == 2 and "Não encontrado" in err
    codigo, _, err = anp("serie", "campo:ALFA", "--variavel", "xyz")
    assert codigo == 1 and "Variável desconhecida" in err


def test_exportar_detalhe_e_link(anp, config):
    codigo, out, _ = anp("exportar", "detalhe", "campo:ALFA", "--formato", "csv", "--json")
    assert codigo == 0 and json.loads(out)["arquivo"].endswith(".csv")
    codigo, out, _ = anp("link", "cluster:Polo X", "--variavel", "gas")
    assert out.strip() == "http://127.0.0.1:8765/#alvos=cluster:Polo%20X&var=gas"


def test_ferramentas_mcp_em_processo(config, base_carregada):
    from anp_producao.mcp_servidor import criar_servidor

    srv = criar_servidor(config)

    async def rodar():
        nomes = {t.name for t in await srv.list_tools()}
        assert {"status_base", "atualizar_base", "serie_producao", "criar_cluster", "detalhar",
                "exportar_planilha", "buscar_campos", "indicadores", "link_painel"} <= nomes
        r = await srv.call_tool("serie_producao", {"alvos": ["campo:ALFA"], "unidade": "m3/d"})
        dados = json.loads(r.content[0].text)
        assert len(dados["meses"]) == 24 and dados["meses"][-1] == "2026-09"
        assert dados["series"][0]["valores"][-1] == pytest.approx(100.0)
        assert "ALFA" in dados["indicadores"] and dados["link_painel"].startswith("http://")
        r = await srv.call_tool("criar_cluster", {"nome": "Mar", "ambientes": ["MAR"]})
        assert json.loads(r.content[0].text)["campos"] == ["ALFA", "BETA"]
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError, match="Talvez: ALFA"):
            await srv.call_tool("serie_producao", {"alvos": ["campo:ALFAX"]})

    asyncio.run(rodar())


def test_servidor_mcp_por_stdio(config, base_carregada):
    """Sobe 'python -m anp_producao mcp' como um agente faria e conversa pelo protocolo."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parametros = StdioServerParameters(
        command=sys.executable, args=["-m", "anp_producao", "--dados", str(config.dir_dados), "mcp"],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    async def rodar():
        async with stdio_client(parametros) as (leitura, escrita):
            async with ClientSession(leitura, escrita) as sessao:
                await sessao.initialize()
                ferramentas = await sessao.list_tools()
                assert any(t.name == "status_base" for t in ferramentas.tools)
                r = await sessao.call_tool("status_base", {})
                assert json.loads(r.content[0].text)["ultimo_mes"] == "2026-09"
                r = await sessao.call_tool("indicadores", {"alvo": "campo:GAMA", "unidade": "m3/d"})
                kpis = {k["id"]: k for k in json.loads(r.content[0].text)["indicadores"]}
                assert kpis["ultimo"]["valor"] == pytest.approx(10.0)
                erro = await sessao.call_tool("detalhar", {"alvo": "campo:NAO EXISTE"})
                assert erro.is_error and "Campo não encontrado" in erro.content[0].text

    asyncio.run(asyncio.wait_for(rodar(), 60))


def test_saida_redirecionada_no_windows_nao_quebra_com_caractere_fora_do_cp1252(config, monkeypatch):
    import io
    import sys

    bruto = io.BytesIO()
    saida = io.TextIOWrapper(bruto, encoding="cp1252")  # como fica o stdout redirecionado no Windows
    monkeypatch.setattr(sys, "stdout", saida)
    assert main(["--dados", str(config.dir_dados), "variaveis"]) == 0
    saida.flush()
    assert b"CO?" in bruto.getvalue()  # CO₂ vira CO? em vez de derrubar o comando
