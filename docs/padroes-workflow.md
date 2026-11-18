# Padroes de workflow LLM para AIOps

Este documento compara os quatro projetos do repositorio como padroes reutilizaveis e define o roadmap dos modelos 05 a 07, que levam esses padroes a um agente de AIOps. A decisao esta registrada em [ADR 001](adrs/001-evolucao-para-agente-aiops.md).

Os exemplos 01 a 04 sao **workflows**, nao agentes: a trajetoria e definida no codigo. O modelo produz conteudo, mas nao escolhe ferramentas, nao repete etapas e nao decide quando parar. No routing, o modelo escolhe um ramo, mas somente entre ramos fixos.

## Visao geral

| | 01 sequencial | 02 contratos | 03 routing | 04 paralelo |
| --- | --- | --- | --- | --- |
| Fluxo | A -> B -> C | A -> B -> C tipado | classificar -> 1 de N | N ramos -> consolidar |
| Primitiva | `prompt \| model \| StrOutputParser` | `with_structured_output(Model, method="json_schema")` | `RunnableBranch` | `RunnableParallel` |
| Dado entre etapas | `str` | instancia Pydantic, serializada com `model_dump_json()` | `dict` com a chave `rota` | `dict` nomeado, depois validado com `AnalisesParalelas.model_validate` |
| Chamadas de LLM por requisicao | 3 | 3 | 1 + (0 ou 1) | 3 + 1 |
| Latencia | soma das 3 | soma das 3 | roteador + 1 ramo | ramo mais lento + consolidacao |
| Provider | Gemini (`init_chat_model`) | Gemini (`init_chat_model`) | OpenAI + Anthropic | OpenAI |
| Interface | script | FastAPI `POST /analises` | FastAPI `POST /triagens` | FastAPI `POST /analises` |
| Saida final | texto livre | `AnaliseIncidente` | `Resultado` (resposta em texto livre) | `Resultado` (tudo estruturado) |

O 02 nao e um padrao de fluxo separado. Ele e o 01 com contratos. O mesmo uso de contratos aparece no roteador do 03 e em todos os ramos do 04. Na pratica, **qualquer padrao destinado a producao deve usar contratos**.

## 01 — Sequencial (prompt chaining)

```text
alerta -> evidencias -> hipotese -> plano de verificacao
```

**Quando usar:** a tarefa se decompoe em transformacoes dependentes, e cada etapa fica melhor com um prompt focado do que com um prompt unico que faz tudo.

**Casos de uso em AIOps:**

- alerta bruto -> evidencias -> hipotese -> plano de verificacao (o caso do exemplo);
- logs de um job com falha -> resumo -> classificacao do erro -> sugestao de runbook;
- timeline de incidente -> rascunho de postmortem -> lista de action items.

**Trade-offs:**

- cada etapa soma latencia e custo;
- um erro no inicio contamina as etapas seguintes;
- com `str` entre as etapas, a etapa seguinte depende do formato livre que a anterior escreveu.

**Quando nao usar:** se as etapas nao dependem umas das outras (use 04) ou se o tipo da entrada define todo o tratamento (use 03).

## 02 — Contratos (structured output)

```text
AlertaEntrada -> Evidencias -> Hipotese -> PlanoVerificacao -> AnaliseIncidente
```

**Quando usar:** sempre que a saida alimenta outro sistema, como ticket no ITSM, mensagem no Slack, automacao, dashboard ou outro workflow. Use tambem quando uma etapa precisa de campos especificos da etapa anterior.

**O que o contrato garante:** formato, campos, tipos, `Literal` e restricoes do `Field` (`min_length`, `ge`). Garante tambem um schema OpenAPI gerado.

**O que nao garante:** que o conteudo esteja correto. Um JSON valido ainda pode trazer uma hipotese errada.

**Detalhes do exemplo que valem como padrao:**

- `Field(description=...)` funciona como instrucao para o modelo, porque a descricao entra no JSON Schema enviado a ele;
- `Literal[...]` restringe categorias, como `categoria` em `Evidencia`;
- a etapa seguinte recebe JSON validado, nao texto livre.

## 03 — Routing

```text
Solicitacao -> roteador (Roteamento.destino) -> RunnableBranch -> 1 tratativa
```

**Quando usar:** entradas heterogeneas em que cada tipo pede um prompt, um modelo ou um tratamento diferente. Use tambem para otimizar custo, mandando o simples para um modelo barato e o complexo para um modelo forte.

**Casos de uso em AIOps:**

