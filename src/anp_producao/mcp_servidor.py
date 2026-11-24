"""Servidor MCP (stdio) para agentes: Hermes, Claude Desktop, Claude Code e afins.

Rodar: ``uv run --directory <pasta do projeto> anp mcp``

As ferramentas usam as mesmas operações do site (anp_producao.servicos), então
o que o agente vê é o que aparece na página.
"""

from __future__ import annotations

from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from anp_producao.atualizacao import AtualizacaoEmAndamento
from anp_producao.config import Config
from anp_producao.formatos import mes_iso, somar_meses, ler_mes
from anp_producao.servicos import ErroUsuario, NaoEncontrado, Servicos

INSTRUCOES = """\
Produção mensal de petróleo, gás natural e água por campo no Brasil, a partir dos dados
abertos da ANP (produção por poço, somada por campo). Base local, atualizada sob demanda.

Alvos (texto "tipo:valor"): cluster:NOME, campo:NOME, poco:NOME, instalacao:NOME,
bacia:NOME, estado:NOME, operador:NOME, empresa:RAZÃO SOCIAL, ambiente:MAR|TERRA, todos.
Sem prefixo, procura um cluster e depois um campo. Nomes de campo seguem a ANP (ex.: BÚZIOS,
TUPI, MERO) e a busca ignora acentos e caixa.

Variáveis: petroleo (óleo + condensado, padrão da ANP), oleo, condensado, gas, gas_assoc,
gas_nao_assoc, agua, boe, rgo, corte_agua, inj_agua, inj_agua_desc, inj_gas, inj_co2, inj_vapor.
Unidades padrão: bbl/d para líquidos, mil m3/d para gás, boe/d para boe. Valores são médias
diárias do mês (volume do mês dividido pelos dias do mês), salvo unidades "/mes".

Cuidados ao responder:
- A ANP publica com cerca de dois meses de atraso; use status_base para saber o último mês.
- Meses que a ANP não publicou (ou publicou incompletos) voltam como null, nunca como zero.
- Produção líquida (liquida_de) aplica a participação atual da empresa a todo o histórico.
- Para comparar, passe vários alvos em serie_producao; para composição, use quebra.
"""


@contextmanager
def _erros_para_o_agente():
    try:
        yield
    except NaoEncontrado as exc:
        raise ToolError(str(exc)) from exc
    except AtualizacaoEmAndamento as exc:
        raise ToolError(str(exc)) from exc
    except ErroUsuario as exc:
        raise ToolError(str(exc)) from exc


def _compactar_cluster(c: dict, limite_campos: int = 60) -> dict:
    campos = c.get("campos_resolvidos", [])
    return {
        "nome": c["nome"], "descricao": c["descricao"], "n_campos": c.get("n_campos", len(campos)),
        "campos": campos[:limite_campos] + (["..."] if len(campos) > limite_campos else []),
        "regra": {k: v for k, v in c["regra"].items() if v}, "incluir": c["incluir"], "excluir": c["excluir"],
        "nao_encontrados": c.get("nao_encontrados", []),
    }


