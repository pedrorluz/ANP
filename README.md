# Produção ANP por campo

Site local para acompanhar a produção mensal de petróleo, gás e água dos campos brasileiros com os dados abertos da ANP. Roda no seu computador (Mac ou Windows), guarda seus clusters e também atende agentes (Hermes, Claude e afins) por MCP, linha de comando e API.

## Como abrir

1. Baixe o projeto (botão **Code**, **Download ZIP** no GitHub, ou `git clone`) e descompacte numa pasta sua.
2. Abra o site.
   * **Windows**, dois cliques em `iniciar.bat`. Se o Windows avisar que o arquivo veio da internet, clique em **Mais informações** e **Executar assim mesmo**.
   * **Mac**, dois cliques em `iniciar.command` no Finder. Se o macOS reclamar, rode `chmod +x iniciar.command` no Terminal uma vez.

   Na primeira vez o script oferece instalar o `uv` (que cuida do Python e das bibliotecas), prepara tudo e abre o site em http://127.0.0.1:8765. A janela preta precisa ficar aberta enquanto você usa o site.
3. Clique em **Atualizar base**. A primeira carga baixa todo o histórico publicado pela ANP (1941 até hoje, cerca de 630 MB e 5,3 milhões de linhas por poço) e leva uns 10 minutos. Os anos mais recentes entram primeiro.
4. Nas próximas vezes, **Atualizar base** baixa só o ano corrente e o anterior e confere o tamanho dos arquivos antigos. Leva segundos.

No Windows, evite deixar a pasta dentro do OneDrive. A sincronização trava o banco SQLite enquanto ele é gravado.

Pelo terminal o equivalente é:

```bash
cd ANP                   # a pasta do projeto
uv run anp servir        # abre o site
uv run anp atualizar     # atualiza a base sem abrir o site
```

Para deixar o site no ar na internet, com senha e sem o seu computador ligado, veja `deploy/HOSTINGER.md` (VPS paga no Brasil) ou `deploy/ORACLE.md` (Oracle Cloud, plano grátis).

## O que o site faz

| Recurso | Como usar |
|---|---|
| Clusters salvos | Botão **Novo** na lateral. Escolha campos à mão, crie uma regra (operador, bacia, estado, mar ou terra, empresa sócia, situação, só ativos) ou combine os dois. Campos novos que entram na regra aparecem sozinhos |
| Caixa seletora | **Variável** escolhe petróleo, óleo, condensado, gás, água, boe, RGO, corte de água e injeções |
| Unidades | bbl/d, mil bbl/d, m³/d, volume no mês; gás em mil m³/d, MM m³/d, MMcf/d ou boe/d |
| Comparar | Marque a caixinha de vários clusters, campos ou recortes |
| Composição | **Visão** por campo, por poço ou por instalação, com as 8 maiores partes e "Outros" |
| Produção líquida | **Participação** mostra só a parte de uma empresa (participação atual da ANP) |
| Indicadores | Último mês, variação mensal e anual, média de 12 meses, pico, acumulados e poços produtores |
| Detalhe do mês | Tabela por poço, instalação ou campo; clique numa linha para ver a série daquele item |
| Salvar os dados | **Excel** e **CSV** do que está na tela, **Imagem** do gráfico, **Link** da visão, exportação e importação de clusters |
| Último dado | No topo, o mês mais recente da base e a data da última atualização; passe o mouse para ver a data da publicação da ANP e a próxima esperada |
| Revisões | Quando a ANP muda números de meses antigos, aparece um aviso com o antes e o depois |

## Dados e cuidados

| Ponto | Detalhe |
|---|---|
| Fonte | ANP, dados abertos, seção Fase de Desenvolvimento e Produção, produção por poço (mar em arquivos anuais, terra em trimestrais), produção por zona (mensal, desde out/2014, usada só para completar meses) e cadastros de campos e concessionários |
| Defasagem | A ANP publica cerca de dois meses depois do mês de produção |
| Petróleo | Óleo + condensado, como nos boletins da ANP |
| Conversões | 1 m³ = 6,28981 bbl; 1.000 m³ de gás = 6,28981 boe (critério da ANP) |
| Médias diárias | Volume do mês dividido pelos dias do mês |
| Meses completados | Quando o arquivo por poço vem sem um mês ou sem parte dos poços, a atualização completa o mês com o arquivo mensal de produção por zona da mesma página da ANP (média diária por poço e zona, convertida em volume do mês). Só entram os poços que faltam. Em nov/2026 isso completou abril e julho de 2026 em terra, que não vieram, e março de 2026 em terra, que veio sem cerca de 1.400 poços. Esses meses aparecem com um aviso no site e marcados com † na tabela |
| Lacunas | Meses que continuam sem dado (nem no arquivo por poço nem no por zona) ficam vazios no gráfico, nunca zerados |
| Participação | O arquivo de concessionários traz só a participação atual, aplicada a todo o histórico. Campos com mais de um contrato têm a participação média |
| Nomes | Os campos seguem a grafia da ANP, inclusive áreas como AnC_Jubarte_Nordeste e BÚZIOS_ECO |

## Uso com agentes

Veja `AGENTS.md`. Em resumo, para o Hermes, acrescente em `~/.hermes/config.yaml`, trocando o caminho pela pasta onde você pôs o projeto:

```yaml
mcp_servers:
  anp-producao:
    command: "/Users/SEU_USUARIO/ANP/.venv/bin/anp"          # Mac
    # command: "C:\\Users\\SEU_USUARIO\\ANP\\.venv\\Scripts\\anp.exe"   # Windows
    args: ["mcp"]
    timeout: 120
```

Depois rode `/reload-mcp` numa conversa do Hermes. O `.venv` é criado na primeira vez que você abre o `iniciar.bat` ou o `iniciar.command` (ou com `uv sync`).

A API do site fica documentada em http://127.0.0.1:8765/docs enquanto o site está aberto.

## Onde ficam as coisas

| Pasta ou arquivo | Conteúdo |
|---|---|
| `dados/anp.sqlite` | Base derivada da ANP. Pode ser apagada e refeita com Atualizar base |
| `dados/usuario.sqlite` | Seus clusters |
| `dados/clusters.json` e `dados/backups/` | Cópia legível dos clusters a cada mudança (últimas 30) |
| `dados/brutos/` | Arquivos da ANP compactados, para refazer a base sem baixar (`anp reprocessar`) |
| `dados/exportacoes/` | Planilhas geradas pela linha de comando ou por agentes |
| `dados/logs/atualizacao.log` | Registro das atualizações disparadas pelo site ou por agentes |
| `src/anp_producao/` | Código (leitura da ANP, banco, consultas, site, linha de comando, MCP) |
| `tests/` | Testes automáticos (`uv run pytest`) |
| `docs/` | Desenho da solução e decisões |

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Porta 8765 ocupada | O site provavelmente já está aberto; o script só abre o navegador. Para outra porta use `ANP_PORTA=8770 uv run anp servir` |
| Atualização falhou | Veja **Histórico** no topo do site ou `dados/logs/atualizacao.log`. Arquivos com problema não derrubam os outros |
| A ANP mudou o layout da página | A atualização avisa que não achou os links; a base atual fica intacta |
| Quero refazer tudo | Em **Histórico**, clique em Atualização completa, ou rode `uv run anp atualizar --completa` |
