"""Linha de comando: `anp <comando>`. Use --json em qualquer consulta para saída de máquina.

Exemplos:
    anp servir                         abre o site em http://127.0.0.1:8765
    anp atualizar                      baixa da ANP o que mudou
    anp status --json
    anp campos --busca buzios
    anp serie campo:BÚZIOS campo:TUPI --variavel gas --inicio 2024-01
    anp serie cluster:"Polo Bahia" --quebra campo --json
    anp detalhe campo:MERO --quebra poco
    anp clusters criar "Pré-sal Santos" --bacia Santos --ativos
    anp exportar serie bacia:Santos --formato xlsx
    anp mcp                            servidor MCP para agentes (stdio)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from anp_producao import __version__
from anp_producao.atualizacao import AtualizacaoEmAndamento
from anp_producao.config import carregar_config
from anp_producao.formatos import numero_br, percentual_br, rotulo_mes
from anp_producao.servicos import ErroUsuario, NaoEncontrado, Servicos, resposta_json


def _imprimir_json(obj) -> None:
    print(resposta_json(obj))


def _tabela(cabecalho: list[str], linhas: list[list], alinhar_esquerda: int = 1) -> str:
    textos = [[("" if v is None else str(v)) for v in l] for l in [cabecalho, *linhas]]
    larguras = [max(len(l[i]) for l in textos) for i in range(len(cabecalho))]
    saida = []
    for n, l in enumerate(textos):
        partes = [c.ljust(larguras[i]) if i < alinhar_esquerda else c.rjust(larguras[i]) for i, c in enumerate(l)]
        saida.append("  ".join(partes))
        if n == 0:
            saida.append("  ".join("-" * w for w in larguras))
    return "\n".join(saida)


def _texto_kpi(k: dict, casas: int) -> str:
    if k.get("valor") is None:
        return "n/d"
    if k.get("tipo") == "percentual":
        return percentual_br(k["valor"])
    if k.get("tipo") == "pontos":
        return f"{numero_br(k['valor'], 1)} p.p."
    c = 2 if k["id"].startswith("acumulado") else (0 if k["id"] == "produtores" else casas)
    return f"{numero_br(k['valor'], c)} {k.get('unidade', '')}".strip()


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #

def cmd_servir(svc: Servicos, a) -> int:
    from anp_producao.api import servir

    servir(svc.config, abrir_navegador=not a.sem_navegador)
    return 0


def cmd_atualizar(svc: Servicos, a) -> int:
    def progresso(etapa, pct, msg):
        if not a.json:
            print(f"[{pct:5.1f}%] {msg}", file=sys.stderr, flush=True)

    resumo = svc.atualizar_agora(origem=a.origem, completa=a.completa, ao_progresso=progresso)
    if a.json:
        _imprimir_json(resumo)
    else:
        print(resumo["mensagem"])
        for aviso in resumo["avisos"][:20]:
            print(f"  aviso: {aviso}")
        for erro in resumo["erros"]:
            print(f"  erro: {erro}")
    return 0 if resumo["status"] != "erro" else 1


def cmd_reprocessar(svc: Servicos, a) -> int:
    from anp_producao.atualizacao import reprocessar_brutos

    r = reprocessar_brutos(svc.config, ao_progresso=lambda e, p, m: print(f"[{p:5.1f}%] {m}", file=sys.stderr))
    _imprimir_json(r) if a.json else print(f"{r['arquivos']} arquivo(s) regravados do cache local")
    return 0


def cmd_status(svc: Servicos, a) -> int:
    s = svc.status()
    if a.json:
        _imprimir_json(s)
        return 0
    if s["base_vazia"]:
        print("Base vazia. Rode 'anp atualizar' para baixar os dados da ANP.")
        return 0
    u = s["ultima_atualizacao"] or {}
    print(f"Dados até {s['ultimo_mes_rotulo']} ({s['totais']['campos']} campos, {s['totais']['pocos']} poços)")
    print(f"Última atualização: {u.get('concluida_em', 'nunca')} ({u.get('status', '')}) {u.get('mensagem', '')}")
    print(f"Página da ANP atualizada em: {s['pagina_anp_atualizada_em']}")
    print(f"Próxima publicação estimada: {s['proxima_publicacao_estimada']}")
    if s["atualizacao_em_andamento"]:
        p = s["progresso"] or {}
        print(f"Atualização em andamento: {p.get('progresso', 0)}% {p.get('mensagem', '')}")
    if s["revisoes_nao_vistas"]:
        print(f"Revisões da ANP não vistas: {s['revisoes_nao_vistas']} (anp revisoes)")
    if s["lacunas_recentes"]:
        print("Meses incompletos na fonte: " + ", ".join(
            f"{l['rotulo']} ({l['ambiente'].lower()}, {l['tipo']})" for l in s["lacunas_recentes"]))
    if s.get("complementos_recentes"):
        print("Completados com o arquivo de produção por zona: " + ", ".join(
            f"{c['rotulo']} ({c['ambiente'].lower()}, {c['pocos']} poços)" for c in s["complementos_recentes"]))
    return 0


def cmd_variaveis(svc: Servicos, a) -> int:
    vs = svc.variaveis()
    if a.json:
        _imprimir_json(vs)
        return 0
    print(_tabela(["id", "nome", "unidades"], [[v["id"], v["nome"], ", ".join(u["id"] for u in v["unidades"])] for v in vs], 2))
    return 0


def cmd_campos(svc: Servicos, a) -> int:
    r = svc.campos(busca=a.busca or "", bacia=a.bacia, estado=a.estado, operador=a.operador, empresa=a.empresa,
                   ambiente=a.ambiente, somente_ativos=a.ativos, limite=a.limite)
    if a.json:
        _imprimir_json(r)
        return 0
    print(f"{r['total']} campos (produção de {rotulo_mes(int(r['mes_referencia'].replace('-', '')))})"
          if r["mes_referencia"] else f"{r['total']} campos")
    print(_tabela(["campo", "bacia", "operador", "petróleo bbl/d", "gás mil m³/d"],
                  [[c["nome"], c["bacia"] or "", ", ".join(c["operadores"]), numero_br(c["petroleo_bbl_d"]),
                    numero_br(c["gas_mil_m3_d"], 1)] for c in r["campos"]], 3))
    return 0


def cmd_empresas(svc: Servicos, a) -> int:
    r = svc.empresas(a.busca or "")
    if a.json:
        _imprimir_json(r)
        return 0
    print(_tabela(["empresa", "campos"], [[e["empresa"], e["campos"]] for e in r]))
    return 0


def _regra_dos_argumentos(a) -> dict | None:
    regra = {"operadores": a.operador or [], "bacias": a.bacia or [], "estados": a.estado or [],
             "ambientes": a.ambiente or [], "empresas": a.empresa or [], "situacoes": a.situacao or [],
             "somente_ativos": bool(a.ativos)}
    return regra if any(regra.values()) else None


def cmd_clusters(svc: Servicos, a) -> int:
    acao = a.acao
    if acao == "listar":
        itens = svc.clusters_listar()
        if a.json:
            _imprimir_json(itens)
        else:
            print(_tabela(["cluster", "campos", "regra"], [
                [c["nome"], c["n_campos"], "; ".join(f"{k}={','.join(v) if isinstance(v, list) else v}"
                                                      for k, v in c["regra"].items() if v)] for c in itens]))
        return 0
    if acao == "mostrar":
        c = svc.cluster_obter(a.nome)
        if a.json:
            _imprimir_json(c)
        else:
            print(f"{c['nome']}: {c['n_campos']} campos")
            print(", ".join(c["campos_resolvidos"]))
        return 0
    if acao == "criar":
        c = svc.cluster_criar(nome=a.nome, incluir=a.campos or [], regra=_regra_dos_argumentos(a),
                              excluir=a.excluir or [], descricao=a.descricao or "", cor=a.cor)
    elif acao == "editar":
        regra = _regra_dos_argumentos(a)
        c = svc.cluster_editar(a.nome, novo_nome=a.renomear, adicionar=a.adicionar, remover=a.remover,
                               regra=regra, descricao=a.descricao, cor=a.cor)
    elif acao == "apagar":
        c = svc.cluster_apagar(a.nome)
        print(f"Cluster {c['nome']} apagado (cópia em {svc.config.dir_backups})")
        return 0
    elif acao == "exportar":
        dados = svc.clusters_exportar()
        destino = Path(a.saida).expanduser() if a.saida else None
        if destino:
            destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Clusters exportados para {destino}")
        else:
            _imprimir_json(dados)
        return 0
    elif acao == "importar":
        if not a.nome:
            raise ValueError("Informe o arquivo JSON: anp clusters importar clusters.json")
        dados = json.loads(Path(a.nome).expanduser().read_text(encoding="utf-8"))
        r = svc.clusters_importar(dados, substituir=a.substituir)
        _imprimir_json(r) if a.json else print(f"{r['criados']} criados, {r['atualizados']} atualizados, {len(r['erros'])} erros")
        return 0 if not r["erros"] else 1
    else:  # pragma: no cover - argparse impede
        return 2
    if a.json:
        _imprimir_json(c)
    else:
        print(f"Cluster {c['nome']} salvo com {c['n_campos']} campos: {', '.join(c['campos_resolvidos'][:30])}")
        if c.get("nao_encontrados"):
            print(f"  sem dados na base: {', '.join(c['nao_encontrados'])}")
    return 0


def cmd_serie(svc: Servicos, a) -> int:
    r = svc.serie(a.alvos, a.variavel, a.unidade, a.inicio, a.fim, a.quebra, a.liquida_de, a.top)
    if a.json:
        _imprimir_json(r)
        return 0
    series = list(r["series"]) + ([{**r["total"], "nome": "Total"}] if r["total"] else [])
    casas = r["unidade"]["casas"]
    if a.csv:
        print(";".join(["mes"] + [s["nome"] for s in series]))
        for i, m in enumerate(r["meses"]):
            print(";".join([m] + ["" if s["valores"][i] is None else numero_br(s["valores"][i], casas + 2).replace(".", "") for s in series]))
        return 0
    print(f"{r['variavel']['nome']} ({r['unidade']['rotulo']})")
    linhas = list(zip(r["meses"], *[s["valores"] for s in series]))[-a.linhas:]
    print(_tabela(["mês"] + [s["nome"] for s in series],
                  [[rotulo_mes(int(m.replace("-", "")))] + [numero_br(v, casas) for v in vals] for m, *vals in linhas]))
    for alvo in r["alvos"]:
        kpis = r["indicadores"].get(alvo["id"], [])
        if kpis:
            print(f"\n{alvo['nome']}")
            for k in kpis:
                print(f"  {k['rotulo']}: {_texto_kpi(k, casas)}  ({k.get('detalhe', '')})")
    for aviso in r["avisos"]:
        print(f"\naviso: {aviso}")
    return 0


def cmd_detalhe(svc: Servicos, a) -> int:
    d = svc.detalhe(a.alvo, a.quebra, a.mes, a.liquida_de, a.limite)
    if a.json:
        _imprimir_json(d)
        return 0
    print(f"{d['alvo']['nome']} por {d['quebra']}, {d['rotulo_mes']} ({d['total_linhas']} itens)")
    print(_tabela(["nome", "situação", "petróleo bbl/d", "gás mil m³/d", "água bbl/d", "boe/d", "corte %", "var. mensal"],
                  [[l["nome"], l["situacao"], numero_br(l["petroleo_bbl_d"]), numero_br(l["gas_mil_m3_d"], 1),
                    numero_br(l["agua_bbl_d"]), numero_br(l["boe_d"]),
                    numero_br(l["corte_agua_pct"], 1) if l["corte_agua_pct"] is not None else "n/d",
                    percentual_br(l["var_mensal_petroleo"]) if l["var_mensal_petroleo"] is not None else "n/d"]
                   for l in d["linhas"]], 2))
    for aviso in d["avisos"]:
        print(f"aviso: {aviso}")
    return 0


def cmd_exportar(svc: Servicos, a) -> int:
    if a.tipo == "detalhe":
        params = dict(alvo=a.alvos[0], quebra=a.quebra or "poco", mes=a.mes, liquida_de=a.liquida_de)
    else:
        params = dict(alvos=a.alvos, variavel=a.variavel, unidade=a.unidade, inicio=a.inicio, fim=a.fim,
                      quebra=a.quebra, liquida_de=a.liquida_de)
    caminho = svc.exportar_para_arquivo(a.tipo, a.formato, a.saida, **params)
    _imprimir_json({"arquivo": str(caminho)}) if a.json else print(caminho)
    return 0


def cmd_revisoes(svc: Servicos, a) -> int:
    r = svc.revisoes(None, a.nao_vistas, a.limite)
    if a.json:
        _imprimir_json(r)
        return 0
    if not r["itens"]:
        print("Nenhuma revisão registrada.")
        return 0
    print(_tabela(["campo", "mês", "variável", "antes", "depois", "variação"],
                  [[i["campo"], i["rotulo_mes"], f"{i['variavel']} ({i['unidade']})", numero_br(i["antes"], 1),
                    numero_br(i["depois"], 1), percentual_br(i["variacao"]) if i["variacao"] is not None else "n/d"]
                   for i in r["itens"]], 3))
    return 0


def cmd_link(svc: Servicos, a) -> int:
    print(svc.link_painel(a.alvos, a.variavel, a.unidade, a.inicio, a.fim, a.quebra, a.liquida_de))
    return 0


def cmd_mcp(svc: Servicos, a) -> int:
    from anp_producao.mcp_servidor import main

    main(svc.config)
    return 0


# --------------------------------------------------------------------------- #
# Argumentos
# --------------------------------------------------------------------------- #

def _opcoes_serie(p: argparse.ArgumentParser) -> None:
    p.add_argument("--variavel", default="petroleo", help="petroleo, oleo, gas, agua, boe, rgo, corte_agua... (anp variaveis)")
    p.add_argument("--unidade", help="bbl/d, m3/d, mil m3/d, MM m3/d, MMcf/d, boe/d...")
    p.add_argument("--inicio", help="AAAA-MM")
    p.add_argument("--fim", help="AAAA-MM")
    p.add_argument("--quebra", choices=["campo", "poco", "instalacao"], help="composição por campo, poço ou instalação")
    p.add_argument("--liquida-de", dest="liquida_de", action="append", help="empresa (repita para somar várias)")


def criar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anp", description="Produção ANP por campo: site local, consultas e MCP.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--versao", action="version", version=f"anp-producao {__version__}")
    p.add_argument("--dados", help="pasta de dados (padrão: ANP/dados ou ANP_DIR_DADOS)")
    p.add_argument("-v", "--verboso", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("servir", help="abre o site local")
    s.add_argument("--porta", type=int)
    s.add_argument("--host")
    s.add_argument("--sem-navegador", action="store_true")
    s.set_defaults(func=cmd_servir)

    s = sub.add_parser("atualizar", help="atualiza a base com os arquivos da ANP")
    s.add_argument("--completa", action="store_true", help="baixa tudo de novo")
    s.add_argument("--origem", default="cli")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_atualizar)

    s = sub.add_parser("reprocessar", help="regrava a base a partir do cache local, sem baixar")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_reprocessar)

    for nome, func, ajuda in (("status", cmd_status, "último mês, última atualização e lacunas"),
                              ("variaveis", cmd_variaveis, "variáveis e unidades")):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)

    s = sub.add_parser("campos", help="busca campos")
    s.add_argument("--busca")
    s.add_argument("--bacia")
    s.add_argument("--estado")
    s.add_argument("--operador")
    s.add_argument("--empresa")
    s.add_argument("--ambiente", choices=["MAR", "TERRA"])
    s.add_argument("--ativos", action="store_true", help="só com produção nos últimos 12 meses")
    s.add_argument("--limite", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_campos)

    s = sub.add_parser("empresas", help="empresas com participação em campos")
    s.add_argument("--busca")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_empresas)

    s = sub.add_parser("clusters", help="lista, cria, edita, apaga, exporta e importa clusters")
    s.add_argument("acao", choices=["listar", "mostrar", "criar", "editar", "apagar", "exportar", "importar"])
    s.add_argument("nome", nargs="?", help="nome do cluster (ou arquivo JSON em importar)")
    s.add_argument("--campos", action="append", help="campo escolhido à mão (repita)")
    s.add_argument("--adicionar", action="append")
    s.add_argument("--remover", action="append")
    s.add_argument("--excluir", action="append")
    s.add_argument("--operador", action="append")
    s.add_argument("--bacia", action="append")
    s.add_argument("--estado", action="append")
    s.add_argument("--ambiente", action="append", choices=["MAR", "TERRA"])
    s.add_argument("--empresa", action="append")
    s.add_argument("--situacao", action="append")
    s.add_argument("--ativos", action="store_true")
    s.add_argument("--renomear")
    s.add_argument("--descricao")
    s.add_argument("--cor")
    s.add_argument("--saida")
    s.add_argument("--substituir", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_clusters)

    s = sub.add_parser("serie", help="série mensal e indicadores")
    s.add_argument("alvos", nargs="+", help="cluster:NOME, campo:NOME, bacia:NOME, operador:NOME, todos...")
    _opcoes_serie(s)
    s.add_argument("--top", type=int, default=8)
    s.add_argument("--linhas", type=int, default=24, help="meses mostrados na tabela de texto")
    s.add_argument("--csv", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_serie)

    s = sub.add_parser("detalhe", help="tabela de um mês por poço, instalação ou campo")
    s.add_argument("alvo")
    s.add_argument("--quebra", default="poco", choices=["poco", "instalacao", "campo"])
    s.add_argument("--mes")
    s.add_argument("--liquida-de", dest="liquida_de", action="append")
    s.add_argument("--limite", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_detalhe)

    s = sub.add_parser("exportar", help="gera Excel ou CSV")
    s.add_argument("tipo", choices=["serie", "detalhe"])
    s.add_argument("alvos", nargs="+")
    _opcoes_serie(s)
    s.add_argument("--mes")
    s.add_argument("--formato", default="xlsx", choices=["xlsx", "csv"])
    s.add_argument("--saida")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_exportar)

    s = sub.add_parser("revisoes", help="meses revisados pela ANP")
    s.add_argument("--nao-vistas", action="store_true")
    s.add_argument("--limite", type=int, default=100)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_revisoes)

    s = sub.add_parser("link", help="link do site para uma visão")
    s.add_argument("alvos", nargs="+")
    _opcoes_serie(s)
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("mcp", help="servidor MCP (stdio) para agentes")
    s.set_defaults(func=cmd_mcp)
    return p


def _saida_tolerante() -> None:
    """No Windows, saída redirecionada usa cp1252; caractere fora dele (CO₂) vira "?" em vez de erro."""
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            try:
                fluxo.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _saida_tolerante()
    args = criar_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verboso else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    sobrescritas = {"dir_dados": args.dados}
    if getattr(args, "porta", None):
        sobrescritas["porta"] = args.porta
    if getattr(args, "host", None):
        sobrescritas["host"] = args.host
    svc = Servicos(carregar_config(**sobrescritas))
    try:
        return args.func(svc, args)
    except NaoEncontrado as exc:
        print(f"Não encontrado: {exc}", file=sys.stderr)
        return 2
    except AtualizacaoEmAndamento as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ErroUsuario as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
