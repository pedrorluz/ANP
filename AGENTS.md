# AGENTS.md

Guia para agentes (Hermes, Claude, Codex e afins) que usam ou mantêm este projeto.

## Para consultar dados

Prefira o servidor MCP. Ele expõe as mesmas operações do site e devolve JSON.

| Ferramenta | Para quê |
|---|---|
| `status_base` | Último mês na base, última atualização, progresso, lacunas da fonte |
| `atualizar_base` | Dispara a atualização em segundo plano; acompanhe com `status_base` |
| `buscar_campos` | Achar o nome exato de um campo e ver bacia, operador, sócios e produção atual |
| `listar_empresas`, `opcoes_de_filtro` | Nomes válidos de empresas, bacias, operadores, estados |
| `serie_producao` | Série mensal e indicadores de um ou mais alvos (comparação) ou composição (`quebra`) |
| `indicadores` | Só os indicadores de um alvo |
| `detalhar` | Tabela de um mês por poço, instalação ou campo |
| `listar_clusters`, `criar_cluster`, `editar_cluster`, `apagar_cluster` | Clusters salvos do usuário |
| `exportar_planilha` | Gera Excel ou CSV e devolve o caminho |
| `revisoes_recentes` | Meses que a ANP revisou |
| `link_painel` | Link para abrir a mesma visão no site |

### Configuração

Hermes (`~/.hermes/config.yaml`, depois `/reload-mcp`):

```yaml
mcp_servers:
  anp-producao:
    command: "/Users/SEU_USUARIO/ANP/.venv/bin/anp"
    args: ["mcp"]
    timeout: 120
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{"mcpServers": {"anp-producao": {"command": "/Users/SEU_USUARIO/ANP/.venv/bin/anp", "args": ["mcp"]}}}
```

Claude Code: `claude mcp add anp-producao -- /Users/SEU_USUARIO/ANP/.venv/bin/anp mcp`

Troque `/Users/SEU_USUARIO/ANP` pela pasta do projeto. No Windows o executável fica em `C:\Users\SEU_USUARIO\ANP\.venv\Scripts\anp.exe`. O `.venv` existe depois de `uv sync` na pasta do projeto. Sem ele, use `uv run --directory /Users/SEU_USUARIO/ANP anp mcp` com o caminho completo do `uv`.

### Alvos e variáveis

* Alvos são textos `tipo:valor`: `cluster:NOME`, `campo:NOME`, `poco:NOME`, `instalacao:NOME`, `bacia:NOME`, `estado:NOME`, `operador:NOME`, `empresa:RAZÃO SOCIAL`, `ambiente:MAR` ou `ambiente:TERRA`, e `todos`. Sem prefixo, procura cluster e depois campo. Acentos e caixa não importam.
* Variáveis: `petroleo` (óleo + condensado), `oleo`, `condensado`, `gas`, `gas_assoc`, `gas_nao_assoc`, `agua`, `boe`, `rgo`, `corte_agua`, `inj_agua`, `inj_agua_desc`, `inj_gas`, `inj_co2`, `inj_vapor`.
* Unidades padrão: `bbl/d` (líquidos), `mil m3/d` (gás), `boe/d`. Outras: `mil bbl/d`, `m3/d`, `m3/mes`, `MM m3/d`, `MMcf/d`, `scf/bbl`.
* Datas em `AAAA-MM`. Em `serie_producao`, sem `inicio` vêm os últimos 24 meses; os indicadores sempre usam o histórico inteiro.

### Regras para responder ao usuário

* Diga o mês de referência (`ultimo_mes_base`); a ANP publica com cerca de dois meses de atraso.
* `null` numa série é mês sem dado da ANP, não produção zero. Repasse os `avisos`.
* Produção líquida (`liquida_de`) usa a participação atual para todo o histórico; avise isso.
* Não invente nomes de campo: confirme com `buscar_campos` quando o nome vier do usuário.
* O usuário prefere resultados em tabela.

## Linha de comando

Tudo aceita `--json`. Exemplos:

```bash
uv run anp status --json
uv run anp campos --busca buzios --json
uv run anp serie campo:BÚZIOS campo:TUPI --variavel gas --inicio 2024-01 --json
uv run anp serie cluster:"Polo Bahia" --quebra campo --json
uv run anp detalhe campo:MERO --quebra poco --json
uv run anp clusters criar "Pré-sal Santos" --bacia Santos --ativos
uv run anp exportar serie bacia:Santos --formato xlsx
uv run anp atualizar
```

Códigos de saída: 0 ok, 1 erro de uso, 2 não encontrado, 3 atualização já em andamento.

## API HTTP

Com o site aberto (`uv run anp servir`), a API fica em `http://127.0.0.1:8765/api` e a documentação em `/docs`. Requisições que alteram dados (POST, PUT, DELETE) exigem o cabeçalho `X-ANP: 1`.

## Para quem mantém o código

* Python 3.12 via `uv`. Rode `uv sync` e depois `uv run pytest` (testes sem internet; a ANP é simulada em `tests/conftest.py`).
* `src/anp_producao/servicos.py` é a camada única usada pelo site, pela linha de comando e pelo MCP. Mudanças de regra entram ali ou abaixo.
* Dois bancos SQLite em WAL: `anp.sqlite` (derivado, descartável) e `usuario.sqlite` (clusters). Nunca misture os dois.
* O leitor (`leitor_csv.py`) reconhece colunas pelo nome, nunca pela posição, e trata as variações reais dos arquivos da ANP (codificação, colchetes, ordem de colunas, linhas embrulhadas em aspas, mês abreviado). Mantenha os testes de `tests/fixtures` passando.
* A página do gov.br devolve 403 para HEAD e não manda ETag; o tamanho remoto vem de um GET com `Range: bytes=0-0`.
* Lacunas da fonte são vazio, nunca zero (`qualidade.py` e `consultas._meses_faltando`).
* Meses que faltam ou vêm incompletos no arquivo por poço são completados com o arquivo mensal de produção por zona (`complemento.py`). O resultado de `serie_producao` traz esses meses em `meses_completados_com_producao_por_zona` e um aviso em `avisos`.
* A paleta do gráfico em `static/js/app.js` foi validada para daltonismo no fundo navy; troque cores só revalidando.
