"""Acesso ao site da ANP: descoberta dos arquivos e download.

O site (gov.br, Plone atrás de Varnish) tem algumas particularidades levantadas
em nov/2026:

* requisições HEAD voltam 403, então o tamanho remoto é lido com um GET de
  um único byte (``Range: bytes=0-0``), que o servidor atende com 206;
* não há ETag nem Last-Modified, então mudança de conteúdo só é confirmada
  pelo SHA-256 do arquivo baixado;
* os nomes dos arquivos não seguem padrão, por isso os links são sempre lidos
  da página em vez de montados.

A mesma página lista os arquivos mensais de "Produção por zona" (out/2014 em
diante), usados para completar meses que faltam nos arquivos por poço (ver
complemento.py). O mês de cada um sai do nome do arquivo e é conferido no
conteúdo, porque há links trocados na página (o de abril/2023 aponta para o
arquivo de março).
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import ssl
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from anp_producao.config import Config

log = logging.getLogger(__name__)

TRECHO_PRODUCAO = "/arquivos-producao-de-petroleo-e-gas-natural-nacional/"
TRECHO_ZONA = "/producao-por-zona/"


class ErroFonte(RuntimeError):
    """Problema ao ler a página ou baixar arquivos da ANP."""


@dataclass(frozen=True)
class ArquivoProducao:
    url: str
    nome: str
    ambiente: str  # MAR ou TERRA
    ano_ref: int  # maior ano citado no caminho; serve para ordenar e para a janela de revisão
    sub_periodo: int  # trimestre ou semestre, quando houver

    @property
    def ordem(self) -> tuple[int, int]:
        return (self.ano_ref, self.sub_periodo)


@dataclass(frozen=True)
class ArquivoZona:
    """Arquivo mensal de produção por zona (média diária por poço e zona)."""

    url: str
    nome: str
    mes: int  # AAAAMM tirado do nome; o conteúdo é conferido ao ler


@dataclass(frozen=True)
class PaginaANP:
    arquivos: list[ArquivoProducao]
    url_campos: str | None
    url_concessionarios: str | None
    atualizada_em: str | None  # ISO 8601, como aparece em "Atualizado em" na página
    zonas: list[ArquivoZona] = field(default_factory=list)  # do mês mais novo para o mais antigo


def _anos_no_texto(texto: str) -> list[int]:
    return [int(a) for a in re.findall(r"(?<!\d)(19[3-9]\d|20\d\d)(?!\d)", texto)]


def _sub_periodo(nome: str) -> int:
    n = nome.lower()
    if m := re.search(r"(\d)\D{0,2}trim|trim\D{0,2}(\d)", n):
        return int(m.group(1) or m.group(2))
    if m := re.search(r"(\d)\D{0,2}sem", n):
        return int(m.group(1)) * 2
    return 0


def _ambiente(url: str) -> str | None:
    caminho = urlsplit(url).path.lower()
    if "/pm/" in caminho:
        return "MAR"
    if "/pt/" in caminho:
        return "TERRA"
    nome = caminho.rsplit("/", 1)[-1]
    if "terra" in nome:
        return "TERRA"
    if "mar" in nome:
        return "MAR"
    return None


def _mes_valido(ano: int, mes: int) -> int | None:
    if 1 <= mes <= 12 and 1990 <= ano <= 2100:
        return ano * 100 + mes
    return None


def mes_do_arquivo_zona(nome: str) -> int | None:
    """Mês (AAAAMM) no nome de um arquivo por zona.

    Formatos vistos: ``producao_zona_04-2026.csv`` e ``Produo_Zona_032026.csv``.
    Também aceita ano antes do mês (``2026-04`` e ``202604``)."""
    base = nome.rsplit(".", 1)[0]
    padroes = (
        (r"(?<!\d)(\d{1,2})[-_.](\d{4})(?!\d)", "ma"),
        (r"(?<!\d)(\d{4})[-_.](\d{1,2})(?!\d)", "am"),
        (r"(?<!\d)(\d{2})(\d{4})(?!\d)", "ma"),
        (r"(?<!\d)(\d{4})(\d{2})(?!\d)", "am"),
    )
    for padrao, ordem in padroes:
        for a, b in re.findall(padrao, base):
            mes, ano = (int(a), int(b)) if ordem == "ma" else (int(b), int(a))
            if valor := _mes_valido(ano, mes):
                return valor
    return None


def extrair_pagina(conteudo_html: str, url_base: str, prefixos: tuple[str, ...]) -> PaginaANP:
    """Extrai da página da ANP os links de produção por poço, por zona e de cadastro."""
    arquivos: dict[str, ArquivoProducao] = {}
    zonas: dict[str, ArquivoZona] = {}
    url_campos = url_concessionarios = None
    for bruto in re.findall(r"""href\s*=\s*["']([^"']+)["']""", conteudo_html, flags=re.I):
        url = urljoin(url_base, html.unescape(bruto).strip())
        for sufixo in ("/view", "/@@download/file", "/download"):
            if url.endswith(sufixo):
                url = url[: -len(sufixo)]
        if not url.startswith(prefixos):
            continue
        baixo = url.lower()
        if not baixo.endswith(".csv"):
            continue
        if baixo.endswith("/extracao-campo.csv"):
            url_campos = url
            continue
        if baixo.endswith("/extracao-campo-concessionarios.csv"):
            url_concessionarios = url
            continue
        if TRECHO_ZONA in baixo:
            nome = url.rsplit("/", 1)[-1]
            mes = mes_do_arquivo_zona(nome)
            if mes is None:
                log.warning("Arquivo por zona sem mês reconhecível no nome: %s", nome)
            else:
                zonas[url] = ArquivoZona(url=url, nome=nome, mes=mes)
            continue
        if TRECHO_PRODUCAO not in baixo:
            continue
        ambiente = _ambiente(url)
        if ambiente is None:
            continue
        trecho = url.split(TRECHO_PRODUCAO, 1)[1]
        nome = trecho.rsplit("/", 1)[-1]
        # Anos do nome do arquivo valem mais que os da pasta (pt/1988-1983/...-1985.csv).
        anos = _anos_no_texto(nome) or _anos_no_texto(trecho)
        arquivos[url] = ArquivoProducao(
            url=url,
            nome=nome,
            ambiente=ambiente,
            ano_ref=max(anos) if anos else 0,
            sub_periodo=_sub_periodo(nome),
        )

    datas = []
    for d, m, a, hh, mm in re.findall(
        r"[Aa]tualizado em\s*(\d{1,2})/(\d{1,2})/(\d{4})(?:\s*(\d{1,2})h(\d{2}))?",
        re.sub(r"<[^>]+>", " ", conteudo_html),
    ):
        try:
            datas.append(datetime(int(a), int(m), int(d), int(hh or 0), int(mm or 0)))
        except ValueError:
            continue
    atualizada = max(datas).isoformat(timespec="minutes") if datas else None

    ordenados = sorted(arquivos.values(), key=lambda a: (a.ordem, a.ambiente), reverse=True)
    zonas_ordenadas = sorted(zonas.values(), key=lambda z: z.mes, reverse=True)
    return PaginaANP(ordenados, url_campos, url_concessionarios, atualizada, zonas_ordenadas)


def _contexto_tls() -> ssl.SSLContext | bool:
    """Usa o cofre de certificados do sistema (macOS Keychain) quando disponível."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - depende do sistema
        return True


