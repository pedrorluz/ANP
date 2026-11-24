"""Servidor web local: a página e a API JSON (documentação em /docs)."""

from __future__ import annotations

import json
import logging
import socket
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from anp_producao import __version__
from anp_producao.atualizacao import AtualizacaoEmAndamento
from anp_producao.config import Config, carregar_config
from anp_producao.servicos import ErroUsuario, NaoEncontrado, Servicos

log = logging.getLogger(__name__)
PASTA_ESTATICA = Path(__file__).parent / "static"
CABECALHO_SEGURANCA = "X-ANP"


class Regra(BaseModel):
    operadores: list[str] = Field(default_factory=list)
    bacias: list[str] = Field(default_factory=list)
    estados: list[str] = Field(default_factory=list)
    ambientes: list[str] = Field(default_factory=list)
    empresas: list[str] = Field(default_factory=list)
    situacoes: list[str] = Field(default_factory=list)
    somente_ativos: bool = False


class ClusterNovo(BaseModel):
    nome: str
    descricao: str = ""
    cor: str | None = None
    regra: Regra | None = None
    incluir: list[str] = Field(default_factory=list)
    excluir: list[str] = Field(default_factory=list)


class ClusterEdicao(BaseModel):
    novo_nome: str | None = None
    descricao: str | None = None
    cor: str | None = None
    regra: Regra | None = None
    incluir: list[str] | None = None
    excluir: list[str] | None = None
    adicionar: list[str] | None = None
    remover: list[str] | None = None


class PreviaCluster(BaseModel):
    regra: Regra | None = None
    incluir: list[str] = Field(default_factory=list)
    excluir: list[str] = Field(default_factory=list)


class PedidoAtualizacao(BaseModel):
    completa: bool = False


def _anexo(conteudo: bytes, nome: str, mime: str) -> Response:
    return Response(
        content=conteudo,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
    )