def criar_servidor(config: Config | None = None) -> MCPServer:
    svc = Servicos(config)
    srv = MCPServer(name="anp-producao", title="Produção ANP por campo", instructions=INSTRUCOES)

    @srv.tool()
    def status_base() -> dict:
        """Situação da base local: último mês com dado, data da última atualização,
        atualização em andamento (com progresso), revisões não vistas e meses que a
        ANP deixou de publicar nos últimos 24 meses."""
        with _erros_para_o_agente():
            s = svc.status()
            s.pop("painel", None)
            return s

    @srv.tool()
    def atualizar_base(completa: bool = False) -> dict:
        """Baixa da ANP o que mudou e atualiza a base, em segundo plano. Use completa=True
        só para baixar tudo de novo (cerca de 630 MB). Depois chame status_base para
        acompanhar o progresso."""
        with _erros_para_o_agente():
            r = svc.iniciar_atualizacao(origem="mcp", completa=completa)
            r["dica"] = "Acompanhe com status_base (campo progresso) até atualizacao_em_andamento ficar falso."
            return r

    @srv.tool()
    def listar_variaveis() -> list[dict]:
        """Variáveis disponíveis, com unidades aceitas e a definição de cada uma."""
        return [{"id": v["id"], "nome": v["nome"], "grupo": v["grupo"], "descricao": v["descricao"],
                 "unidades": [u["id"] for u in v["unidades"]], "unidade_padrao": v["unidade_padrao"]}
                for v in svc.variaveis()]

    @srv.tool()
    def buscar_campos(
        busca: str = "",
        bacia: str | None = None,
        estado: str | None = None,
        operador: str | None = None,
        empresa: str | None = None,
        ambiente: str | None = None,
        somente_ativos: bool = False,
        limite: int = 30,
    ) -> dict:
        """Procura campos por nome (parte do nome, sem acento) e filtros. Cada campo vem com
        bacia, estado, operador, sócios com participação, primeiro e último mês com dado e a
        produção do último mês da base (petróleo em bbl/d, gás em mil m3/d). Sem busca, ordena
        pelos maiores produtores."""
        with _erros_para_o_agente():
            r = svc.campos(busca=busca, bacia=bacia, estado=estado, operador=operador, empresa=empresa,
                           ambiente=ambiente, somente_ativos=somente_ativos, limite=limite)
            for c in r["campos"]:
                c.pop("id", None)
            return r

    @srv.tool()
    def listar_empresas(busca: str = "") -> list[dict]:
        """Empresas com participação em campos (razão social como a ANP publica), com o número
        de campos. Use o nome exato em liquida_de ou em alvos empresa:NOME."""
        return svc.empresas(busca)

    @srv.tool()
    def opcoes_de_filtro() -> dict:
        """Listas de bacias, estados, operadores, empresas, situações e ambientes existentes na base."""
        with _erros_para_o_agente():
            return svc.filtros()

    @srv.tool()
    def listar_clusters() -> list[dict]:
        """Clusters salvos, com a regra, os campos incluídos e excluídos à mão e os campos que
        cada um reúne hoje."""
        with _erros_para_o_agente():
            return [_compactar_cluster(c) for c in svc.clusters_listar()]

    @srv.tool()
    def criar_cluster(
        nome: str,
        campos: list[str] | None = None,
        operadores: list[str] | None = None,
        bacias: list[str] | None = None,
        estados: list[str] | None = None,
        ambientes: list[str] | None = None,
        empresas: list[str] | None = None,
        situacoes: list[str] | None = None,
        somente_ativos: bool = False,
        excluir: list[str] | None = None,
        descricao: str = "",
        cor: str | None = None,
    ) -> dict:
        """Cria um cluster salvo. Combine campos escolhidos (campos) e uma regra automática
        (operadores, bacias, estados, ambientes MAR/TERRA, empresas sócias, situações,
        somente_ativos). Dentro de um critério vale qualquer opção; entre critérios valem
        todos. 'excluir' tira campos que a regra puxaria. cor no formato #RRGGBB."""
        with _erros_para_o_agente():
            regra = {"operadores": operadores or [], "bacias": bacias or [], "estados": estados or [],
                     "ambientes": ambientes or [], "empresas": empresas or [], "situacoes": situacoes or [],
                     "somente_ativos": somente_ativos}
            c = svc.cluster_criar(nome=nome, incluir=campos or [], regra=regra, excluir=excluir or [],
                                  descricao=descricao, cor=cor)
            return _compactar_cluster(c)

    @srv.tool()
    def editar_cluster(
        nome: str,
        novo_nome: str | None = None,
        adicionar: list[str] | None = None,
        remover: list[str] | None = None,
        regra: dict | None = None,
        descricao: str | None = None,
        cor: str | None = None,
    ) -> dict:
        """Altera um cluster: renomeia, adiciona ou remove campos (remover um campo puxado pela
        regra o coloca em 'excluir'), troca a regra inteira (mesmas chaves de criar_cluster) ou
        a descrição e a cor. Só muda o que for informado."""
        with _erros_para_o_agente():
            c = svc.cluster_editar(nome, novo_nome=novo_nome, adicionar=adicionar, remover=remover,
                                   regra=regra, descricao=descricao, cor=cor)
            return _compactar_cluster(c)

    @srv.tool()
    def apagar_cluster(nome: str) -> dict:
        """Apaga um cluster. Uma cópia fica em dados/backups e pode ser reimportada."""
        with _erros_para_o_agente():
            c = svc.cluster_apagar(nome)
            return {"apagado": c["nome"], "backup": str(svc.config.dir_backups)}

    @srv.tool()
    def serie_producao(
        alvos: list[str],
        variavel: str = "petroleo",
        unidade: str | None = None,
        inicio: str | None = None,
        fim: str | None = None,
        quebra: str | None = None,
        liquida_de: list[str] | None = None,
        top_n: int = 8,
    ) -> dict:
        """Série mensal com indicadores (último mês, variação mensal e anual, média de 12 meses,
        pico, acumulados, poços produtores). alvos: lista de "tipo:valor" (vários = comparação).
        Datas em AAAA-MM; sem inicio, traz os últimos 24 meses (os indicadores sempre usam o
        histórico inteiro). quebra: campo, poco ou instalacao (com um alvo só) para ver a
        composição, com as top_n maiores partes mais "Outros". liquida_de: empresas para ver só a
        parte delas. Meses sem dado da ANP voltam como null."""
        with _erros_para_o_agente():
            if not inicio:
                ultimo = ler_mes(svc.status()["ultimo_mes"]) if not fim else ler_mes(fim)
                inicio = mes_iso(somar_meses(ultimo, -23)) if ultimo else None
            r = svc.serie(alvos, variavel, unidade, inicio, fim, quebra, liquida_de, top_n)
            return {
                "variavel": r["variavel"]["nome"], "unidade": r["unidade"]["rotulo"], "meses": r["meses"],
                "series": [{"nome": s["nome"], "tipo": s["tipo"], "valores": s["valores"]} for s in r["series"]],
                "total": r["total"]["valores"] if r["total"] else None,
                "indicadores": {a["nome"]: r["indicadores"].get(a["id"], []) for a in r["alvos"]},
                "alvos": r["alvos"], "lacunas_da_fonte": r["lacunas"],
                "meses_completados_com_producao_por_zona": r["complementados"], "avisos": r["avisos"],
                "periodo": r["periodo"], "ultimo_mes_base": r["ultimo_mes_base"],
                "link_painel": svc.link_painel(alvos, variavel, unidade, inicio, fim, quebra, liquida_de),
            }

    @srv.tool()
    def indicadores(alvo: str, variavel: str = "petroleo", unidade: str | None = None,
                    liquida_de: list[str] | None = None) -> dict:
        """Só os indicadores de um alvo (sem a série): último mês, variações, média de 12 meses,
        pico histórico, acumulado no ano e no histórico."""
        with _erros_para_o_agente():
            ultimo = svc.status()["ultimo_mes"]
            r = svc.serie([alvo], variavel, unidade, ultimo, None, None, liquida_de)
            a = r["alvos"][0]
            return {"alvo": a["nome"], "variavel": r["variavel"]["nome"], "unidade": r["unidade"]["rotulo"],
                    "indicadores": r["indicadores"].get(a["id"], []), "avisos": r["avisos"]}

    @srv.tool()
    def detalhar(alvo: str, quebra: str = "poco", mes: str | None = None,
                 liquida_de: list[str] | None = None, limite: int = 30) -> dict:
        """Tabela de um mês por poço, instalação ou campo (quebra), com petróleo, gás, água, boe,
        corte de água, RGO, variações e injeção. Sem mês, usa o último com dado. Ordenada pelo
        total em boe/d; 'limite' controla quantas linhas voltam."""
        with _erros_para_o_agente():
            return svc.detalhe(alvo, quebra, mes, liquida_de, limite)

    @srv.tool()
    def exportar_planilha(
        alvos: list[str],
        tipo: str = "serie",
        formato: str = "xlsx",
        variavel: str = "petroleo",
        unidade: str | None = None,
        inicio: str | None = None,
        fim: str | None = None,
        quebra: str | None = None,
        mes: str | None = None,
        liquida_de: list[str] | None = None,
        caminho: str | None = None,
    ) -> dict:
        """Gera um arquivo Excel ou CSV e devolve o caminho. tipo 'serie' usa os mesmos
        parâmetros de serie_producao (vários alvos); tipo 'detalhe' usa o primeiro alvo com
        quebra e mes. Sem caminho, grava em dados/exportacoes."""
        with _erros_para_o_agente():
            if tipo == "detalhe":
                params = dict(alvo=alvos[0], quebra=quebra or "poco", mes=mes, liquida_de=liquida_de)
            else:
                params = dict(alvos=alvos, variavel=variavel, unidade=unidade, inicio=inicio, fim=fim,
                              quebra=quebra, liquida_de=liquida_de)
            arquivo = svc.exportar_para_arquivo(tipo, formato, caminho, **params)
            return {"arquivo": str(arquivo)}

    @srv.tool()
    def revisoes_recentes(limite: int = 50, somente_nao_vistas: bool = False) -> dict:
        """Meses já existentes que mudaram de valor quando a ANP republicou os arquivos, com
        valores antes e depois (petróleo e água em bbl/d, gás em mil m3/d)."""
        with _erros_para_o_agente():
            return svc.revisoes(None, somente_nao_vistas, limite)

    @srv.tool()
    def link_painel(alvos: list[str], variavel: str = "petroleo", unidade: str | None = None,
                    inicio: str | None = None, fim: str | None = None, quebra: str | None = None,
                    liquida_de: list[str] | None = None) -> dict:
        """Link para abrir a mesma visão no site local (precisa do site rodando: anp servir)."""
        with _erros_para_o_agente():
            return {"url": svc.link_painel(alvos, variavel, unidade, inicio, fim, quebra, liquida_de)}

    return srv


def main(config: Config | None = None) -> None:
    criar_servidor(config).run("stdio")