- triagem de solicitacoes DevOps: pipeline, IaC ou release (o caso do exemplo);
- ChatOps: identificar a intencao da mensagem e despachar para o handler certo;
- roteamento de alertas por dominio (rede, banco, aplicacao) para prompts especializados;
- decidir se um alerta segue para analise automatica ou direto para um humano.

**Trade-offs:**

- o roteador e um ponto unico de erro semantico, porque uma rota valida pode estar errada;
- o fallback precisa existir e precisa ser util, mesmo que seja apenas "esclarecer";
- os ramos devem devolver o mesmo contrato (`Resultado`) para que o consumidor nao precise saber qual ramo rodou.

**Cuidados no exemplo:**

- `provider` e `modelo` estao duplicados: aparecem na construcao do modelo e de novo dentro de cada `tratar_*`, e podem divergir;
- a classificacao nao traz justificativa nem confianca.

## 04 — Paralelo (fan-out / fan-in)

```text
Incidente -> RunnableParallel(pipeline, infraestrutura, aplicacao) -> consolidacao
```

**Quando usar:** varias analises independentes sobre a mesma entrada, em que o valor esta em comparar perspectivas, ou checagens independentes que podem rodar ao mesmo tempo.

**Casos de uso em AIOps:**

- analise de incidente por varias perspectivas (o caso do exemplo);
- analisar logs, metricas e historico de deploys em paralelo e depois correlacionar;
- varios avaliadores independentes sobre a mesma mudanca, como risco, seguranca e custo de um PR de Terraform;
- votacao ou consenso entre execucoes para reduzir variancia.

**Trade-offs:**

- a concorrencia reduz o tempo de parede, mas nao reduz o numero de chamadas nem o custo;
- `RunnableParallel` falha por inteiro se um ramo falhar, sem resultado parcial;
- a consolidacao continua probabilistica e precisa ser instruida a preservar divergencias.

**Cuidados no exemplo:**

- as tres analises sao opinioes sobre o mesmo texto de entrada: nenhum ramo busca dados, entao nenhum descobre algo que ja nao esteja na `descricao`;
- o campo `perspectiva` de cada `Analise` depende de o modelo repetir o valor certo e nao e conferido contra a chave do ramo;
- quatro instancias de `ChatOpenAI` sao criadas para o mesmo modelo.

## Combinando padroes

Os padroes sao blocos e se compoem como Runnables:

- **routing -> paralelo:** o roteador identifica o tipo de incidente e cada rota dispara seu proprio conjunto de analises paralelas;
- **paralelo com ramos sequenciais:** cada perspectiva e uma pequena cadeia evidencias -> hipotese;
- **sequencial com routing no meio:** extrair evidencias, rotear pelo dominio detectado e gerar o plano especializado.

Mantenha contratos em todas as fronteiras entre blocos.

Num agente de AIOps, os dois padroes ocupam etapas diferentes:

| Etapa do agente | Padrao |
| --- | --- |
| Triagem: o que e isso e quem trata | 03 routing |
| Investigacao: juntar evidencias | 04 paralelo, com ferramentas |
| Sintese | consolidacao do 04 |
| Acao e verificacao | agente (modelo 07) |

## Lacunas para producao

O que os exemplos 01 a 04 omitem de proposito. Os exemplos continuam didaticos; a coluna "Resolvido em" indica qual modelo novo trata cada lacuna.

| Lacuna | Situacao nos exemplos | Resolvido em |
| --- | --- | --- |
| Entrada real | JSON didatico, sem webhook | 05 |
| Busca de dados | nenhum ramo consulta fontes | 05 |
| Configuracao de modelo | IDs de modelo fixos no codigo | 05 |
| Validacao de ambiente | 01/02 fazem `raise SystemExit` no import se faltar chave, o que derruba o servidor; 03/04 nao validam | 05 |
| Retry e timeout | nenhum `max_retries`, `timeout` ou `.with_retry()` | 05 |
| Fallback de modelo | nenhum `.with_fallbacks()` para outro modelo ou provider | pendente; o 05 usa `.with_fallbacks()` so para resultado parcial |
| Tratamento de erros HTTP | qualquer falha do LLM vira 500 | 05 |
| Resultado parcial | um ramo com falha derruba a requisicao (04) | 05 |
| Limites de entrada | sem `max_length`, sem controle de custo nem de prompt injection | 05 |
| Concorrencia | endpoints sincronos (`def`) com `.invoke()` | 05 |
| Observabilidade | `print()`, sem logging estruturado nem tracing | 05 (logging com `incidente=<id>`; tracing via variaveis `LANGSMITH_*`) |
| Testes | nenhum; use fake chat models do `langchain_core` para testar o fluxo sem chamar a API | 05 |
| Confianca do roteador | `Roteamento` sem justificativa nem confianca (03) | 06 |
| Recomendacao de acao | nenhuma | 06 |
| Acao e verificacao | nenhuma | 07 |
| Autenticacao | nenhuma | 05 (`WEBHOOK_TOKEN` opcional); assinatura do SNS fora do escopo |
| Avaliacao de qualidade | nenhuma; o contrato valida a forma, nao o conteudo | fora do escopo; ver roadmap |

