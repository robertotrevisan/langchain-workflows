# Triagem e investigacao de incidentes

Segundo modelo pronto para uso real do roadmap (ver `docs/padroes-workflow.md` e `docs/adrs/001-evolucao-para-agente-aiops.md` na raiz do repo). Estende o `05-investigacao` com uma triagem na entrada: antes de gastar quatro chamadas de LLM numa investigacao, um roteador decide se o alerta merece investigacao, e ruido ou precisa de um humano. Quando investiga, recomenda um runbook de um catalogo fechado.

O projeto **nao executa acoes**. As fontes de dados sao ficticias, como no 05.

```text
Alertmanager / CloudWatch (SNS) / generico
    |
    v
adaptador -> Incidente -> registro (deduplicacao, historico) -> 202
                              |
                              v  background
            roteador: destino, confianca, justificativa
              |   confianca < LIMIAR_CONFIANCA -> humano
              v
            RunnableBranch
              +--> investigar -> investigacao do 05 -> recomendacao de runbook
              +--> ruido      -> registra e encerra
              +--> humano     -> registra para escalacao
                              |
                              v
                         Tratativa -> GET /incidentes/{id}
```

## O que muda em relacao ao 05

| Arquivo | Mudanca |
| --- | --- |
| `src/triagem.py` | novo: roteador, `RunnableBranch`, tratativas e recomendacao |
| `src/runbooks.py`, `runbooks/catalogo.json` | novo: catalogo fechado de runbooks |
| `src/contratos.py` | `Roteamento`, `Runbook`, `EscolhaRunbook`, `Recomendacao`, `Tratativa`; o registro guarda `tratativa` em vez de `diagnostico` |
| `src/registro.py` | historico por `origem:fingerprint` para contar disparos recentes (flapping) |
| `src/config.py` | `MODELO_ROTEADOR`, `LIMIAR_CONFIANCA`, `CATALOGO_RUNBOOKS` |
| `src/app.py` | monta a `Triagem` e chama `triar()` em background |
| `src/workflow.py`, `fontes.py`, `webhooks.py`, `autenticacao.py` | iguais ao 05 (`criar_modelo` aceita um modelo alternativo) |

## Executar

```bash
cp .env.example .env   # preencha OPENAI_API_KEY e, fora do ambiente local, WEBHOOK_TOKEN
uv sync
uv run fastapi dev src/app.py
```

Os endpoints, a autenticacao e os payloads sao os mesmos do 05. Exemplos por rota esperada:

```bash
# investigar: checkout-api com 18% de 5xx (e pagamentos-worker, no mesmo POST)
curl -X POST -H 'Content-Type: application/json' \
  --data @entradas/alertmanager.json http://127.0.0.1:8000/webhooks/alertmanager

# ruido: Watchdog, o alerta que sempre dispara
curl -X POST -H 'Content-Type: application/json' \
  --data @entradas/alertmanager-watchdog.json http://127.0.0.1:8000/webhooks/alertmanager

# humano: lentidao geral, varios sintomas, sem servico
curl -X POST -H 'Content-Type: application/json' \
  --data @entradas/generico-ambiguo.json http://127.0.0.1:8000/webhooks/generico

curl http://127.0.0.1:8000/incidentes/<id>
```

Testes:

```bash
uv run pytest                                          # modelo falso, sem API
RODAR_LLM=1 uv run pytest tests/test_roteador_llm.py -v  # roteador com o modelo real
```

O segundo comando e uma avaliacao: chama o modelo configurado para as tres entradas acima e confere o destino esperado. Rode depois de mudar o prompt do roteador ou trocar `MODELO_ROTEADOR`.

## Rotas

| Rota | Quando | O que faz | Chamadas de LLM |
| --- | --- | --- | --- |
| `investigar` | falha real em um servico identificado | investigacao do 05 e recomendacao de runbook | 1 + 4 + 1 |
| `ruido` | heartbeat, teste, informativo, instavel sem impacto | registra a decisao e encerra | 1 |
| `humano` | varios servicos, servico nao identificado, informacao insuficiente, ou confianca abaixo do limiar | registra para escalacao | 1 |

A `Tratativa` guarda a rota executada (`rota`), a decisao original do roteador (`roteamento`, com `confianca` e `justificativa`) e o `motivo`. Quando a confianca fica abaixo do limiar, `rota` e `humano` e `roteamento.destino` mostra o que o roteador teria feito.

O roteador recebe tambem quantos incidentes com o mesmo `origem:fingerprint` foram abertos na ultima hora. Varios disparos e resolucoes seguidos indicam alerta instavel.

Um incidente classificado como `ruido` ou `humano` continua aberto ate o `resolved`: reenvios da origem sao `duplicado` e nao passam de novo pelo roteador.

## Recomendacao de runbook

- So acontece na rota `investigar` e quando o diagnostico tem consolidacao (`completo` ou `parcial`).
- O modelo recebe o catalogo inteiro e devolve um `runbook_id` ou `null`.
- O codigo confere o id contra o catalogo: um id inventado vira `runbook: null` com confianca 0.
- Uma falha na recomendacao nao descarta o diagnostico; a recomendacao fica `null` com o tipo do erro.
- A `confianca` da recomendacao e devolvida como veio; quem consome decide o que fazer com valores baixos.

Para adicionar um runbook, inclua um item em `runbooks/catalogo.json` com `id` unico, `quando_usar` claro (e o que o modelo le para escolher) e `passos`.

## O que observar

- a triagem custa uma chamada curta e evita a investigacao para ruido e casos ambiguos;
- o limiar de confianca transforma "roteador em duvida" em escalacao, e nao em rota errada;
- o roteador escolhe entre rotas fixas e a recomendacao entre runbooks fixos: o modelo decide, mas so dentro do que o codigo oferece;
- `MODELO_ROTEADOR` permite um modelo mais barato na triagem e um mais forte na investigacao.

## Limites intencionais

Os mesmos do 05 (registro em memoria, background no processo, token opcional, assinatura do SNS nao validada, sem fallback de modelo), mais:

- a rota `humano` so registra; nao notifica ninguem (Slack, PagerDuty);
- os destinos esperados do roteador sao avaliados com tres casos; um conjunto maior de avaliacao fica para o projeto real;
- sem execucao de acoes: o runbook e uma recomendacao (modelo 07).
