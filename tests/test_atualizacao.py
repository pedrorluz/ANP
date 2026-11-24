from datetime import datetime

import pytest

from anp_producao import atualizacao, qualidade
from anp_producao.atualizacao import AtualizacaoEmAndamento, Trava, atualizar, reprocessar_brutos
from anp_producao.db import conectar_anp
from conftest import PRODUCAO, csv_anp, linha


def agregado(config, campo, mes):
    con = conectar_anp(config)
    try:
        return con.execute(
            "SELECT p.* FROM producao_campo_mes p JOIN campos c ON c.id = p.campo_id "
            "WHERE c.nome = ? AND p.mes = ?", (campo, mes)
        ).fetchone()
    finally:
        con.close()


def test_primeira_carga(config, base_carregada):
    r = base_carregada
    assert r["status"] == "ok", r
    assert r["arquivos_total"] == 10 and r["arquivos_alterados"] == 10
    assert r["ultimo_mes"] == 202609
    assert r["pagina_atualizada_em"] == "2026-11-03T16:20"
    alfa = agregado(config, "ALFA", 202601)
    assert alfa["oleo"] == pytest.approx(2 * 50 * 31)
    assert alfa["gas_assoc"] == pytest.approx(1000.0)
    assert alfa["inj_agua_rec"] == pytest.approx(3000.0)
    assert (alfa["n_produtores"], alfa["n_injetores"], alfa["n_linhas"]) == (2, 1, 3)
    con = conectar_anp(config)
    try:
        campos = {r["nome"]: dict(r) for r in con.execute("SELECT * FROM campos")}
        assert campos["GAMA"]["ambientes"] == "TERRA" and campos["ALFA"]["ambientes"] == "MAR"
        assert campos["ALFA"]["bacia"] == "Santos" and campos["BETA"]["estado"] == "Espírito Santo"
        assert campos["ALFA"]["primeiro_mes"] == 201801 and campos["ALFA"]["ultimo_mes"] == 202609
        assert con.execute("SELECT COUNT(*) FROM concessionarios").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM cadastro_campos").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM cobertura WHERE ambiente='TERRA' AND mes=202604").fetchone()[0] == 0
        assert con.execute("SELECT status FROM atualizacoes").fetchone()[0] == "ok"
    finally:
        con.close()
    assert (config.dir_brutos / "mar" / "producao-mar-2026.csv.gz").exists()


def test_segunda_carga_sem_novidade_so_baixa_a_janela_recente(config, site, cliente_factory, base_carregada):
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert r["arquivos_alterados"] == 0 and r["status"] == "ok"
    assert "Nenhuma novidade" in r["mensagem"]
    limite = datetime.now().year - config.janela_revisao_anos + 1
    antigo = PRODUCAO + "pm/producao-mar-2016-2018.csv"
    recente = PRODUCAO + "pm/producao-mar-2026.csv"
    assert site.downloads[recente] == (2 if 2026 >= limite else 1)
    assert site.downloads[antigo] == 1  # só conferido pelo tamanho
    assert site.requisicoes[("GET", antigo)] == 2


def test_arquivo_antigo_com_tamanho_novo_e_baixado(config, site, cliente_factory, base_carregada):
    antigo = "pm/producao-mar-2016-2018.csv"
    linhas = [linha(201801, "ALFA", "7-ALF-1-RJS", oleo=999.0, instalacao="FPSO ALFA")]
    site.publicar(antigo, csv_anp(linhas), "2016-2018")
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert r["arquivos_alterados"] == 1
    assert agregado(config, "ALFA", 201801)["oleo"] == pytest.approx(999.0)
    assert agregado(config, "ALFA", 201802) is None  # meses que saíram do arquivo somem


def test_revisao_de_mes_antigo_e_detectada_e_mes_novo_nao(config, site, cliente_factory, base_carregada):
    from anp_producao.formatos import dias_no_mes

    linhas = []
    for m in range(1, 11):
        mes = 202600 + m
        dias = dias_no_mes(mes)
        oleo = (60.0 if m == 1 else 50.0) * dias
        for poco in ("7-ALF-1-RJS", "7-ALF-2-RJS"):
            linhas.append(linha(mes, "ALFA", poco, oleo=oleo, gas_assoc=500.0, agua=float(m), instalacao="FPSO ALFA"))
        linhas.append(linha(mes, "BETA", "7-BET-1-ESS", estado="Espírito Santo", bacia="Campos",
                            oleo=31.0, condensado=10.0, gas_nao_assoc=100.0, agua=5.0, instalacao="PLATAFORMA BETA"))
    site.publicar("pm/producao-mar-2026.csv", csv_anp(linhas), "2026")
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert r["ultimo_mes"] == 202610
    assert r["revisoes"] >= 1
    con = conectar_anp(config)
    try:
        revs = con.execute(
            "SELECT c.nome, r.mes, r.variavel, r.antes, r.depois FROM revisoes r JOIN campos c ON c.id = r.campo_id"
        ).fetchall()
    finally:
        con.close()
    assert {(x["nome"], x["mes"], x["variavel"]) for x in revs} == {("ALFA", 202601, "petroleo")}
    rev = revs[0]
    assert rev["antes"] == pytest.approx(3100.0) and rev["depois"] == pytest.approx(3720.0)


def test_arquivo_que_sai_da_pagina_e_removido(config, site, cliente_factory, base_carregada):
    site.retirar("pm/producao-mar-2016-2018.csv")
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert any("saiu da página" in a for a in r["avisos"])
    assert agregado(config, "ALFA", 201801) is None


def test_pagina_quase_vazia_nao_apaga_a_base(config, site, cliente_factory, base_carregada):
    for caminho in list(site.arquivos):
        if "2026" not in caminho:
            site.arquivos.pop(caminho)
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert any("mantidos por segurança" in a for a in r["avisos"])
    assert agregado(config, "ALFA", 201801) is not None


