"""Ferramentas de teste: uma ANP simulada (página e arquivos) servida sem internet.

Os arquivos gerados seguem o formato real publicado pela ANP em nov/2026:
cabeçalho entre colchetes, BOM, CRLF e números no padrão brasileiro entre aspas.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import httpx
import pytest

from anp_producao.config import Config

FIXTURES = Path(__file__).parent / "fixtures"
PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/fase-de-desenvolvimento-e-producao"
BASE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/"
    "arquivos-fase-de-desenvolvimento-e-producao/"
)
PRODUCAO = BASE + "arquivos-producao-de-petroleo-e-gas-natural-nacional/"
ZONA = BASE + "producao-por-zona/"

CABECALHO = (
    "[Ano],[Mês/Ano],[Estado],[Bacia],[Campo],[Poço],[Ambiente],[Instalação],"
    "[Produção de Óleo (m³)],[Produção de Condensado (m³)],[Produção de Gás Associado (Mm³)],"
    "[Produção de Gás Não Associado (Mm³)],[Produção de Água (m³)],[Injeção de Gás (Mm³)],"
    "[Injeção de Água para Recuperação Secundária (m³)],[Injeção de Água para Descarte (m³)],"
    "[Injeção de Gás Carbônico (Mm³)],[Injeção de Nitrogênio (Mm³)],[Injeção de Vapor de Água (t)],"
    "[Injeção de Polímeros (m³)],[Injeção de Outros Fluidos (m³)]"
)
ORDEM_VOLUMES = ("oleo", "condensado", "gas_assoc", "gas_nao_assoc", "agua", "inj_gas", "inj_agua_rec",
                 "inj_agua_desc", "inj_co2", "inj_n2", "inj_vapor", "inj_polimeros", "inj_outros")


def numero_br(valor) -> str:
    if valor is None:
        return ""
    texto = f"{valor:,.3f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f'"{texto}"'


def linha(mes: int, campo: str, poco: str, estado="Rio de Janeiro", bacia="Santos", ambiente="MAR",
          instalacao="FPSO TESTE", **volumes) -> str:
    ano, m = divmod(mes, 100)
    produtor = any(volumes.get(c) for c in ORDEM_VOLUMES[:5])
    valores = []
    for c in ORDEM_VOLUMES:
        v = volumes.get(c)
        if v is None and c in ORDEM_VOLUMES[:5] and produtor:
            v = 0.0
        valores.append(numero_br(v))
    return ",".join([str(ano), f"{m:02d}/{ano}", estado, bacia, campo, poco, ambiente, instalacao, *valores])


def csv_anp(linhas: list[str]) -> bytes:
    return ("﻿" + "\r\n".join([CABECALHO, *linhas]) + "\r\n").encode("utf-8")


# Arquivo mensal "Produção por zona": média diária por poço e zona, com duas casas.
CABECALHO_ZONA = (
    "Zona,Nome poço ANP,Campo,Bacia,Ambiente,Data,Óleo (m³/d),Condensado (m³/d),Petróleo (m³/d),"
    "Gás associado (Mil m³/d),Gás não associado (Mil m³/d),Gás total (Mil m³/d),Água (m³/d),"
    "Água injetada para descarte (m³/d),Água injetada para rec. secundária (m³/d),Vapor injetado (t/d),"
    "Gás injetado (Mil m³/d),CO2 injetado (Mil m³/d),N2 injetado (Mil m³/d),Outros fluidos injetados (m³/d)"
)


def numero_br2(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f'"{texto}"'


def linha_zona(mes: int, campo: str, poco: str, zona: str = "Z1", bacia: str = "Santos",
               ambiente: str = "MAR", data_com_dia: bool = True, **diarios) -> str:
    ano, m = divmod(mes, 100)
    data = f"01/{m:02d}/{ano}" if data_com_dia else f"{m:02d}/{ano}"
    d = {c: float(diarios.get(c, 0.0)) for c in (
        "oleo", "condensado", "gas_assoc", "gas_nao_assoc", "agua", "inj_agua_desc", "inj_agua_rec",
        "inj_vapor", "inj_gas", "inj_co2", "inj_n2", "inj_outros")}
    valores = [d["oleo"], d["condensado"], d["oleo"] + d["condensado"], d["gas_assoc"], d["gas_nao_assoc"],
               d["gas_assoc"] + d["gas_nao_assoc"], d["agua"], d["inj_agua_desc"], d["inj_agua_rec"],
               d["inj_vapor"], d["inj_gas"], d["inj_co2"], d["inj_n2"], d["inj_outros"]]
    return ",".join([zona, poco, campo, bacia, ambiente, data, *map(numero_br2, valores)])


def csv_zona(linhas: list[str]) -> bytes:
    return ("\r\n".join([CABECALHO_ZONA, *linhas]) + "\r\n").encode("utf-8")


def zona_do_mes(mes: int, pocos_gama: int = 10) -> bytes:
    """Arquivo por zona coerente com o site padrão: mesmos poços e volumes, em média diária.
    O poço 7-GAM-1-BA produz por duas zonas."""
    from anp_producao.formatos import dias_no_mes

    dias = dias_no_mes(mes)
    com_dia = mes % 2 == 0  # a ANP usa 01/MM/AAAA em uns meses e MM/AAAA em outros
    linhas = []
    for poco in ("7-ALF-1-RJS", "7-ALF-2-RJS"):
        linhas.append(linha_zona(mes, "ALFA", poco, data_com_dia=com_dia, oleo=50.0,
                                 gas_assoc=500.0 / dias, agua=(mes % 100) / dias))
    linhas.append(linha_zona(mes, "ALFA", "8-ALF-9-RJS", data_com_dia=com_dia, inj_agua_rec=3000.0 / dias))
    linhas.append(linha_zona(mes, "BETA", "7-BET-1-ESS", bacia="Campos", data_com_dia=com_dia,
                             oleo=31.0 / dias, condensado=10.0 / dias, gas_nao_assoc=100.0 / dias, agua=5.0 / dias))
    for i in range(1, pocos_gama + 1):
        kw = dict(bacia="Recôncavo", ambiente="TERRA", data_com_dia=com_dia)
        if i == 1:
            linhas.append(linha_zona(mes, "GAMA", "7-GAM-1-BA", zona="GAM-A", oleo=0.6, gas_assoc=0.06, agua=1.2, **kw))
            linhas.append(linha_zona(mes, "GAMA", "7-GAM-1-BA", zona="GAM-B", oleo=0.4, gas_assoc=0.04, agua=0.8, **kw))
        else:
            linhas.append(linha_zona(mes, "GAMA", f"7-GAM-{i}-BA", zona="GAM-A", oleo=1.0, gas_assoc=0.1, agua=2.0, **kw))
    return csv_zona(linhas)


def publicar_zonas_padrao(site: "SiteANP", meses=range(202601, 202610)) -> None:
    """Publica os arquivos por zona de 2026, com o nome estranho que a ANP usou em março."""
    rotulos = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro")
    for mes in meses:
        m = mes % 100
        nome = "Produo_Zona_032026.csv" if mes == 202603 else f"producao_zona_{m:02d}-{mes // 100}.csv"
        site.publicar_zona(nome, zona_do_mes(mes), rotulos[m - 1])


class SiteANP:
    """Página e arquivos da ANP em memória, com o comportamento real do servidor:
    HEAD devolve 403 e Range devolve 206 com Content-Range."""

    def __init__(self):
        self.arquivos: dict[str, bytes] = {}
        self.rotulos: dict[str, str] = {}
        self.cadastros: dict[str, bytes] = {
            BASE + "informacoes-sobre-campos/extracao-campo.csv": b"",
            BASE + "informacoes-sobre-campos/extracao-campo-concessionarios.csv": b"",
        }
        self.atualizado_em = "03/11/2026 16h20"
        self.requisicoes: Counter = Counter()
        self.downloads: Counter = Counter()
        self.falhas: dict[str, int] = {}

    def publicar(self, caminho: str, conteudo: bytes, rotulo: str = "arquivo") -> str:
        url = PRODUCAO + caminho
        self.arquivos[url] = conteudo
        self.rotulos[url] = rotulo
        return url

    def publicar_zona(self, nome: str, conteudo: bytes, rotulo: str = "mês") -> str:
        url = ZONA + nome
        self.arquivos[url] = conteudo
        self.rotulos[url] = rotulo
        return url

    def retirar(self, caminho: str) -> None:
        self.arquivos.pop(PRODUCAO + caminho, None)

    def cadastro(self, campos: bytes, concessionarios: bytes) -> None:
        self.cadastros[BASE + "informacoes-sobre-campos/extracao-campo.csv"] = campos
        self.cadastros[BASE + "informacoes-sobre-campos/extracao-campo-concessionarios.csv"] = concessionarios

    def html(self) -> str:
        itens = [f'<li><a href="{u}" class="internal-link">{self.rotulos[u]}</a></li>' for u in self.arquivos]
        itens += [f'<li><a href="{u}">cadastro</a></li>' for u in self.cadastros]
        return (f"<html><body><span>Atualizado em {self.atualizado_em}</span><ul>"
                + "".join(itens) + "</ul></body></html>")

    def _responder(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requisicoes[(request.method, url)] += 1
        if request.method == "HEAD":
            return httpx.Response(403, text="Forbidden")
        if self.falhas.get(url, 0) > 0:
            self.falhas[url] -= 1
            return httpx.Response(503, text="indisponível")
        if url == PAGINA:
            return httpx.Response(200, text=self.html(), headers={"content-type": "text/html; charset=utf-8"})
        conteudo = self.arquivos.get(url, self.cadastros.get(url))
        if conteudo is None:
            return httpx.Response(404, text="não encontrado")
        faixa = request.headers.get("range")
        if faixa == "bytes=0-0":
            return httpx.Response(206, content=conteudo[:1],
                                  headers={"content-range": f"bytes 0-0/{len(conteudo)}", "content-type": "text/csv"})
        self.downloads[url] += 1
        return httpx.Response(200, content=conteudo, headers={"content-type": "text/csv"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._responder)


@pytest.fixture(autouse=True)
def limiar_parcial_para_testes(monkeypatch):
    """O site simulado tem 10 poços por mês em terra; em produção o mínimo é 100 linhas."""
    from anp_producao import qualidade

    monkeypatch.setattr(qualidade, "MIN_LINHAS_PARCIAL", 5)


@pytest.fixture
def config(tmp_path) -> Config:
    cfg = Config(dir_dados=tmp_path / "dados", url_pagina=PAGINA, porta=8799)
    cfg.garantir_pastas()
    return cfg


def _campos_csv() -> bytes:
    linhas = [
        "CODIGO_CAMPO,CAMPO,SIGLA_CAMPO,BACIA,ESTADO,OPERADOR,AMBIENTE,RODADA,NOME_CONTRATO,NUMERO_CONTRATO,"
        "SITUACAO_CAMPO,DATA_ASSINATURA,TERMINO_FASE,DATA_INICIO_PRODUCAO,DATA_TERMINO_PRODUCAO",
        "1,ALFA,ALF,Santos,Rio de Janeiro,PETROBRAS,Mar,BID0,ALF,1,Produção,06/08/1998,05/08/2052,01/01/2010,-",
        "2,BETA,BET,Campos,Espírito Santo,PETROBRAS,Mar,BID0,BET,2,Produção,06/08/1998,05/08/2052,01/01/2010,-",
        "3,GAMA,GAM,Recôncavo,Bahia,TERRA FORTE,Terra,BID13,GAM,3,Produção,23/12/2015,07/06/2051,-,-",
    ]
    return ("﻿" + "\r\n".join(linhas)).encode("utf-8")


def _concessionarios_csv() -> bytes:
    linhas = [
        "CODIGO_CAMPO,CAMPO,BACIA,AMBIENTE,ESTADO,RODADA,SITUACAO,RAZAO_SOCIAL/CONCESSIONARIO,CNPJ,PARTICIPACAO (%),OPERADOR",
        '1,ALFA,Santos,Mar,Rio de Janeiro,BID0,Produção,PETRÓLEO BRASILEIRO S.A. - PETROBRAS,33000167000101,"60,00",S',
        '1,ALFA,Santos,Mar,Rio de Janeiro,BID0,Produção,SHELL BRASIL PETRÓLEO LTDA,10456016000167,"40,00",N',
        '2,BETA,Campos,Mar,Espírito Santo,BID0,Produção,PETRÓLEO BRASILEIRO S.A. - PETROBRAS,33000167000101,"100,00",S',
        '3,GAMA,Recôncavo,Terra,Bahia,BID13,Produção,TERRA FORTE S.A.,11111111000111,"100,00",S',
    ]
    return ("﻿" + "\r\n".join(linhas)).encode("utf-8")


def montar_site_padrao() -> SiteANP:
    """Três campos com números redondos para conferir contas:

    * ALFA (mar, Santos): poços A1 e A2 produzem 50 m³/d de óleo cada (100 m³/d no campo),
      1.000 mil m³ de gás no mês e água crescente. Injetor A9 com água.
    * BETA (mar, Campos): poço B1 com 31 m³ de óleo por mês e condensado.
    * GAMA (terra, Recôncavo): 10 poços com 1 m³/d cada. A fonte não traz abr/2026
      nem jul/2026, e mar/2026 vem só com 3 poços (mês parcial).
    """
    from anp_producao.formatos import dias_no_mes

    site = SiteANP()
    site.cadastro(_campos_csv(), _concessionarios_csv())

    def mar(ano: int, meses: range) -> list[str]:
        linhas = []
        for m in meses:
            mes = ano * 100 + m
            dias = dias_no_mes(mes)
            for poco in ("7-ALF-1-RJS", "7-ALF-2-RJS"):
                linhas.append(linha(mes, "ALFA", poco, oleo=50.0 * dias, gas_assoc=500.0,
                                    agua=float(m), instalacao="FPSO ALFA"))
            linhas.append(linha(mes, "ALFA", "8-ALF-9-RJS", inj_agua_rec=3000.0, instalacao="FPSO ALFA"))
            linhas.append(linha(mes, "BETA", "7-BET-1-ESS", estado="Espírito Santo", bacia="Campos",
                                oleo=31.0, condensado=10.0, gas_nao_assoc=100.0, agua=5.0,
                                instalacao="PLATAFORMA BETA"))
        return linhas

    def terra(ano: int, meses, parcial: dict | None = None) -> list[str]:
        linhas = []
        for m in meses:
            mes = ano * 100 + m
            dias = dias_no_mes(mes)
            n = (parcial or {}).get(m, 10)
            for i in range(1, n + 1):
                linhas.append(linha(mes, "GAMA", f"7-GAM-{i}-BA", estado="Bahia", bacia="Recôncavo",
                                    ambiente="TERRA", instalacao="ESTAÇÃO GAMA", oleo=1.0 * dias,
                                    gas_assoc=0.1 * dias, agua=2.0 * dias))
        return linhas

    site.publicar("pm/producao-mar-2026.csv", csv_anp(mar(2026, range(1, 10))), "2026")
    site.publicar("pm/producao-mar-2025.csv", csv_anp(mar(2025, range(1, 13))), "2025")
    site.publicar("pm/producao-mar-2016-2018.csv", csv_anp(mar(2018, range(1, 13))), "2016-2018")
    for t, meses in ((1, (1, 2, 3)), (2, (4, 5, 6)), (3, (7, 8, 9)), (4, (10, 11, 12))):
        site.publicar(f"pt/2025/producao_por_poco_terra_2025_{t}_trim.csv", csv_anp(terra(2025, meses)), f"{t}º trimestre")
    site.publicar("pt/2026/producao_por_poco_terra_2026_1_trim.csv", csv_anp(terra(2026, (1, 2, 3), {3: 3})), "1º trimestre")
    site.publicar("pt/2026/producao_por_poco_terra_2026_2_trim.csv", csv_anp(terra(2026, (5, 6))), "2º trimestre")
    site.publicar("pt/2026/producao_por_poco_terra_2026_3_trim.csv", csv_anp(terra(2026, (8, 9))), "3º trimestre")
    return site


@pytest.fixture
def site() -> SiteANP:
    return montar_site_padrao()


@pytest.fixture
def site_zona(site) -> SiteANP:
    """Site padrão mais os arquivos mensais por zona de 2026."""
    publicar_zonas_padrao(site)
    return site


@pytest.fixture
def cliente_factory(config, site):
    from anp_producao.fonte_anp import ClienteANP

    def _fabricar():
        return ClienteANP(config, transport=site.transport(), pausa_entre_tentativas=0)

    return _fabricar


@pytest.fixture
def base_carregada(config, site, cliente_factory):
    """Base com o site padrão já carregado. Devolve o resumo da atualização."""
    from anp_producao.atualizacao import atualizar

    with cliente_factory() as cliente:
        return atualizar(config, origem="teste", cliente=cliente)