class ClienteANP:
    """Cliente HTTP com novas tentativas e limites de domínio."""

    def __init__(
        self,
        config: Config,
        transport: httpx.BaseTransport | None = None,
        pausa_entre_tentativas: float = 2.0,
    ):
        self.config = config
        self.prefixos = config.prefixos_permitidos()
        self.pausa = pausa_entre_tentativas
        self._cliente = httpx.Client(
            timeout=httpx.Timeout(config.timeout_s, connect=20.0),
            headers={"User-Agent": config.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"},
            follow_redirects=True,
            verify=_contexto_tls() if transport is None else True,
            transport=transport,
        )

    def fechar(self) -> None:
        self._cliente.close()

    def __enter__(self) -> "ClienteANP":
        return self

    def __exit__(self, *exc) -> None:
        self.fechar()

    def _validar(self, url: str) -> None:
        if not url.startswith(self.prefixos):
            raise ErroFonte(f"Endereço fora da ANP recusado: {url}")

    def _com_tentativas(self, descricao: str, funcao: Callable[[], object], tentativas: int = 3):
        ultimo_erro: Exception | None = None
        for tentativa in range(1, tentativas + 1):
            try:
                return funcao()
            except httpx.HTTPStatusError as exc:
                codigo = exc.response.status_code
                ultimo_erro = exc
                if codigo < 500 and codigo != 429:
                    break
            except httpx.TransportError as exc:
                ultimo_erro = exc
            if tentativa < tentativas:
                espera = self.pausa * (3 ** (tentativa - 1))
                log.warning("%s falhou (%s). Nova tentativa em %.0fs", descricao, ultimo_erro, espera)
                time.sleep(espera)
        raise ErroFonte(f"{descricao} falhou: {ultimo_erro}") from ultimo_erro

    def pagina(self) -> PaginaANP:
        url = self.config.url_pagina
        self._validar(url)

        def _get() -> str:
            r = self._cliente.get(url)
            r.raise_for_status()
            return r.text

        conteudo = self._com_tentativas("Leitura da página da ANP", _get)
        pagina = extrair_pagina(conteudo, url, self.prefixos)
        if not pagina.arquivos:
            raise ErroFonte(
                "Nenhum arquivo de produção por poço encontrado na página da ANP. "
                "O layout da página pode ter mudado."
            )
        return pagina

    def tamanho_remoto(self, url: str) -> int | None:
        """Tamanho do arquivo no servidor, sem baixá-lo (GET de 1 byte)."""
        self._validar(url)

        def _probe() -> int | None:
            with self._cliente.stream("GET", url, headers={"Range": "bytes=0-0"}) as r:
                r.raise_for_status()
                if r.status_code == 206:
                    faixa = r.headers.get("content-range", "")
                    m = re.search(r"/(\d+)$", faixa)
                    return int(m.group(1)) if m else None
                tamanho = r.headers.get("content-length")
                return int(tamanho) if tamanho else None

        try:
            return self._com_tentativas(f"Consulta de tamanho de {url}", _probe)
        except ErroFonte as exc:
            log.warning("%s", exc)
            return None

    def baixar(self, url: str, destino_dir: Path) -> tuple[Path, int, str]:
        """Baixa para um arquivo temporário. Devolve (caminho, bytes, sha256)."""
        self._validar(url)
        destino_dir.mkdir(parents=True, exist_ok=True)

        def _baixar() -> tuple[Path, int, str]:
            sha = hashlib.sha256()
            total = 0
            fd, nome_tmp = tempfile.mkstemp(prefix="anp-", suffix=".csv.part", dir=destino_dir)
            caminho = Path(nome_tmp)
            try:
                with open(fd, "wb") as f, self._cliente.stream("GET", url) as r:
                    r.raise_for_status()
                    for bloco in r.iter_bytes(1 << 16):
                        f.write(bloco)
                        sha.update(bloco)
                        total += len(bloco)
                esperado = r.headers.get("content-length")
                if esperado and int(esperado) != total:
                    raise httpx.TransportError(
                        f"download incompleto ({total} de {esperado} bytes)"
                    )
            except BaseException:
                caminho.unlink(missing_ok=True)
                raise
            return caminho, total, sha.hexdigest()

        return self._com_tentativas(f"Download de {url}", _baixar)

    def baixar_bytes(self, url: str) -> bytes:
        self._validar(url)

        def _get() -> bytes:
            r = self._cliente.get(url)
            r.raise_for_status()
            return r.content

        return self._com_tentativas(f"Download de {url}", _get)