## Roadmap: modelos 05 a 07

Tres modelos incrementais prontos para uso real, cada um utilizavel sozinho como boilerplate. As fontes de dados sao ficticias. Cada modelo e um projeto `uv` independente e copia o codigo comum do anterior.

| | 05 investigacao | 06 triagem + investigacao | 07 agente |
| --- | --- | --- | --- |
| Entrada | webhook | webhook | webhook + reentrada |
| Quem decide o fluxo | codigo | codigo (o roteador escolhe 1 de N) | modelo |
| Ferramentas | consultas fixas por ramo | as do 05 | o modelo escolhe consultas e acoes |
| Saida | diagnostico | diagnostico + runbook recomendado | acao executada e verificada |
| Age no ambiente | nao | nao | sim, com limites |
| Estende | 04 | 03 + 05 | sai de workflow |
| Conceito novo | entrada real + dados | triagem com confianca | autonomia com limites |

### Base comum: webhook e contrato de entrada

Construida no 05 e copiada para 06 e 07.

**Endpoints** *(proposta)*:

| Metodo e rota | Funcao |
| --- | --- |
| `POST /webhooks/alertmanager` | recebe o payload do Alertmanager (`version: "4"`, lista `alerts[]`) |
| `POST /webhooks/cloudwatch` | recebe a notificacao SNS do alarme do CloudWatch |
| `POST /webhooks/generico` | recebe o contrato `Incidente` diretamente, para outras origens e testes |
| `GET /incidentes/{id}` | consulta o status e o resultado do processamento |

**Regras:**

- **Normalizacao.** Cada adaptador converte o payload da origem para `Incidente`. Do adaptador em diante, nada no workflow depende da origem.
- **Alertmanager.**
  - Um POST pode trazer varios alertas agrupados; cada item de `alerts[]` vira um `Incidente`.
  - `status: resolved` nao dispara investigacao. No 05 e no 06 ele so atualiza o registro; no 07 ele aciona a reentrada.
- **CloudWatch.**
  - O alarme chega via SNS. Uma mensagem `SubscriptionConfirmation` precisa ser confirmada acessando a `SubscribeURL`, o que no ambiente ficticio pode ser simulado ou apenas registrado.
  - O alarme vem como string JSON no campo `Message` de uma mensagem `Notification`.
  - `NewStateValue` define o status: `ALARM` vira `firing` e `OK` vira `resolved`.
- **Responder rapido.** O endpoint valida, deduplica, devolve `202` com o `id` e processa em background. Esperar a analise do LLM faz a origem reenviar o mesmo alerta.
- **Deduplicacao.** Um evento com `fingerprint` ja em processamento nao abre novo processamento.

**Contrato `Incidente`** *(proposta)*:

| Campo | Tipo | Origem no Alertmanager | Origem no CloudWatch |
| --- | --- | --- | --- |
| `id` | `str` | gerado | gerado |
| `fingerprint` | `str` | `fingerprint` | `AlarmArn` |
| `origem` | `Literal["alertmanager", "cloudwatch", "generico"]` | fixo | fixo |
| `status` | `Literal["firing", "resolved"]` | `status` | `NewStateValue` |
| `titulo` | `str` | `labels.alertname` | `AlarmName` |
| `descricao` | `str` (com `max_length`) | `annotations.description` ou `summary` | `NewStateReason` |
| `servico` | `str \| None` | `labels.service` ou `labels.job` | dimensoes de `Trigger` |
| `severidade` | `Literal["info", "warning", "critical"]` | `labels.severity` | derivada do nome ou de tags do alarme |
| `labels` | `dict[str, str]` | `labels` | dimensoes de `Trigger` |
| `inicio` | `datetime` | `startsAt` | `StateChangeTime` |

**Fontes ficticias:**

- Funcoes com a mesma assinatura que uma fonte real teria: `consultar_metricas(servico, janela)`, `consultar_logs(servico, janela)` e `consultar_deploys(servico, janela)`.
- Os dados vem de cenarios em arquivo, como `cenarios/checkout-pool-conexoes.json`, que contam uma historia coerente entre metricas, logs e deploys.
- Trocar uma fonte ficticia por uma real deve exigir mudar so a implementacao da funcao.

