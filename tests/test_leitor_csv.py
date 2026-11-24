"""Leitura dos CSVs com as variações encontradas nos arquivos reais da ANP.

As amostras em fixtures/ usam linhas reais publicadas pela ANP e algumas
linhas sintéticas no mesmo formato (para cobrir milhar com dois pontos,
vírgula dentro de nome e linhas quebradas).
"""

import math

import pytest

from anp_producao.leitor_csv import (
    FormatoInvalido,
    chave_nome,
    interpretar_mes,
    ler_producao,
    ler_producao_zona,
    ler_tabela_simples,
)
from conftest import FIXTURES, csv_zona, linha_zona


def ler(nome, ambiente=None):
    return ler_producao((FIXTURES / nome).read_bytes(), ambiente_padrao=ambiente)


def test_arquivo_novo_utf8_com_bom_e_colchetes():
    r = ler("mar_2026_amostra.csv", "MAR")
    df = r.dados
    assert r.codificacao == "utf-8-sig"
    assert len(df) == 7 and r.linhas_descartadas == 0
    paru = df[df["poco"] == "4-ALS-39-AL"].iloc[0]
    assert paru["mes"] == 202601
    assert paru["campo"] == "PARU" and paru["ambiente"] == "MAR"
    assert paru["condensado"] == pytest.approx(414.92)
    assert paru["gas_nao_assoc"] == pytest.approx(3127.441)  # milhar com ponto
    assert paru["agua"] == pytest.approx(220.65)
    assert math.isnan(paru["inj_gas"])  # coluna vazia
    buzios = df[df["campo"] == "BÚZIOS"].iloc[0]
    assert buzios["oleo"] == pytest.approx(1118730.564)  # dois separadores de milhar
    assert r.mes_min == 202601 and r.mes_max == 202609


def test_injetor_com_producao_vazia():
    df = ler("mar_2026_amostra.csv").dados
    inj = df[df["poco"] == "7-TTG-3D-SES"].iloc[0]
    assert math.isnan(inj["oleo"])
    assert inj["inj_agua_desc"] == pytest.approx(0.32)


def test_mesmo_poco_em_dois_campos_vira_duas_linhas():
    df = ler("mar_2026_amostra.csv").dados
    poco = df[df["poco"] == "6-BRSA-1222A-ESS"]
    assert set(poco["campo"]) == {"AnC_Jubarte_Nordeste", "AnC_Jubarte_Sudoeste"}


def test_arquivo_antigo_cp1252_com_linhas_embrulhadas_em_aspas():
    r = ler("mar_2021_cp1252_amostra.csv", "MAR")
    df = r.dados
    assert r.codificacao == "cp1252"
    assert len(df) == 3 and r.linhas_descartadas == 0
    paru = df[df["poco"] == "4-ALS-39-AL"].iloc[0]
    assert paru["instalacao"] == "Estação de Paru"  # acento preservado
    assert paru["condensado"] == pytest.approx(279.931)
    assert paru["gas_nao_assoc"] == pytest.approx(4424.25429)
    assert paru["ambiente"] == "MAR"  # 'Mar' normalizado
    manati = df[df["campo"] == "MANATI"].iloc[0]
    assert manati["agua"] == pytest.approx(84.038)


def test_ordem_de_colunas_diferente_e_mes_abreviado():
    df = ler("terra_2025_t4_ordem_variante.csv", "TERRA").dados
    assert set(df["mes"]) == {202510, 202511, 202512}
    rem = df[(df["campo"] == "REMANSO") & (df["mes"] == 202510)].iloc[0]
    assert rem["estado"] == "Bahia" and rem["instalacao"] == "Estação Remanso"
    assert rem["oleo"] == pytest.approx(30.751)
    assert rem["agua"] == pytest.approx(472.974)
    assert "CAMPO, COM VÍRGULA" in set(df["campo"])


def test_instalacao_no_meio_e_linhas_com_colunas_sobrando():
    r = ler("terra_2022_t4_instalacao_no_meio.csv", "TERRA")
    df = r.dados
    assert len(df) == 3
    assert r.linhas_descartadas == 1  # 'linha quebrada sem estrutura'
    assert set(df["instalacao"]) == {"Estação Coletora de Candeias"}
    nov = df[df["mes"] == 202211].iloc[0]
    assert nov["inj_agua_rec"] == pytest.approx(150.0)
    assert nov["ambiente"] == "TERRA"


def test_historico_antigo_com_inteiros_sem_aspas():
    df = ler("terra_1941_1966_amostra.csv", "TERRA").dados
    primeiro = df[df["mes"] == 194112].iloc[0]
    assert primeiro["oleo"] == pytest.approx(30.0)
    assert df[df["mes"] == 194201].iloc[0]["oleo"] == pytest.approx(22.2)
    assert primeiro["instalacao"] == "Não Informado"


@pytest.mark.parametrize("texto,esperado", [
    ("01/2026", 202601), ("1/2026", 202601), ("2026/09", 202609), ("out/2025", 202510),
    ("set/26", 202609), ("dez.2025", 202512), ("202603", 202603), ("15/03/2026", 202603),
    ("13/2026", None), ("", None), ("xyz", None),
])
def test_interpretar_mes(texto, esperado):
    assert interpretar_mes(texto) == esperado