def criar_app(config: Config | None = None) -> FastAPI:
    config = config or carregar_config()
    svc = Servicos(config)
    app = FastAPI(
        title="Produção ANP por campo",
        version=__version__,
        description=(
            "API local do site de produção de petróleo, gás e água por campo (dados abertos da ANP). "
            "Volumes da ANP em m³ (gás em mil m³); séries em médias diárias por padrão. "
            "Requisições que alteram dados exigem o cabeçalho X-ANP: 1."
        ),
    )
    if config.host in ("127.0.0.1", "localhost"):
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def protecao_csrf(request: Request, call_next):
        # Bloqueia formulários de outros sites tentando mexer na base local.
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
            if request.headers.get(CABECALHO_SEGURANCA) != "1":
                return JSONResponse({"erro": f"Cabeçalho {CABECALHO_SEGURANCA}: 1 obrigatório"}, status_code=403)
        return await call_next(request)

    def _resposta(codigo: int):
        async def tratar(request: Request, exc: Exception):
            return JSONResponse({"erro": str(exc)}, status_code=codigo)
        return tratar

    for classe in NaoEncontrado:
        app.add_exception_handler(classe, _resposta(404))
    for classe in ErroUsuario:
        app.add_exception_handler(classe, _resposta(400))
    app.add_exception_handler(AtualizacaoEmAndamento, _resposta(409))

    # ------------------------------------------------------------ estado da base
    @app.get("/api/status", summary="Último mês na base, andamento da atualização e lacunas da fonte")
    def status():
        return svc.status()

    @app.post("/api/atualizar", summary="Dispara a atualização da base em segundo plano")
    def atualizar(pedido: PedidoAtualizacao = Body(default=PedidoAtualizacao())):
        r = svc.iniciar_atualizacao(origem="web", completa=pedido.completa)
        if not r["iniciada"]:
            return JSONResponse(r, status_code=409)
        return r

    @app.get("/api/atualizacoes", summary="Histórico de atualizações")
    def atualizacoes(limite: int = 20):
        return svc.historico_atualizacoes(limite)

    @app.get("/api/revisoes", summary="Meses anteriores revisados pela ANP")
    def revisoes(atualizacao_id: int | None = None, somente_nao_vistas: bool = False, limite: int = 500):
        return svc.revisoes(atualizacao_id, somente_nao_vistas, limite)

    @app.post("/api/revisoes/vistas", summary="Marca as revisões como vistas")
    def revisoes_vistas():
        return {"marcadas": svc.marcar_revisoes_vistas()}

    # ------------------------------------------------------------------ catálogo
    @app.get("/api/variaveis", summary="Variáveis do seletor e unidades disponíveis")
    def variaveis():
        return svc.variaveis()

    @app.get("/api/campos", summary="Busca de campos com filtros")
    def campos(busca: str = "", bacia: str | None = None, estado: str | None = None,
               operador: str | None = None, empresa: str | None = None, ambiente: str | None = None,
               situacao: str | None = None, somente_ativos: bool = False, limite: int = 50):
        return svc.campos(busca=busca, bacia=bacia, estado=estado, operador=operador, empresa=empresa,
                          ambiente=ambiente, situacao=situacao, somente_ativos=somente_ativos, limite=limite)

    @app.get("/api/filtros", summary="Opções de bacia, estado, operador, empresa e situação")
    def filtros():
        return svc.filtros()

    @app.get("/api/empresas", summary="Empresas com participação em campos")
    def empresas(busca: str = ""):
        return svc.empresas(busca)

    @app.get("/api/instalacoes", summary="Instalações (plataformas, FPSOs, estações)")
    def instalacoes(busca: str = "", limite: int = 50):
        return svc.instalacoes(busca, limite)

    @app.get("/api/pocos", summary="Busca de poços pelo nome")
    def pocos(busca: str, limite: int = 30):
        return svc.pocos(busca, limite)

    # ------------------------------------------------------------------ clusters
    @app.get("/api/clusters", summary="Clusters salvos, com os campos que cada um puxa hoje")
    def clusters_listar():
        return svc.clusters_listar()

    @app.post("/api/clusters", summary="Cria um cluster", status_code=201)
    def clusters_criar(dados: ClusterNovo):
        return svc.cluster_criar(
            nome=dados.nome, descricao=dados.descricao, cor=dados.cor,
            regra=dados.regra.model_dump() if dados.regra else None,
            incluir=dados.incluir, excluir=dados.excluir,
        )

    @app.post("/api/clusters/previa", summary="Campos que um cluster teria, sem salvar")
    def clusters_previa(dados: PreviaCluster):
        return svc.cluster_previa(regra=dados.regra.model_dump() if dados.regra else None,
                                  incluir=dados.incluir, excluir=dados.excluir)

    @app.get("/api/clusters/exportar", summary="Baixa todos os clusters em JSON")
    def clusters_exportar():
        conteudo = json.dumps(svc.clusters_exportar(), ensure_ascii=False, indent=2).encode("utf-8")
        return _anexo(conteudo, "clusters-anp.json", "application/json")

    @app.post("/api/clusters/importar", summary="Importa clusters de um JSON exportado")
    def clusters_importar(dados: dict = Body(...), substituir: bool = False):
        return svc.clusters_importar(dados, substituir=substituir)

    @app.get("/api/clusters/{ref}", summary="Um cluster (por nome ou id)")
    def clusters_obter(ref: str):
        return svc.cluster_obter(ref)

    @app.put("/api/clusters/{ref}", summary="Altera um cluster (só os campos enviados)")
    def clusters_editar(ref: str, dados: ClusterEdicao):
        mudancas = dados.model_dump(exclude_unset=True)
        if dados.regra is not None:
            mudancas["regra"] = dados.regra.model_dump()
        return svc.cluster_editar(ref, **mudancas)

    @app.delete("/api/clusters/{ref}", summary="Apaga um cluster (fica no backup)")
    def clusters_apagar(ref: str):
        return svc.cluster_apagar(ref)

    # -------------------------------------------------------------------- séries
    @app.get("/api/serie", summary="Série mensal de um ou mais alvos, com indicadores")
    def serie(alvo: list[str] = Query(..., description="Repita para comparar: cluster:X, campo:Y, bacia:Z..."),
              variavel: str = "petroleo", unidade: str | None = None, inicio: str | None = None,
              fim: str | None = None, quebra: str | None = None,
              liquida_de: list[str] = Query(default_factory=list), top_n: int = 10):
        return svc.serie(alvo, variavel, unidade, inicio, fim, quebra, liquida_de, top_n)

    @app.get("/api/detalhe", summary="Tabela de um mês por poço, instalação ou campo")
    def detalhe(alvo: str, quebra: str = "poco", mes: str | None = None,
                liquida_de: list[str] = Query(default_factory=list), limite: int | None = None):
        return svc.detalhe(alvo, quebra, mes, liquida_de, limite)

    @app.get("/api/exportar", summary="Baixa a série ou o detalhe em CSV ou Excel")
    def exportar(tipo: str = "serie", formato: str = "xlsx", padrao_csv: str = "br",
                 alvo: list[str] = Query(...), variavel: str = "petroleo", unidade: str | None = None,
                 inicio: str | None = None, fim: str | None = None, quebra: str | None = None,
                 mes: str | None = None, liquida_de: list[str] = Query(default_factory=list),
                 top_n: int = 10):
        if tipo == "detalhe":
            params = dict(alvo=alvo[0], quebra=quebra or "poco", mes=mes, liquida_de=liquida_de)
        else:
            params = dict(alvos=alvo, variavel=variavel, unidade=unidade, inicio=inicio, fim=fim,
                          quebra=quebra, liquida_de=liquida_de, top_n=top_n)
        conteudo, nome, mime = svc.exportar(tipo, formato, padrao_csv=padrao_csv, **params)
        return _anexo(conteudo, nome, mime)

    # -------------------------------------------------------------------- página
    if PASTA_ESTATICA.exists():
        app.mount("/static", StaticFiles(directory=PASTA_ESTATICA), name="static")

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(PASTA_ESTATICA / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def porta_ocupada(host: str, porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, porta)) == 0


def servir(config: Config | None = None, abrir_navegador: bool = True) -> None:
    import uvicorn

    config = config or carregar_config()
    url = config.url_painel
    if porta_ocupada(config.host, config.porta):
        print(f"A porta {config.porta} já está em uso. Se for este site, ele já está aberto em {url}")
        if abrir_navegador:
            webbrowser.open(url)
        return
    if abrir_navegador:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Produção ANP rodando em {url}  (Ctrl+C para parar)")
    uvicorn.run(criar_app(config), host=config.host, port=config.porta, log_level="warning")