def test_arquivo_com_formato_invalido_nao_derruba_os_outros(config, site, cliente_factory):
    site.publicar("pm/producao-mar-2024.csv", b"isto nao e um csv da anp\n1,2,3\n", "2024")
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    assert r["status"] == "ok_com_avisos"
    assert any("producao-mar-2024.csv" in e for e in r["erros"])
    assert r["ultimo_mes"] == 202609


def test_mes_repetido_em_dois_arquivos_vale_o_mais_recente(config, site, cliente_factory):
    from anp_producao.formatos import dias_no_mes

    linhas = [linha(202606, "GAMA", f"7-GAM-{i}-BA", estado="Bahia", bacia="Recôncavo", ambiente="TERRA",
                    instalacao="ESTAÇÃO GAMA", oleo=5.0 * dias_no_mes(202606)) for i in range(1, 4)]
    site.publicar("pt/2026/producao_por_poco_terra_2026_4_trim.csv", csv_anp(linhas), "4º trimestre")
    with cliente_factory() as c:
        r = atualizar(config, cliente=c)
    g = agregado(config, "GAMA", 202606)
    assert g["n_linhas"] == 3  # só o arquivo do 4º trimestre, sem somar o do 2º
    assert g["oleo"] == pytest.approx(3 * 5.0 * 30)
    assert any("mais recente" in a for a in r["avisos"])


def test_trava_impede_duas_atualizacoes(config, cliente_factory):
    with Trava(config.arquivo_trava):
        assert atualizacao.atualizacao_em_andamento(config)
        with cliente_factory() as c, pytest.raises(AtualizacaoEmAndamento):
            atualizar(config, cliente=c)
    assert not atualizacao.atualizacao_em_andamento(config)


def test_lacunas_da_fonte(config, base_carregada):
    con = conectar_anp(config)
    try:
        lac = qualidade.lacunas(con)
    finally:
        con.close()
    assert lac["TERRA"] == {202603: "parcial", 202604: "ausente", 202607: "ausente"}
    assert 202604 not in lac["MAR"] and 201901 in lac["MAR"]


def test_reprocessar_do_cache_local(config, base_carregada):
    antes = agregado(config, "GAMA", 202602)["oleo"]
    r = reprocessar_brutos(config)
    assert r["arquivos"] == 10
    assert agregado(config, "GAMA", 202602)["oleo"] == pytest.approx(antes)


def test_iniciar_em_segundo_plano_dispara_processo(config, monkeypatch):
    chamadas = []

    class Falso:
        def __init__(self, comando, **kwargs):
            chamadas.append((comando, kwargs))

    monkeypatch.setattr(atualizacao.subprocess, "Popen", Falso)
    r = atualizacao.iniciar_em_segundo_plano(config, origem="web", completa=True)
    assert r == {"iniciada": True}
    comando, kwargs = chamadas[0]
    assert comando[1:4] == ["-m", "anp_producao", "atualizar"] and "--completa" in comando
    assert kwargs["env"]["ANP_DIR_DADOS"] == str(config.dir_dados)
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"  # log em UTF-8 também no Windows
    assert kwargs["start_new_session"] is True


def test_no_windows_a_atualizacao_sai_desligada_da_janela(config, monkeypatch):
    chamadas = []
    monkeypatch.setattr(atualizacao, "NO_WINDOWS", True)
    monkeypatch.setattr(atualizacao.subprocess, "Popen", lambda comando, **kw: chamadas.append(kw))
    monkeypatch.setattr(atualizacao, "atualizacao_em_andamento", lambda cfg: False)
    atualizacao.iniciar_em_segundo_plano(config)
    kwargs = chamadas[0]
    assert "start_new_session" not in kwargs
    assert kwargs["creationflags"] == 0x200 | 0x8  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS


class MsvcrtFalso:
    """Imita msvcrt.locking: um byte travado por arquivo, erro se outro identificador tentar."""

    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self):
        self.travados: dict[tuple, int] = {}
        self.chamadas = []

    def locking(self, fd, modo, n):
        import os

        info = os.fstat(fd)
        arquivo = (info.st_dev, info.st_ino)  # o mesmo arquivo aberto por outro identificador
        self.chamadas.append((modo, n, os.lseek(fd, 0, os.SEEK_CUR)))
        if modo == self.LK_NBLCK:
            if arquivo in self.travados:
                raise OSError(36, "Resource deadlock avoided")
            self.travados[arquivo] = fd
        else:
            self.travados.pop(arquivo, None)


def test_trava_no_windows_usa_msvcrt(config, monkeypatch):
    falso = MsvcrtFalso()
    monkeypatch.setattr(atualizacao, "NO_WINDOWS", True)
    monkeypatch.setattr(atualizacao, "msvcrt", falso, raising=False)
    with Trava(config.arquivo_trava):
        with pytest.raises(AtualizacaoEmAndamento):
            with Trava(config.arquivo_trava):
                pass
        assert atualizacao.atualizacao_em_andamento(config) is True
    assert atualizacao.atualizacao_em_andamento(config) is False
    assert all(n == 1 and posicao == 0 for _, n, posicao in falso.chamadas)  # sempre o primeiro byte
    assert falso.travados == {}


def test_poucos_pocos_nao_viram_mes_parcial(config, base_carregada, monkeypatch):
    from anp_producao import qualidade

    monkeypatch.setattr(qualidade, "MIN_LINHAS_PARCIAL", 100)
    con = conectar_anp(config)
    try:
        lac = qualidade.lacunas(con)
    finally:
        con.close()
    assert 202603 not in lac["TERRA"] and lac["TERRA"][202604] == "ausente"
