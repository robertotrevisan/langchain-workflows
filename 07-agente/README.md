# Agente de AIOps

Terceiro modelo pronto para uso real do roadmap (ver `docs/padroes-workflow.md` e `docs/adrs/001-evolucao-para-agente-aiops.md` na raiz do repo). Os modelos 05 e 06 sao workflows: o codigo define a trajetoria. Aqui o **modelo decide** o que consultar, que acao executar e quando parar, e o codigo impoe os limites.

O agente age num **ambiente simulado com estado**: as acoes mudam o cenario e as consultas seguintes refletem a mudanca. Nada sai do processo.

```text
Alertmanager / CloudWatch (SNS) / generico
    |
    v
adaptador -> Incidente -> registro (dedup, reentrada, supressao) -> 202
                              |
                              v  background
        create_agent (tool-calling em loop, thread por incidente)
          observar -> hipotese -> agir -> verificar -> repetir
          |
          +-- politica de acoes: servico do incidente, argumentos validos, MAX_ACOES
          +-- aprovacao humana: ACOES_COM_APROVACAO pausa o loop
          +-- teto: MAX_CHAMADAS_MODELO por incidente
          v
        Desfecho (resolvido | escalado | sem_acao), conferido pelo codigo
                              |
                              v
               timeline auditavel -> GET /incidentes/{id}
```

## O que muda em relacao ao 05 e ao 06

| Arquivo | Mudanca |
| --- | --- |
| `src/agente.py` | novo: ferramentas, politica de acoes, `create_agent`, conferencia do desfecho, timeline |
| `src/ambiente.py` | novo: substitui `fontes.py`; consultas e acoes sobre um estado que muda |
| `cenarios/*.json` | estado inicial, dados degradados e saudaveis e o efeito de cada acao |
| `src/registro.py` | maquina de estados com aprovacao, reentrada, supressao e timeline |
| `src/contratos.py` | `Desfecho`, `EventoTimeline`, `AcaoPendente`, `PedidoAprovacao`; sem `Diagnostico` |
| `src/config.py` | limites do agente (`MAX_*`, `ACOES_COM_APROVACAO`, `JANELA_SUPRESSAO_MIN`) |
| `src/app.py` | endpoint de aprovacao; dispara iniciar, reentrar ou decidir em background |
| `src/webhooks.py`, `autenticacao.py` | iguais ao 05 |

A triagem do 06 nao entra: o agente pode concluir `sem_acao` ou `escalado` sozinho, e um incidente sem servico escala sem chamar o modelo. Para cortar custo com ruido, ponha o roteador do 06 na frente.

## Executar

```bash
cp .env.example .env   # preencha a chave e, fora do ambiente local, WEBHOOK_TOKEN
uv sync
uv run fastapi dev src/app.py
```

O modelo precisa suportar tool calling. Para usar OpenRouter ou outro gateway compativel com a OpenAI, veja `OPENAI_BASE_URL` no `.env.example`.

### Roteiro: aprovacao, verificacao e resolved

```bash
H='Content-Type: application/json'; API=http://127.0.0.1:8000

# 1. checkout-api com 18% de 5xx. O agente investiga e pede para reverter o deploy.
curl -X POST -H "$H" --data @entradas/alertmanager-checkout.json $API/webhooks/alertmanager
curl $API/incidentes/<id>        # processamento: aguardando_aprovacao, pendentes: [reverter_deploy]

# 2. Aprovar (ou {"decisao": "rejeitar", "motivo": "..."}). O agente reverte e verifica.
curl -X POST -H "$H" -d '{"decisao": "aprovar", "motivo": "ok do oncall"}' $API/incidentes/<id>/aprovacao
curl $API/incidentes/<id>        # aguardando_confirmacao, desfecho: resolvido

# 3. A origem confirma.
curl -X POST -H "$H" --data @entradas/alertmanager-resolvido.json $API/webhooks/alertmanager
```

Outros casos:

```bash
# pagamentos-worker: causa externa (gateway); o agente escala sem agir
curl -X POST -H "$H" --data @entradas/alertmanager.json $API/webhooks/alertmanager

# depois de uma acao no checkout-api: alerta de pods reiniciando e suprimido
curl -X POST -H "$H" --data @entradas/alertmanager-efeito-colateral.json $API/webhooks/alertmanager
```

O estado do ambiente vive no processo: reinicie o servidor para voltar o cenario ao inicio.

Testes, sempre com modelo roteirizado (sem API):

```bash
uv run pytest
```

## Ciclos

**Loop interno.** Numa execucao, o modelo chama ferramentas ate devolver um `Desfecho` (via `ToolStrategy`), pausar para aprovacao ou esgotar `MAX_CHAMADAS_MODELO`.

