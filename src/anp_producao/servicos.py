"""Camada única de operações, usada pelo site, pela linha de comando e pelo MCP.

Cada chamada abre e fecha as conexões, então pode ser usada de qualquer
processo ou thread sem estado compartilhado.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from anp_producao import atualizacao, cadastro, clusters, consultas, exportacao, qualidade
from anp_producao.config import Config, carregar_config
from anp_producao.db import conectar_anp, conectar_usuario, ler_meta, transacao
from anp_producao.formatos import ler_mes, mes_iso, rotulo_mes, somar_meses
from anp_producao.variaveis import catalogo

ErroUsuario = (consultas.ErroConsulta, clusters.ErroCluster, ValueError)
NaoEncontrado = (consultas.NaoEncontrado, clusters.ClusterNaoEncontrado)


class Servicos:
    def __init__(self, config: Config | None = None):
        self.config = config or carregar_config()
        self.config.garantir_pastas()

    @contextmanager
    def _contexto(self):
        con = conectar_anp(self.config)
        con_usr = conectar_usuario(self.config)
        try:
            yield consultas.Contexto(con, con_usr)
        finally:
            con.close()
            con_usr.close()

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with self._contexto() as ctx:
            con = ctx.con
            ultimo = ctx.ultimo_mes
            em_andamento = atualizacao.atualizacao_em_andamento(self.config)
            linha_exec = con.execute(
                "SELECT * FROM atualizacoes WHERE status = 'executando' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            ultima = con.execute(
                "SELECT * FROM atualizacoes WHERE status != 'executando' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            lac = ctx.lacunas
            desde = somar_meses(ultimo, -24) if ultimo else None
            n_campos = con.execute("SELECT COUNT(*) FROM campos").fetchone()[0]
            n_pocos = con.execute("SELECT COUNT(*) FROM pocos").fetchone()[0]
            nao_vistas = con.execute("SELECT COUNT(*) FROM revisoes WHERE vista = 0").fetchone()[0]
            return {
                "base_vazia": ultimo is None,
                "ultimo_mes": mes_iso(ultimo),
                "ultimo_mes_rotulo": rotulo_mes(ultimo) if ultimo else None,
                "pagina_anp_atualizada_em": ler_meta(con, "pagina_atualizada_em"),
                "proxima_publicacao_estimada": (
                    f"início de {rotulo_mes(somar_meses(ultimo, 3))} (dados de {rotulo_mes(somar_meses(ultimo, 1))})"
                    if ultimo else None
                ),
                "atualizacao_em_andamento": em_andamento,
                "progresso": dict(linha_exec) if (linha_exec and em_andamento) else None,
                "ultima_atualizacao": dict(ultima) if ultima else None,
                "revisoes_nao_vistas": nao_vistas,
                "lacunas_recentes": [
                    {**l, "mes": mes_iso(l["mes"]), "rotulo": rotulo_mes(l["mes"])}
                    for l in qualidade.resumo_lacunas(lac, desde)
                ],
                "complementos_recentes": [
                    {**c, "mes": mes_iso(c["mes"]), "rotulo": rotulo_mes(c["mes"])}
                    for c in qualidade.resumo_complementos(ctx.complementos, desde)
                ],
                "totais": {"campos": n_campos, "pocos": n_pocos},
                "painel": self.config.url_painel,
            }

    def historico_atualizacoes(self, limite: int = 20) -> list[dict]:
        con = conectar_anp(self.config)
        try:
            return [dict(r) for r in con.execute(
                "SELECT * FROM atualizacoes ORDER BY id DESC LIMIT ?", (limite,)
            )]
        finally:
            con.close()

    def iniciar_atualizacao(self, origem: str = "web", completa: bool = False) -> dict:
        return atualizacao.iniciar_em_segundo_plano(self.config, origem=origem, completa=completa)

    def atualizar_agora(self, origem: str = "cli", completa: bool = False, ao_progresso=None) -> dict:
        return atualizacao.atualizar(self.config, origem=origem, completa=completa, ao_progresso=ao_progresso)

    # --------------------------------------------------------------- catálogo
    def variaveis(self) -> list[dict]:
        return catalogo()

    def campos(self, **filtros) -> dict:
        with self._contexto() as ctx:
            return consultas.buscar_campos(ctx, **filtros)

    def filtros(self) -> dict:
        with self._contexto() as ctx:
            return consultas.opcoes_filtro(ctx)

    def empresas(self, busca: str = "") -> list[dict]:
        from anp_producao.leitor_csv import chave_nome

        con = conectar_anp(self.config)
        try:
            itens = cadastro.empresas(con)
        finally:
            con.close()
        termo = chave_nome(busca)
        return [e for e in itens if termo in chave_nome(e["empresa"])] if termo else itens

    def instalacoes(self, busca: str = "", limite: int = 50) -> list[dict]:
        con = conectar_anp(self.config)
        try:
            ultimo = con.execute("SELECT MAX(mes) FROM producao_instalacao_mes").fetchone()[0]
            linhas = con.execute(
                "SELECT i.nome, COUNT(DISTINCT p.campo_id) AS campos, "
                "SUM(COALESCE(p.oleo,0)+COALESCE(p.condensado,0)) AS petroleo "
                "FROM instalacoes i LEFT JOIN producao_instalacao_mes p ON p.instalacao_id = i.id AND p.mes = ? "
                "WHERE i.nome LIKE ? GROUP BY i.id ORDER BY petroleo DESC NULLS LAST, i.nome LIMIT ?",
                (ultimo, f"%{busca}%", limite),
            ).fetchall()
            return [{"nome": r["nome"], "campos": r["campos"]} for r in linhas]
        finally:
            con.close()

    def pocos(self, busca: str, limite: int = 30) -> list[str]:
        con = conectar_anp(self.config)
        try:
            return [r[0] for r in con.execute(
                "SELECT nome FROM pocos WHERE nome LIKE ? ORDER BY nome LIMIT ?", (f"%{busca}%", limite)
            )]
        finally:
            con.close()

    # --------------------------------------------------------------- clusters
    def clusters_listar(self, com_campos: bool = True) -> list[dict]:
        with self._contexto() as ctx:
            itens = clusters.listar(ctx.con_usr)
            if com_campos:
                for c in itens:
                    ids, faltam = clusters.resolver(c, ctx.info, ctx.ultimo_mes)
                    c["n_campos"] = len(ids)
                    c["campos_resolvidos"] = sorted(ctx.info[i].nome for i in ids)
                    c["nao_encontrados"] = faltam
            return itens

    def cluster_obter(self, ref: str | int) -> dict:
        with self._contexto() as ctx:
            c = clusters.obter(ctx.con_usr, ref)
            ids, faltam = clusters.resolver(c, ctx.info, ctx.ultimo_mes)
            c["n_campos"] = len(ids)
            c["campos_resolvidos"] = sorted(ctx.info[i].nome for i in ids)
            c["nao_encontrados"] = faltam
            return c

    def cluster_previa(self, regra: dict | None = None, incluir=None, excluir=None) -> dict:
        """Campos que um cluster teria, sem salvar (usado no editor)."""
        with self._contexto() as ctx:
            provisorio = {"incluir": clusters._lista_campos(incluir), "excluir": clusters._lista_campos(excluir),
                          "regra": clusters.Regra.de_dict(regra).como_dict()}
            ids, faltam = clusters.resolver(provisorio, ctx.info, ctx.ultimo_mes)
            return {"n_campos": len(ids), "campos": sorted(ctx.info[i].nome for i in ids), "nao_encontrados": faltam}

    def _nomes_oficiais(self, dados: dict) -> dict:
        """Troca nomes digitados ('alfa') pelo nome como a ANP escreve ('ALFA')."""
        from anp_producao.leitor_csv import chave_nome

        chaves = ("incluir", "excluir", "adicionar", "remover")
        if not any(dados.get(k) for k in chaves):
            return dados
        con = conectar_anp(self.config)
        try:
            oficiais = {chave_nome(n): n for (n,) in con.execute("SELECT nome FROM campos")}
        finally:
            con.close()
        resultado = dict(dados)
        for k in chaves:
            valor = dados.get(k)
            if valor:
                lista = valor.split(",") if isinstance(valor, str) else list(valor)
                resultado[k] = [oficiais.get(chave_nome(n), n.strip()) for n in lista if n and n.strip()]
        return resultado

    def cluster_criar(self, **dados) -> dict:
        dados = self._nomes_oficiais(dados)
        con = conectar_usuario(self.config)
        try:
            c = clusters.criar(con, dir_dados=self.config.dir_dados, **dados)
        finally:
            con.close()
        return self.cluster_obter(c["id"])

    def cluster_editar(self, ref: str | int, **mudancas) -> dict:
        mudancas = self._nomes_oficiais(mudancas)
        con = conectar_usuario(self.config)
        try:
            c = clusters.editar(con, ref, dir_dados=self.config.dir_dados, **mudancas)
        finally:
            con.close()
        return self.cluster_obter(c["id"])

    def cluster_apagar(self, ref: str | int) -> dict:
        con = conectar_usuario(self.config)
        try:
            return clusters.apagar(con, ref, dir_dados=self.config.dir_dados)
        finally:
            con.close()

    def clusters_exportar(self) -> dict:
        con = conectar_usuario(self.config)
        try:
            return clusters.exportar(con)
        finally:
            con.close()

    def clusters_importar(self, dados: dict, substituir: bool = False) -> dict:
        con = conectar_usuario(self.config)
        try:
            return clusters.importar(con, dados, substituir=substituir, dir_dados=self.config.dir_dados)
        finally:
            con.close()

    # ----------------------------------------------------------------- séries
    def serie(
        self,
        alvos: list[str],
        variavel: str | None = None,
        unidade: str | None = None,
        inicio: str | int | None = None,
        fim: str | int | None = None,
        quebra: str | None = None,
        liquida_de: list[str] | None = None,
        top_n: int = 10,
    ) -> dict:
        with self._contexto() as ctx:
            return consultas.serie(
                ctx, alvos, variavel=variavel, unidade=unidade, inicio=ler_mes(inicio), fim=ler_mes(fim),
                quebra=quebra or None, liquida_de=[e for e in (liquida_de or []) if e], top_n=top_n,
            )

    def detalhe(
        self,
        alvo: str,
        quebra: str = "poco",
        mes: str | int | None = None,
        liquida_de: list[str] | None = None,
        limite: int | None = None,
    ) -> dict:
        with self._contexto() as ctx:
            return consultas.detalhe(ctx, alvo, quebra=quebra, mes=ler_mes(mes),
                                     liquida_de=[e for e in (liquida_de or []) if e], limite=limite)

    def exportar(self, tipo: str, formato: str, padrao_csv: str = "br", **parametros) -> tuple[bytes, str, str]:
        """Devolve (conteúdo, nome do arquivo, tipo MIME)."""
        formato = (formato or "xlsx").lower()
        if formato not in ("csv", "xlsx"):
            raise ValueError("Formato deve ser csv ou xlsx")
        if tipo == "serie":
            resultado = self.serie(**parametros)
            conteudo = exportacao.serie_xlsx(resultado) if formato == "xlsx" else exportacao.serie_csv(resultado, padrao_csv)
        elif tipo == "detalhe":
            resultado = self.detalhe(**parametros)
            conteudo = exportacao.detalhe_xlsx(resultado) if formato == "xlsx" else exportacao.detalhe_csv(resultado, padrao_csv)
        else:
            raise ValueError("Tipo de exportação deve ser serie ou detalhe")
        nome = exportacao.nome_arquivo(resultado, tipo, formato)
        mime = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if formato == "xlsx" else "text/csv; charset=utf-8"
        )
        return conteudo, nome, mime

    def exportar_para_arquivo(self, tipo: str, formato: str, destino: str | None = None, **parametros) -> Path:
        conteudo, nome, _ = self.exportar(tipo, formato, **parametros)
        caminho = Path(destino).expanduser() if destino else self.config.dir_exportacoes / nome
        if caminho.is_dir():
            caminho = caminho / nome
        pasta_exportacoes = self.config.dir_exportacoes.resolve()
        if caminho.exists() and pasta_exportacoes not in caminho.resolve().parents:
            raise ValueError(f"Já existe um arquivo em {caminho}; escolha outro nome")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)
        return caminho.resolve()

    # ---------------------------------------------------------------- revisões
    def revisoes(self, atualizacao_id: int | None = None, somente_nao_vistas: bool = False, limite: int = 500) -> dict:
        with self._contexto() as ctx:
            return consultas.revisoes(ctx, atualizacao_id, somente_nao_vistas, limite)

    def marcar_revisoes_vistas(self) -> int:
        con = conectar_anp(self.config)
        try:
            with transacao(con):
                return con.execute("UPDATE revisoes SET vista = 1 WHERE vista = 0").rowcount
        finally:
            con.close()

    # ------------------------------------------------------------------ links
    def link_painel(self, alvos: list[str], variavel: str | None = None, unidade: str | None = None,
                    inicio: str | None = None, fim: str | None = None, quebra: str | None = None,
                    liquida_de: list[str] | None = None) -> str:
        # Mesmo formato que a página usa: cada alvo codificado sozinho, vírgula separa alvos.
        partes = ["alvos=" + ",".join(quote(a, safe=":") for a in alvos)]
        ini, fim_ = mes_iso(ler_mes(inicio)), mes_iso(ler_mes(fim))
        for chave, valor in (("var", variavel), ("un", unidade),
                             ("per", "personalizado" if (ini or fim_) else None),
                             ("ini", ini), ("fim", fim_), ("visao", quebra),
                             ("liq", (liquida_de or [None])[0])):
            if valor:
                partes.append(f"{chave}={quote(str(valor), safe='')}")
        return self.config.url_painel + "#" + "&".join(partes)


def resposta_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
