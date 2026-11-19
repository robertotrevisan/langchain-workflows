# Iniciar um projeto a partir dos modelos 05 a 07

Este guia diz como comecar um projeto real copiando um dos modelos 05, 06 ou 07: qual escolher, o que copiar, o que trocar primeiro e em que ordem levar o projeto a producao. O que cada modelo faz esta no README de cada pasta e em [padroes-workflow.md](padroes-workflow.md).

## 1. Escolher o modelo

| O projeto precisa de | Comece pelo |
| --- | --- |
| diagnostico ou analise, sem agir no ambiente | `05-investigacao` |
| classificar entradas variadas antes de gastar LLM, e recomendar um procedimento | `06-triagem-investigacao` |
| executar acoes no ambiente, com limites, aprovacao humana e verificacao | `07-agente` |
| agente com custo controlado | `07-agente`, depois traga o roteador do 06 para a frente (secao 6) |

Na duvida, comece pelo menor modelo que resolve o problema. Passar de 05 para 07 depois e mais facil do que tirar autonomia de um agente que ja esta em uso.

## 2. Copiar

Copie a pasta inteira para um repositorio novo, sem `.venv`, `.env` e `__pycache__`:

```bash
rsync -a --exclude .venv --exclude .env --exclude __pycache__ \
  07-agente/ ../meu-agente/
cd ../meu-agente
git init
```

Depois, no `pyproject.toml`:

- troque `name` e `description`;
- confira `known-first-party` em `[tool.ruff.lint.isort]` se renomear ou criar modulos em `src/`;
- rode `uv sync` para gerar um `uv.lock` com o nome novo.

Confirme que a copia funciona antes de mudar qualquer coisa:

```bash
uv run pytest
uv run ruff check src tests
```

Os testes nao chamam API de LLM, entao passam sem chave.

## 3. O que cada arquivo e

Cada arquivo de `src/` cai em uma de tres categorias.

**Mantenha (infraestrutura).** Funciona em qualquer projeto do mesmo formato. Ajuste so se o projeto exigir.

| Arquivo | Conteudo |
| --- | --- |
| `app.py` | endpoints, `lifespan`, processamento em background |
| `config.py` | leitura e validacao do ambiente na inicializacao |
| `autenticacao.py` | tokens por rota (`WEBHOOK_TOKEN`; no 07, tambem `APROVACAO_TOKEN`) |
| `registro.py` | deduplicacao e estado do incidente (no 07, a maquina de estados com aprovacao, reentrada, carencia e supressao) |

**Mantenha se a entrada for alerta.** Se o projeto recebe alertas do Alertmanager ou do CloudWatch, use como estao. Se nao (ChatOps, revisao de PR, ticket), troque.

| Arquivo | Conteudo |
| --- | --- |
| `webhooks.py` | adaptadores de payload para `Incidente` |
| `contratos.py`, secoes 1 a 3 | `Incidente`, payloads das origens, dados das fontes e limites de tamanho |

**Troque (dominio).** E exemplo; cada projeto escreve o seu.

| Arquivo | 05 | 06 | 07 |
| --- | --- | --- | --- |
| fontes de dados | `fontes.py` | `fontes.py` | consultas em `ambiente.py` |
| acoes | - | - | acoes em `ambiente.py` |
| prompts | `workflow.py` | `workflow.py`, `triagem.py` | `SYSTEM_AGENTE` em `agente.py` |
| catalogo | - | `runbooks/catalogo.json` | lista de ferramentas em `criar_ferramentas` |
| dados ficticios | `cenarios/` | `cenarios/` | `cenarios/` |
| exemplos de entrada | `entradas/` | `entradas/` | `entradas/` |

## 4. Trocar o dominio, nesta ordem

### 4.1. Contrato de entrada

Se a entrada nao for alerta, comece por aqui, porque tudo depois depende dela:

1. Reescreva `Incidente` (ou crie o contrato equivalente) em `contratos.py`, com `max_length` em todo campo de texto que entra no prompt.
2. Troque os adaptadores de `webhooks.py` e os endpoints de `app.py`.
3. Mantenha a chave de deduplicacao no formato `origem:identificador`.

Se a entrada for alerta, pule esta etapa.

### 4.2. Fontes de dados

Escreva uma classe real com os mesmos metodos da simulada. O workflow e o agente so conhecem a assinatura.

- 05 e 06: implemente o `Protocol` `Fontes` de `fontes.py` (`consultar_metricas`, `consultar_logs`, `consultar_deploys`) e passe a classe nova para `Investigacao` em `app.py`.
- 07: as consultas sao os quatro primeiros metodos de `AmbienteSimulado`. Separe-as numa classe real e mantenha o contrato de retorno (`list[Metrica]`, `list[LinhaLog]`, `list[Deploy]`, `dict`).

Regras que valem para a classe real:

- levante `FonteIndisponivel` quando a fonte nao responder ou nao tiver dados; o 05 trata isso como lacuna (`parcial` ou `inconclusivo`), e o 07 devolve o erro ao modelo como resultado da ferramenta;
- valide o nome do servico antes de usa-lo em URL, query ou caminho, porque ele vem de um payload externo (`NOME_SERVICO`);
- limite o volume devolvido (pontos de serie, linhas de log): tudo vai para o prompt.

Mantenha a classe simulada para os testes.

### 4.3. Acoes (so 07)

As acoes sao os tres ultimos metodos de `AmbienteSimulado`. Para cada acao real:

1. Escreva o metodo com a mesma forma: recebe o servico e os parametros, executa, devolve uma frase curta do que foi feito e **nao** diz se resolveu. Quem decide se resolveu e a consulta de metricas seguinte.
2. Levante `AcaoInvalida` quando o estado nao permitir a acao (sem versao anterior, replicas fora do limite).
3. Registre a ferramenta em `criar_ferramentas` e o nome em `ACOES`.
4. Decida se ela exige aprovacao: inclua o nome em `ACOES_COM_APROVACAO`. Comece com todas as acoes nessa lista e retire uma de cada vez, depois de ver o agente usa-la bem.

Os limites ficam no codigo, nao no prompt: `PoliticaDeAcoes` (servico do incidente, argumentos validos, uma acao por resposta, `MAX_ACOES`), `ModelCallLimitMiddleware` e `validar_desfecho`. Se acrescentar uma regra, acrescente ali. A politica precisa continuar antes do `HumanInTheLoopMiddleware` na lista de middlewares.

### 4.4. Prompts e catalogo

So depois de as ferramentas existirem:

- reescreva os prompts com o vocabulario do dominio, mantendo o que o codigo confere (desfecho, catalogo fechado, rotas fixas);
- 06: troque `runbooks/catalogo.json`; o `quando_usar` de cada item e o que o modelo le para escolher;
- 06: ajuste `SYSTEM_ROTEADOR` e os tres casos de `tests/test_roteador_llm.py`.

### 4.5. Cenarios e testes

Reescreva `cenarios/` e `entradas/` com casos do dominio novo e ajuste os testes. Os cenarios simulados continuam uteis depois que as fontes reais existem: sao eles que permitem testar sem ambiente.

- 05 e 06: `ModeloFalso` em `tests/conftest.py` devolve respostas fixas.
- 07: `ModeloRoteirizado` recebe a sequencia de tool calls que o modelo faria (`consultar`, `agir`, `concluir`). Escreva um roteiro para cada caminho que importa: resolve, nao resolve, acao bloqueada, aprovacao rejeitada, limite esgotado.

## 5. Rodar com um modelo real

```bash
cp .env.example .env   # preencha a chave e MODELO
uv run fastapi dev src/app.py
```

`MODELO` usa o formato `provider:modelo` do `init_chat_model`. Para OpenRouter, defina `OPENAI_BASE_URL` e use o id do gateway (`openai:anthropic/claude-sonnet-5`); o `.env.example` mostra o bloco.

Antes de confiar no projeto, rode cada cenario com o modelo real e leia o resultado inteiro (no 07, a `timeline`). Os testes provam que o codigo trata cada caminho; so a execucao real mostra se o modelo escolhe o caminho certo.

## 6. Combinar modelos

Para por a triagem do 06 na frente do agente do 07:

1. Copie `triagem.py` e `Roteamento` (de `contratos.py`) do 06 para o projeto do 07. `runbooks.py` e o catalogo so sao necessarios se o agente tambem for recomendar runbook.
2. Em `Triagem`, troque `_investigar`, que hoje chama a `Investigacao` do 05 e a recomendacao, por uma chamada a `Agente.iniciar`. Tire o import de `workflow`.
3. Mantenha `ruido` e `humano` como estao: registram e encerram, sem chamar o agente.
4. Ajuste o registro do 07 para guardar a decisao do roteador quando a rota nao for o agente.
5. Use `MODELO_ROTEADOR` com um modelo mais barato que o do agente.

## 7. Levar a producao

Os modelos deixam de fora, de proposito, o que depende da infraestrutura de cada time. Faca nesta ordem; os primeiros itens bloqueiam rodar mais de uma replica ou sobreviver a um deploy.

| # | Item | Hoje | O que fazer |
| --- | --- | --- | --- |
| 1 | Estado persistente | `Registro` e o checkpointer do 07 em memoria | registro em banco ou Redis; `PostgresSaver` no lugar do `InMemorySaver` |
| 2 | Fila externa | `BackgroundTasks` no proprio processo | SQS, Redis ou similar; o endpoint so enfileira |
| 3 | Identidade na aprovacao (07) | `aprovador` e um texto informado por quem chama | aprovador vindo do login (SSO); `APROVACAO_TOKEN` so para servicos |
| 4 | Assinatura do SNS | nao validada | validar o certificado e a assinatura; confirmar a inscricao |
| 5 | Fallback de modelo | um modelo so | `.with_fallbacks()` para outro modelo ou provider |
| 6 | Avaliacao de qualidade | 3 casos no 06 | conjunto de casos com resultado esperado, rodado a cada mudanca de prompt ou modelo |
| 7 | Observabilidade | logging com o id do incidente; tracing via `LANGSMITH_*` | metricas de custo, latencia, desfechos e acoes por servico |
| 8 | Notificacao | rota `humano` e desfecho `escalado` so registram | Slack, PagerDuty ou ticket |

No 07, mantenha `ACOES_COM_APROVACAO` cheio ate os itens 1, 3 e 6 estarem prontos.

## 8. Correcoes nos modelos originais

O codigo comum existe em tres copias neste repositorio (ADR 001). Um projeto derivado nao precisa acompanha-las. Mas se voce corrigir um problema em `webhooks.py`, `autenticacao.py` ou nas secoes 1 a 3 de `contratos.py` num projeto derivado, verifique se a correcao vale para 05, 06 e 07 aqui.

Se muitos projetos nascerem destes modelos, extraia a base comum para um pacote compartilhado, ou transforme o 07 num template (copier ou cookiecutter). Essa decisao merece um ADR novo.