**Reentrada externa.** O thread do checkpointer e o `id` do incidente. Quando o agente conclui `resolvido`, o incidente fica em `aguardando_confirmacao` com o fingerprint aberto. Se a origem disparar de novo em vez de mandar `resolved`, a conclusao nao se sustentou: o mesmo thread recebe uma mensagem de reentrada e continua com todo o historico. Depois de `MAX_CICLOS`, o novo disparo escala sem chamar o modelo.

O cenario do checkout-api tem uma armadilha para isso: `reiniciar_servico` deixa o servico saudavel na primeira consulta de metricas e degradado nas seguintes. Um agente que reinicia e verifica uma vez conclui `resolvido`, e o alerta volta.

| `processamento` | Quando | Novo firing do mesmo alerta |
| --- | --- | --- |
| `processando` | o agente esta executando | `duplicado` |
| `aguardando_aprovacao` | uma acao espera decisao humana | `duplicado` |
| `aguardando_confirmacao` | o agente concluiu `resolvido` | `reentrada` (ou `escalado` apos `MAX_CICLOS`) |
| `escalado` | o agente escalou ou esgotou o teto | `duplicado`: o incidente e de um humano |
| `encerrado` | o agente concluiu `sem_acao` | `duplicado` |
| `resolvido` | a origem mandou `resolved` | abre incidente novo |
| `falha` | erro de modelo ou de codigo | abre incidente novo |

## Limites

| Limite | Onde | Comportamento |
| --- | --- | --- |
| Lista fechada de ferramentas | `criar_ferramentas` | 4 consultas e 3 acoes; nada alem disso existe para o modelo |
| Acao so no servico do incidente | `PoliticaDeAcoes` | bloqueia e devolve erro ao modelo |
| Argumentos validos | `PoliticaDeAcoes` | `replicas` entre 1 e 20, `motivo` de 10 a 500 caracteres |
| `MAX_ACOES` por incidente | `PoliticaDeAcoes` | soma os ciclos; acima disso, bloqueia e pede `escalado` |
| Aprovacao humana | `HumanInTheLoopMiddleware` | `ACOES_COM_APROVACAO` pausa antes de executar; aprovar ou rejeitar |
| `MAX_CHAMADAS_MODELO` | `ModelCallLimitMiddleware` | por thread, soma os ciclos; ao esgotar, `escalado` |
| `MAX_CICLOS` | `Registro` | reentradas alem do limite escalam |
| Resolucao com prova | `validar_desfecho` | `resolvido` sem acao executada ou sem consulta de metricas depois dela vira `escalado` |
| Supressao | `Registro` | alerta novo no mesmo servico ate `JANELA_SUPRESSAO_MIN` apos uma acao entra na timeline do incidente que agiu |

A politica roda antes da aprovacao: um humano nunca e chamado para aprovar uma acao que seria bloqueada. Uma rejeicao chega ao modelo como erro da ferramenta, com o motivo; o prompt pede para nao repetir a acao.

## Timeline

Cada incidente guarda os eventos em ordem: `recebido`, `raciocinio` (texto do modelo, quando houver), `consulta`, `acao` (com o `motivo`, que e a hipotese), `resultado`, `aprovacao_solicitada`, `aprovacao`, `desfecho`, `reentrada`, `suprimido`, `resolvido`, `limite` e `falha`. Os eventos do agente sao derivados das mensagens de cada ciclo; o horario e o do fim do ciclo, nao o de cada chamada.

## O que observar

- o mesmo modelo que no 05 analisava dados agora escolhe quais dados buscar e o que fazer com eles;
- os limites estao no codigo, nao no prompt: o prompt orienta, a politica garante;
- `validar_desfecho` impede o modelo de declarar sucesso sem ter verificado;
- com o modelo real, uma rejeicao da reversao levou o agente a reiniciar como mitigacao, verificar e ainda assim escalar, porque a causa continuava.

## Limites intencionais

Os mesmos do 05 (registro em memoria, background no processo, token opcional, assinatura do SNS nao validada, sem fallback de modelo), mais:

- checkpointer em memoria (`InMemorySaver`): reiniciar perde as conversas e as aprovacoes pendentes; em producao, use um checkpointer persistente;
- a aprovacao nao identifica quem aprovou: qualquer portador do token decide; uma decisao vale para todas as acoes pendentes do incidente;
- a supressao e por servico e janela: um problema novo e real no mesmo servico logo apos uma acao tambem e suprimido (fica registrado na timeline);
- o ambiente simulado nao tem efeitos colaterais proprios: o alerta de efeito colateral e enviado a mao;
- o cenario nao muda com o tempo, so com acoes e consultas.
