"""Clusters de campos: grupos salvos que você monta e reutiliza.

Um cluster combina três coisas:

* ``incluir``: campos escolhidos à mão;
* ``regra``: critérios que puxam campos automaticamente (operador, bacia,
  estado, ambiente, empresa sócia, situação, só ativos). Dentro de um critério
  vale qualquer opção (OU); entre critérios valem todos (E);
* ``excluir``: campos que nunca entram, mesmo que a regra os puxe.

Cada mudança grava um backup em JSON (``dados/clusters.json`` e uma cópia com
data em ``dados/backups``), então os clusters sobrevivem até a uma base apagada.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from anp_producao.cadastro import InfoCampo
from anp_producao.db import transacao
from anp_producao.formatos import somar_meses
from anp_producao.leitor_csv import chave_nome, limpar_nome

MAX_BACKUPS = 30
CRITERIOS = ("operadores", "bacias", "estados", "ambientes", "empresas", "situacoes")


class ErroCluster(ValueError):
    pass


class ClusterNaoEncontrado(LookupError):
    pass


@dataclass
class Regra:
    operadores: list[str] = field(default_factory=list)
    bacias: list[str] = field(default_factory=list)
    estados: list[str] = field(default_factory=list)
    ambientes: list[str] = field(default_factory=list)
    empresas: list[str] = field(default_factory=list)
    situacoes: list[str] = field(default_factory=list)
    somente_ativos: bool = False

    @classmethod
    def de_dict(cls, dados: dict | None) -> "Regra":
        dados = dados or {}
        desconhecidos = set(dados) - set(CRITERIOS) - {"somente_ativos"}
        if desconhecidos:
            raise ErroCluster(f"Critérios desconhecidos na regra: {sorted(desconhecidos)}")
        valores = {}
        for c in CRITERIOS:
            itens = dados.get(c) or []
            if isinstance(itens, str):
                itens = [itens]
            valores[c] = sorted({limpar_nome(i) for i in itens if limpar_nome(i)})
        valores["ambientes"] = sorted({a.upper() for a in valores["ambientes"]})
        return cls(**valores, somente_ativos=bool(dados.get("somente_ativos", False)))

    def vazia(self) -> bool:
        return not any(getattr(self, c) for c in CRITERIOS) and not self.somente_ativos

    def como_dict(self) -> dict:
        return asdict(self)

    def aceita(self, campo: InfoCampo, ultimo_mes_global: int | None) -> bool:
        if self.vazia():
            return False

        def bate(opcoes: list[str], valores: list[str]) -> bool:
            if not opcoes:
                return True
            chaves = {chave_nome(v) for v in valores if v}
            return any(chave_nome(o) in chaves for o in opcoes)

        if not bate(self.operadores, campo.operadores):
            return False
        if not bate(self.bacias, [campo.bacia or ""]):
            return False
        if not bate(self.estados, [campo.estado or ""]):
            return False
        if not bate(self.ambientes, campo.ambientes):
            return False
        if not bate(self.empresas, [e for e, p in campo.empresas.items() if p > 0]):
            return False
        if not bate(self.situacoes, campo.situacoes):
            return False
        if self.somente_ativos:
            if not campo.ultimo_mes or not ultimo_mes_global:
                return False
            if campo.ultimo_mes < somar_meses(ultimo_mes_global, -12):
                return False
        return True


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validar_nome(nome: str) -> str:
    nome = limpar_nome(nome)
    if not nome:
        raise ErroCluster("Dê um nome ao cluster")
    if len(nome) > 80:
        raise ErroCluster("Nome do cluster com mais de 80 caracteres")
    if ":" in nome or "," in nome:
        raise ErroCluster("O nome do cluster não pode ter ':' nem ','")
    return nome


def _garantir_nome_livre(con: sqlite3.Connection, nome: str, exceto_id: int | None = None) -> None:
    for l in con.execute("SELECT id, nome FROM clusters"):
        if l["id"] != exceto_id and chave_nome(l["nome"]) == chave_nome(nome):
            raise ErroCluster(f"Já existe um cluster chamado {l['nome']}")


def _validar_cor(cor: str | None) -> str | None:
    if cor in (None, ""):
        return None
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor):
        raise ErroCluster(f"Cor inválida: {cor}. Use o formato #RRGGBB")
    return cor.upper()


def _lista_campos(campos) -> list[str]:
    """Lista limpa, sem repetidos (ignorando acentos e caixa), em ordem alfabética."""
    if campos is None:
        return []
    if isinstance(campos, str):
        campos = campos.split(",")
    vistos: dict[str, str] = {}
    for c in campos:
        nome = limpar_nome(c)
        if nome and chave_nome(nome) not in vistos:
            vistos[chave_nome(nome)] = nome
    return [vistos[k] for k in sorted(vistos)]


def _linha_para_dict(con: sqlite3.Connection, linha: sqlite3.Row) -> dict:
    campos = con.execute(
        "SELECT campo, modo FROM cluster_campos WHERE cluster_id = ? ORDER BY campo", (linha["id"],)
    ).fetchall()
    regra = json.loads(linha["regra"] or "{}")
    return {
        "id": linha["id"],
        "nome": linha["nome"],
        "descricao": linha["descricao"],
        "cor": linha["cor"],
        "regra": Regra.de_dict(regra).como_dict(),
        "incluir": sorted((c["campo"] for c in campos if c["modo"] == "incluir"), key=chave_nome),
        "excluir": sorted((c["campo"] for c in campos if c["modo"] == "excluir"), key=chave_nome),
        "criado_em": linha["criado_em"],
        "atualizado_em": linha["atualizado_em"],
    }


def listar(con: sqlite3.Connection) -> list[dict]:
    linhas = con.execute("SELECT * FROM clusters ORDER BY nome COLLATE NOCASE").fetchall()
    return [_linha_para_dict(con, l) for l in linhas]


def _buscar_linha(con: sqlite3.Connection, ref: str | int) -> sqlite3.Row:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        linha = con.execute("SELECT * FROM clusters WHERE id = ?", (int(ref),)).fetchone()
        if linha:
            return linha
    linha = con.execute(
        "SELECT * FROM clusters WHERE nome = ? COLLATE NOCASE", (limpar_nome(str(ref)),)
    ).fetchone()
    if not linha:
        # Tolerância a acentos e caixa
        for l in con.execute("SELECT * FROM clusters"):
            if chave_nome(l["nome"]) == chave_nome(str(ref)):
                return l
        raise ClusterNaoEncontrado(f"Cluster não encontrado: {ref}")
    return linha


def obter(con: sqlite3.Connection, ref: str | int) -> dict:
    return _linha_para_dict(con, _buscar_linha(con, ref))


def _gravar_campos(con: sqlite3.Connection, cluster_id: int, incluir: list[str], excluir: list[str]) -> None:
    con.execute("DELETE FROM cluster_campos WHERE cluster_id = ?", (cluster_id,))
    con.executemany(
        "INSERT INTO cluster_campos (cluster_id, campo, modo) VALUES (?, ?, ?)",
        [(cluster_id, c, "incluir") for c in incluir] + [(cluster_id, c, "excluir") for c in excluir],
    )


def criar(
    con: sqlite3.Connection,
    nome: str,
    incluir=None,
    regra: dict | None = None,
    excluir=None,
    descricao: str = "",
    cor: str | None = None,
    dir_dados: Path | None = None,
) -> dict:
    nome = _validar_nome(nome)
    regra_obj = Regra.de_dict(regra)
    incluir_l = _lista_campos(incluir)
    excluir_l = _lista_campos(excluir)
    if regra_obj.vazia() and not incluir_l:
        raise ErroCluster("O cluster precisa de pelo menos um campo ou um critério de regra")
    _garantir_nome_livre(con, nome)
    agora = _agora()
    with transacao(con):
        try:
            cid = con.execute(
                "INSERT INTO clusters (nome, descricao, cor, regra, criado_em, atualizado_em) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nome, limpar_nome(descricao), _validar_cor(cor),
                 json.dumps(regra_obj.como_dict(), ensure_ascii=False), agora, agora),
            ).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroCluster(f"Já existe um cluster chamado {nome}") from exc
        _gravar_campos(con, cid, incluir_l, excluir_l)
    resultado = obter(con, cid)
    if dir_dados:
        salvar_backup(con, dir_dados)
    return resultado


def editar(
    con: sqlite3.Connection,
    ref: str | int,
    novo_nome: str | None = None,
    descricao: str | None = None,
    cor: str | None = None,
    regra: dict | None = None,
    incluir=None,
    excluir=None,
    adicionar=None,
    remover=None,
    dir_dados: Path | None = None,
) -> dict:
    """Altera só o que vier preenchido. ``incluir``/``excluir`` substituem a lista;
    ``adicionar``/``remover`` mexem na lista de campos incluídos."""
    atual = obter(con, ref)
    nome = _validar_nome(novo_nome) if novo_nome else atual["nome"]
    regra_obj = Regra.de_dict(regra) if regra is not None else Regra.de_dict(atual["regra"])
    incluir_l = _lista_campos(incluir) if incluir is not None else list(atual["incluir"])
    excluir_l = _lista_campos(excluir) if excluir is not None else list(atual["excluir"])
    if adicionar:
        incluir_l = _lista_campos(incluir_l + _lista_campos(adicionar))
        excluir_l = [c for c in excluir_l if chave_nome(c) not in {chave_nome(a) for a in _lista_campos(adicionar)}]
    if remover:
        tirar = {chave_nome(r) for r in _lista_campos(remover)}
        sobrou = [c for c in incluir_l if chave_nome(c) not in tirar]
        # Campo puxado pela regra só sai se for para a lista de excluídos.
        if not regra_obj.vazia():
            excluir_l = _lista_campos(excluir_l + [r for r in _lista_campos(remover)])
        incluir_l = sobrou
    if regra_obj.vazia() and not incluir_l:
        raise ErroCluster("O cluster ficaria vazio: mantenha um campo ou um critério de regra")
    _garantir_nome_livre(con, nome, exceto_id=atual["id"])
    with transacao(con):
        try:
            con.execute(
                "UPDATE clusters SET nome = ?, descricao = ?, cor = ?, regra = ?, atualizado_em = ? "
                "WHERE id = ?",
                (
                    nome,
                    limpar_nome(descricao) if descricao is not None else atual["descricao"],
                    _validar_cor(cor) if cor is not None else atual["cor"],
                    json.dumps(regra_obj.como_dict(), ensure_ascii=False),
                    _agora(),
                    atual["id"],
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ErroCluster(f"Já existe um cluster chamado {nome}") from exc
        _gravar_campos(con, atual["id"], incluir_l, excluir_l)
    resultado = obter(con, atual["id"])
    if dir_dados:
        salvar_backup(con, dir_dados)
    return resultado


def apagar(con: sqlite3.Connection, ref: str | int, dir_dados: Path | None = None) -> dict:
    atual = obter(con, ref)
    if dir_dados:
        salvar_backup(con, dir_dados)  # guarda o estado de antes de apagar
    with transacao(con):
        con.execute("DELETE FROM clusters WHERE id = ?", (atual["id"],))
    if dir_dados:
        salvar_backup(con, dir_dados)
    return atual


def resolver(
    cluster: dict, info: dict[int, InfoCampo], ultimo_mes_global: int | None
) -> tuple[list[int], list[str]]:
    """Ids dos campos do cluster e nomes incluídos à mão que não existem na base."""
    por_chave: dict[str, int] = {chave_nome(c.nome): cid for cid, c in info.items()}
    ids: set[int] = set()
    nao_encontrados = []
    for nome in cluster["incluir"]:
        cid = por_chave.get(chave_nome(nome))
        if cid is None:
            nao_encontrados.append(nome)
        else:
            ids.add(cid)
    regra = Regra.de_dict(cluster.get("regra"))
    if not regra.vazia():
        ids |= {cid for cid, c in info.items() if regra.aceita(c, ultimo_mes_global)}
    for nome in cluster["excluir"]:
        ids.discard(por_chave.get(chave_nome(nome), -1))
    return sorted(ids), nao_encontrados


def exportar(con: sqlite3.Connection) -> dict:
    return {
        "formato": "anp-producao/clusters",
        "versao": 1,
        "exportado_em": _agora(),
        "clusters": [
            {k: c[k] for k in ("nome", "descricao", "cor", "regra", "incluir", "excluir")}
            for c in listar(con)
        ],
    }


def importar(
    con: sqlite3.Connection, dados: dict, substituir: bool = False, dir_dados: Path | None = None
) -> dict:
    if not isinstance(dados, dict) or not isinstance(dados.get("clusters"), list):
        raise ErroCluster("Arquivo de clusters inválido: falta a lista 'clusters'")
    if dir_dados:
        salvar_backup(con, dir_dados)
    criados = atualizados = 0
    erros = []
    if substituir:
        with transacao(con):
            con.execute("DELETE FROM clusters")
    for item in dados["clusters"]:
        try:
            nome = _validar_nome(item.get("nome", ""))
            existe = con.execute("SELECT id FROM clusters WHERE nome = ? COLLATE NOCASE", (nome,)).fetchone()
            if existe:
                editar(con, existe["id"], descricao=item.get("descricao", ""), cor=item.get("cor"),
                       regra=item.get("regra") or {}, incluir=item.get("incluir") or [],
                       excluir=item.get("excluir") or [])
                atualizados += 1
            else:
                criar(con, nome, incluir=item.get("incluir"), regra=item.get("regra"),
                      excluir=item.get("excluir"), descricao=item.get("descricao", ""), cor=item.get("cor"))
                criados += 1
        except (ErroCluster, ClusterNaoEncontrado) as exc:
            erros.append(f"{item.get('nome', '?')}: {exc}")
    if dir_dados:
        salvar_backup(con, dir_dados)
    return {"criados": criados, "atualizados": atualizados, "erros": erros}


def salvar_backup(con: sqlite3.Connection, dir_dados: Path) -> Path:
    """Grava ``clusters.json`` (estado atual) e uma cópia datada em ``backups``."""
    conteudo = json.dumps(exportar(con), ensure_ascii=False, indent=2)
    principal = dir_dados / "clusters.json"
    pasta = dir_dados / "backups"
    pasta.mkdir(parents=True, exist_ok=True)
    temporario = principal.with_suffix(".json.tmp")
    temporario.write_text(conteudo, encoding="utf-8")
    temporario.replace(principal)
    copia = pasta / f"clusters-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    copia.write_text(conteudo, encoding="utf-8")
    antigos = sorted(pasta.glob("clusters-*.json"))
    for velho in antigos[:-MAX_BACKUPS]:
        velho.unlink(missing_ok=True)
    return principal