### 05 — Investigacao

**Status: implementado** em `05-investigacao/`. O README do projeto descreve endpoints, cenarios e status.

```text
webhook -> Incidente -> RunnableParallel(metricas, logs, deploys) -> consolidacao -> Diagnostico
                         cada ramo: consulta a fonte ficticia -> LLM analisa o resultado
```

**Entrega:**

- a base comum completa: webhook, adaptadores, `Incidente`, deduplicacao e processamento em background;
- ramos que consultam dados antes de chamar o LLM, cada um com uma fonte e uma perspectiva;
- resultado parcial: um ramo que falha vira `Analise(status="falha")` via `.with_fallbacks(..., exception_key="erro")`, e a consolidacao continua com os demais;
- `origem_falha` separa "a fonte nao tinha dados" (`inconclusivo`) de "o modelo ou o codigo falhou" (registro em `falha`, com nova tentativa no proximo disparo);
- `perspectiva` definida pelo codigo, nao pelo modelo;
- modelo e timeouts configuraveis por variavel de ambiente, com `max_retries` e `timeout`;
- validacao de ambiente na inicializacao do app, sem `SystemExit` no import;
- logging estruturado com o `id` do incidente;
- testes com fake chat model e cenarios ficticios.

**Nao faz:** triagem, recomendacao de acao, execucao de acao.

### 06 — Triagem + investigacao

**Status: implementado** em `06-triagem-investigacao/`. O README do projeto descreve rotas, limiar e avaliacao do roteador.

```text
webhook -> Incidente -> roteador (destino, confianca, justificativa)
                          +--> investigar  -> investigacao do 05 -> recomendacao de runbook
                          +--> ruido       -> registra e encerra
                          +--> humano      -> registra para escalacao
```

**Entrega:**

- `Roteamento` com `destino`, `confianca` e `justificativa`; confianca abaixo de `LIMIAR_CONFIANCA` vai para `humano`, preservando o destino original na `Tratativa`;
- rota `ruido` para alertas informativos, heartbeat ou flapping, o que evita gastar a investigacao; o roteador recebe quantos incidentes com o mesmo `origem:fingerprint` foram abertos na ultima hora;
- um catalogo de runbooks ficticio; a recomendacao escolhe um id do catalogo, e o codigo descarta ids inventados;
- um contrato de saida unico para todas as rotas (`Tratativa`);
- `MODELO_ROTEADOR` separado de `MODELO`, para usar um modelo mais barato na triagem;
- testes da logica de rotas com modelo falso, e uma avaliacao do roteador com o modelo real (`RODAR_LLM=1`) sobre tres entradas com destino esperado.

**Nao faz:** execucao de acao.

### 07 — Agente

```text
webhook -> Incidente -> agente (tool-calling em loop)
                          observar -> hipotese -> agir -> verificar -> repetir
                          ferramentas: consultas do 05 + acoes do ambiente simulado
                          termina em: resolvido | limite esgotado -> escala | aguardando aprovacao
```

**Entrega:**

- **Ambiente simulado com estado.** Acoes como `reiniciar_servico`, `escalar_replicas` e `reverter_deploy` alteram o estado do cenario, e as consultas seguintes refletem a mudanca. Alguns cenarios resistem a primeira acao.
- **Dois ciclos:**
  - loop interno: o agente itera ate verificar a resolucao;
  - reentrada externa: um novo evento do webhook com o mesmo `fingerprint` retoma o mesmo incidente a partir do estado persistido.
- **Limites obrigatorios:**
  - maximo de iteracoes por incidente;
  - lista fechada de acoes permitidas;
  - aprovacao humana para acoes marcadas como destrutivas;
  - escalacao ao esgotar os limites;
  - janela de supressao para alertas causados pela propria acao.
- **Timeline auditavel** de cada passo: consulta, hipotese, acao, resultado e verificacao.
- **Implementacao:** `create_agent` do LangChain 1.x com checkpointer por incidente e middleware de aprovacao humana; migrar para LangGraph explicito se o fluxo exigir. *(premissa do ADR 001)*

### Fora do escopo dos tres modelos

- fontes de dados reais (Prometheus, CloudWatch Logs, CI);
- validacao da assinatura das mensagens SNS;
- avaliacao automatizada da qualidade dos diagnosticos;
- fila externa (SQS, Redis): o background roda no proprio processo.

## Referencias

- [ADR 001](adrs/001-evolucao-para-agente-aiops.md)
- `01-workflow-sequencial/README.md`
- `02-outputs-contratos/README.md`
- `03-routing-workflow/README.md`
- `04-parallel-workflow/README.md`
