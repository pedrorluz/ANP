"""Configuração do aplicativo.

Tudo pode ser ajustado por variáveis de ambiente, sem arquivo extra:

| Variável            | Padrão                                   | Uso                                              |
|---------------------|------------------------------------------|--------------------------------------------------|
| ANP_DIR_DADOS       | <pasta do projeto>/dados                  | Onde ficam os bancos, o cache bruto e exportações |
| ANP_URL_PAGINA      | página "Fase de Desenvolvimento e Produção" | Página da ANP lida para achar os arquivos        |
| ANP_HOST            | 127.0.0.1                                | Endereço do servidor web (só local por padrão)    |
| ANP_PORTA           | 8765                                     | Porta do servidor web                             |
| ANP_JANELA_REVISAO  | 2                                        | Anos mais recentes sempre baixados de novo        |
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

URL_PAGINA_PADRAO = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "fase-de-desenvolvimento-e-producao"
)
PREFIXO_ANP = "https://www.gov.br/anp/"


def _raiz_projeto() -> Path:
    """Pasta do projeto (onde está o pyproject.toml), ou a pasta atual."""
    candidata = Path(__file__).resolve().parents[2]
    if (candidata / "pyproject.toml").exists():
        return candidata
    return Path.cwd()


@dataclass(frozen=True)
class Config:
    dir_dados: Path
    url_pagina: str = URL_PAGINA_PADRAO
    host: str = "127.0.0.1"
    porta: int = 8765
    janela_revisao_anos: int = 2
    timeout_s: float = 60.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36 anp-producao/1.0 (uso pessoal de dados abertos)"
    )

    @property
    def banco_anp(self) -> Path:
        return self.dir_dados / "anp.sqlite"

    @property
    def banco_usuario(self) -> Path:
        return self.dir_dados / "usuario.sqlite"

    @property
    def dir_brutos(self) -> Path:
        return self.dir_dados / "brutos"

    @property
    def dir_exportacoes(self) -> Path:
        return self.dir_dados / "exportacoes"

    @property
    def dir_backups(self) -> Path:
        return self.dir_dados / "backups"

    @property
    def dir_logs(self) -> Path:
        return self.dir_dados / "logs"

    @property
    def arquivo_trava(self) -> Path:
        return self.dir_dados / ".atualizacao.lock"

    @property
    def arquivo_clusters_json(self) -> Path:
        return self.dir_dados / "clusters.json"

    @property
    def url_painel(self) -> str:
        return f"http://{self.host}:{self.porta}/"

    def prefixos_permitidos(self) -> tuple[str, ...]:
        """Origens de onde a atualização aceita baixar arquivos."""
        partes = urlsplit(self.url_pagina)
        origem = f"{partes.scheme}://{partes.netloc}/"
        if self.url_pagina.startswith(PREFIXO_ANP):
            return (PREFIXO_ANP,)
        return (PREFIXO_ANP, origem)

    def garantir_pastas(self) -> None:
        for pasta in (
            self.dir_dados,
            self.dir_brutos,
            self.dir_exportacoes,
            self.dir_backups,
            self.dir_logs,
        ):
            pasta.mkdir(parents=True, exist_ok=True)


def carregar_config(**sobrescritas) -> Config:
    """Lê a configuração do ambiente. Argumentos nomeados têm prioridade."""
    dir_dados = Path(os.environ.get("ANP_DIR_DADOS") or (_raiz_projeto() / "dados"))
    valores = dict(
        dir_dados=dir_dados.expanduser().resolve(),
        url_pagina=os.environ.get("ANP_URL_PAGINA", URL_PAGINA_PADRAO),
        host=os.environ.get("ANP_HOST", "127.0.0.1"),
        porta=int(os.environ.get("ANP_PORTA", "8765")),
        janela_revisao_anos=int(os.environ.get("ANP_JANELA_REVISAO", "2")),
    )
    valores.update({k: v for k, v in sobrescritas.items() if v is not None})
    if isinstance(valores["dir_dados"], str):
        valores["dir_dados"] = Path(valores["dir_dados"]).expanduser().resolve()
    return Config(**valores)
