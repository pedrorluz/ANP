# Produção ANP por campo: desenho

Data: 23/11/2026. Pedido do Pedro, aprovado com todas as sugestões ("pode implementar").

## Intenção

Ver a produção mensal de petróleo, gás e água dos campos brasileiros com base nos dados da ANP, agrupar campos em clusters salvos, salvar os dados vistos e atualizar a base com um botão que mostra de quando é o último dado. Uso local (Mac ou Windows) e por agentes locais (Hermes, Claude e afins).

## Objetivos

* Clusters persistentes, manuais e por regra.
* Caixa seletora de variável (óleo, gás, água e derivados) com unidades.
* Exportação em CSV e Excel, imagem do gráfico, backup de clusters.
* Botão de atualização com progresso, data da publicação da ANP e do último mês.
* As nove melhorias aprovadas: unidades e petróleo = óleo + condensado; boe, RGO, corte de água e injeção; clusters por regra; produção líquida por participação; comparação e composição; indicadores; detalhe por poço e instalação; exportações; registro de atualizações e revisões.
* Acesso por agentes (MCP, linha de comando com JSON, API documentada).

## Fora do escopo

* Acesso multiusuário, login e hospedagem em servidor.
* Previsões ou curvas de declínio ajustadas.
* Histórico de participações (a ANP só publica a vigente).

## Abordagens consideradas

| Abordagem | Troca | Decisão |
|---|---|---|
| Página única hospedada (artifact) buscando a ANP pelo navegador | Sem servidor, mas o navegador não processa 630 MB nem guarda milhões de linhas com segurança | Descartada |
| Streamlit ou Dash | Rápido de montar, mas pouco controle visual e sem API limpa para agentes | Descartada |
| FastAPI + SQLite + página estática + CLI + MCP | Mais código, mas uma camada única de serviços atende site, terminal e agentes | Escolhida |
| DuckDB no lugar de SQLite | Mais compacto e rápido em agregações, mas um processo escritor bloqueia os outros (site, MCP e atualização rodam em processos diferentes) | SQLite em WAL |

## Fonte de dados (levantada em 23/11/2026)

* Página: gov.br/anp, dados abertos, Fase de Desenvolvimento e Produção. 127 CSVs de produção por poço (mar em arquivos anuais ou multianuais desde 1941; terra trimestral desde 2016 e semestral antes), 632 MB, 5,3 milhões de linhas.
* Cadastros: `extracao-campo.csv` (operador, situação) e `extracao-campo-concessionarios.csv` (participações vigentes).
* Volumes do mês em m³ (gás em mil m³). Últimos dados: set/2026, publicados em 03/11/2026.
* Variações reais tratadas: UTF-8 com BOM e Windows-1252; cabeçalho com e sem colchetes; ordem de colunas diferente (terra 2022 T4 e 2025 T4); linhas inteiras embrulhadas em aspas; mês "out/2025"; poço em dois campos no mesmo mês.
* Servidor: HEAD devolve 403; sem ETag nem Last-Modified; aceita Range. Mudança detectada por tamanho (GET de 1 byte) e confirmada por SHA-256.
* Lacunas: terra sem abr/2026 e jul/2026, e mar/2026 parcial. Tratadas como vazio.
* Complemento (23/11/2026): a mesma página publica a "Produção por zona", mensal desde out/2014, com média diária por poço e zona. Nos meses completos bate com o arquivo por poço em até 0,1% (petróleo e gás); somar a mar/2026 os poços que faltavam reconstitui o mês com 0,01% de diferença. A atualização acrescenta só os poços que faltam, nos meses sem dado, nos meses parciais e nos meses da janela de revisão em que o arquivo por poço fica mais de 0,5% abaixo do por zona. Instalação herdada do mês vizinho. Detalhes em `complemento.py`.

## Decisões

* Dois bancos: `anp.sqlite` derivado e descartável; `usuario.sqlite` com clusters, mais JSON de backup a cada mudança.
* Atualização em processo separado com trava de arquivo, progresso gravado no banco, uma transação por arquivo, revisões registradas quando meses existentes mudam, arquivos que saem da página removidos só se a página vier plausível.
* Janela de revisão: ano corrente e anterior sempre baixados; antigos só se o tamanho mudar; "atualização completa" baixa tudo.
* Agregados por campo-mês e instalação-campo-mês para consultas instantâneas; poço vem da tabela detalhada.
* Paleta do gráfico com cobre primeiro, validada para daltonismo no fundo navy.
* Servidor só em 127.0.0.1, verificação de Host e cabeçalho obrigatório para alterações (proteção contra CSRF de outros sites).

## Deixado para depois

* Mapeamento de campos renomeados ou unitizados ao longo do tempo.
* Curvas de declínio e previsões.
* Agendamento automático da atualização (hoje é sob demanda ou por agente).