def test_cabecalho_sem_colunas_obrigatorias():
    with pytest.raises(FormatoInvalido):
        ler_producao("a,b,c\r\n1,2,3\r\n".encode())


def test_arquivo_vazio():
    with pytest.raises(FormatoInvalido):
        ler_producao(b"")


def test_numeros_em_formato_internacional_sao_detectados():
    conteudo = ("Mês/Ano,Campo,Poço,Produção de Óleo (m³)\n"
                "01/2026,X,P1,1234.5\n01/2026,X,P2,0.25\n").encode()
    r = ler_producao(conteudo, "MAR")
    assert list(r.dados["oleo"]) == pytest.approx([1234.5, 0.25])
    assert any("internacional" in a for a in r.avisos)


def test_gas_em_m3_e_convertido_para_mil_m3():
    conteudo = ("Mês/Ano,Campo,Poço,Produção de Óleo (m³),Produção de Gás Associado (m³)\n"
                '01/2026,X,P1,"1,0","2.500,0"\n').encode()
    r = ler_producao(conteudo, "MAR")
    assert r.dados.iloc[0]["gas_assoc"] == pytest.approx(2.5)


def test_tabela_simples_de_cadastro():
    regs = ler_tabela_simples((FIXTURES / "extracao-campo-concessionarios.csv").read_bytes())
    assert regs[0]["campo"] == "ARARACANGA"
    assert regs[1]["situacao"] == "Em devolução, licitado"
    assert regs[0]["participacao (%)"] == "100,00"


def test_chave_nome_ignora_acentos_e_espacos():
    assert chave_nome("  Búzios ") == chave_nome("BUZIOS")
    assert chave_nome("Mata  de São João") == "MATA DE SAO JOAO"


# --------------------------------------------------------------------------- #
# Arquivo mensal "Produção por zona"
# --------------------------------------------------------------------------- #

def test_zona_soma_as_zonas_do_poco_e_converte_media_diaria_em_volume_do_mes():
    r = ler_producao_zona((FIXTURES / "zona_04_2026_amostra.csv").read_bytes())
    df = r.dados.set_index("poco")
    assert set(df["mes"]) == {202604} and len(df) == 5  # 6 linhas, um poço com duas zonas
    cp = df.loc["7-CP-1974D-SE"]  # abril tem 30 dias
    assert (cp["oleo"], cp["gas_assoc"], cp["agua"]) == (pytest.approx(120.0), pytest.approx(4.5), pytest.approx(450.0))
    assert cp["campo"] == "CARMÓPOLIS" and cp["bacia"] == "Sergipe" and cp["ambiente"] == "TERRA"
    assert cp["instalacao"] == "Não Informado"
    inj = df.loc["8-CP-100-SE"]
    assert inj["inj_agua_desc"] == pytest.approx(360.0)
    assert inj["inj_agua_rec"] == pytest.approx(1234.56 * 30)
    assert inj["inj_vapor"] == pytest.approx(90.0)
    assert df.loc["1-IMET-1-BA"]["gas_nao_assoc"] == pytest.approx(6.15 * 30)
    atl = df.loc["7-ATL-2HP-RJS"]
    assert atl["ambiente"] == "MAR" and atl["oleo"] == pytest.approx(497.45 * 30)
    assert math.isnan(atl["inj_polimeros"])  # coluna que o arquivo por zona não tem


def test_zona_ate_meados_de_2025_tem_colunas_de_geologia_e_periodo_nao_e_o_mes():
    # até jun/2025 o arquivo traz Pré-sal, Período (geológico), Época, Idade, Grupo, Formação e Membro
    r = ler_producao_zona((FIXTURES / "zona_01_2025_geologia_amostra.csv").read_bytes())
    df = r.dados.set_index("poco")
    assert set(df["mes"]) == {202501} and len(df) == 2 and r.linhas_descartadas == 0
    imet = df.loc["1-IMET-1-BA"]
    assert imet["gas_nao_assoc"] == pytest.approx(1.0 * 31) and imet["agua"] == pytest.approx(0.03 * 31)
    assert imet["campo"] == "CARDEAL AMARELO" and imet["bacia"] == "Recôncavo"
    buz = df.loc["7-BUZ-1-RJS"]
    assert buz["ambiente"] == "MAR" and buz["campo"] == "BÚZIOS"
    assert buz["oleo"] == pytest.approx(25000.0 * 31) and buz["gas_assoc"] == pytest.approx(1500.5 * 31)


def test_zona_com_mes_no_formato_mm_aaaa():
    conteudo = csv_zona([linha_zona(202512, "X", "P1", data_com_dia=False, oleo=2.0, gas_assoc=0.5)])
    r = ler_producao_zona(conteudo)
    linha = r.dados.iloc[0]
    assert linha["mes"] == 202512
    assert linha["oleo"] == pytest.approx(62.0) and linha["gas_assoc"] == pytest.approx(15.5)


def test_arquivo_por_poco_nao_passa_como_zona():
    with pytest.raises(FormatoInvalido):
        ler_producao_zona((FIXTURES / "mar_2026_amostra.csv").read_bytes())
