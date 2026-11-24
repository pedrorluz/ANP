import httpx
import pytest

from anp_producao.config import PREFIXO_ANP
from anp_producao.fonte_anp import ClienteANP, ErroFonte, extrair_pagina, mes_do_arquivo_zona
from conftest import FIXTURES, PAGINA, PRODUCAO


def test_extrair_pagina_real():
    html = (FIXTURES / "pagina_anp.html").read_text(encoding="utf-8")
    p = extrair_pagina(html, PAGINA, (PREFIXO_ANP,))
    nomes = [a.nome for a in p.arquivos]
    assert len(p.arquivos) == 9  # sem metadados, produção por zona e domínio externo
    assert "producao-mar-2099.csv" not in nomes
    assert "producao_zona_09-2026.csv" not in nomes
    assert p.url_campos.endswith("/extracao-campo.csv")
    assert p.url_concessionarios.endswith("/extracao-campo-concessionarios.csv")
    assert p.atualizada_em == "2026-11-03T16:20"
    por_nome = {a.nome: a for a in p.arquivos}
    assert por_nome["producao-mar-2026.csv"].ambiente == "MAR"
    assert por_nome["producao_por_poco_2024.csv"].ambiente == "MAR"
    assert por_nome["producao_por_poco_terra_2026_3_trim.csv"].ambiente == "TERRA"
    assert por_nome["producao_por_poco_terra_2026_3_trim.csv"].sub_periodo == 3
    assert por_nome["producao-por-poco-terra-trim-3.csv"].ano_ref == 2024  # ano vem da pasta
    assert por_nome["producao-por-poco-terra-trim-3.csv"].sub_periodo == 3
    assert por_nome["producaoterra20191trim.csv"].ano_ref == 2019
    assert por_nome["producao-terra-1985.csv"].ano_ref == 1985  # não 1988 da pasta
    assert por_nome["producao-mar-2016-2018.csv"].ano_ref == 2018
    # do mais novo para o mais antigo
    assert p.arquivos[0].ano_ref == 2026
    assert [a.ano_ref for a in p.arquivos] == sorted([a.ano_ref for a in p.arquivos], reverse=True)
    # arquivos mensais de produção por zona, usados para completar meses que faltam
    assert [(z.nome, z.mes) for z in p.zonas] == [("producao_zona_09-2026.csv", 202609),
                                                  ("Produo_Zona_032026.csv", 202603)]
    assert all("/producao-por-zona/" in z.url for z in p.zonas)


@pytest.mark.parametrize("nome,esperado", [
    ("producao_zona_01-2026.csv", 202601), ("Produo_Zona_032026.csv", 202603),
    ("producao_zona_10-2014.csv", 201410), ("producao_zona_2026-04.csv", 202604),
    ("producao-zona-202607.csv", 202607), ("producao_zona.csv", None), ("producao_zona_13-2026.csv", None),
])
def test_mes_do_arquivo_zona(nome, esperado):
    assert mes_do_arquivo_zona(nome) == esperado


def test_cliente_le_tamanho_com_range_porque_head_da_403(config, site):
    with ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0) as c:
        url = PRODUCAO + "pm/producao-mar-2026.csv"
        assert c.tamanho_remoto(url) == len(site.arquivos[url])
    assert not any(m == "HEAD" for m, _ in site.requisicoes)
    assert site.downloads[url] == 0  # não baixou o arquivo inteiro


def test_cliente_baixa_com_hash_e_tenta_de_novo(config, site, tmp_path):
    url = PRODUCAO + "pm/producao-mar-2026.csv"
    site.falhas[url] = 2  # duas respostas 503 antes de funcionar
    with ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0) as c:
        caminho, tamanho, sha = c.baixar(url, tmp_path)
    assert caminho.read_bytes() == site.arquivos[url]
    assert tamanho == len(site.arquivos[url]) and len(sha) == 64


def test_cliente_recusa_endereco_fora_da_anp(config, site, tmp_path):
    with ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0) as c:
        with pytest.raises(ErroFonte):
            c.baixar("https://exemplo.com/x.csv", tmp_path)


def test_cliente_desiste_depois_de_tres_falhas(config, site, tmp_path):
    url = PRODUCAO + "pm/producao-mar-2026.csv"
    site.falhas[url] = 5
    with ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0) as c:
        with pytest.raises(ErroFonte):
            c.baixar(url, tmp_path)
    assert not list(tmp_path.glob("*.part"))  # não deixa lixo


def test_pagina_sem_arquivos_e_erro_claro(config):
    transporte = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>sem links</html>"))
    with ClienteANP(config, transport=transporte, pausa_entre_tentativas=0) as c:
        with pytest.raises(ErroFonte, match="layout"):
            c.pagina()
