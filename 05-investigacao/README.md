# Investigacao de incidentes

Primeiro modelo pronto para uso real do roadmap (ver `docs/padroes-workflow.md` e `docs/adrs/001-evolucao-para-agente-aiops.md` na raiz do repo). Recebe alertas por webhook do Alertmanager, do CloudWatch (via SNS) ou de qualquer origem no formato generico, consulta tres fontes de dados em paralelo e devolve um diagnostico consolidado.

O projeto **nao executa acoes**. Ele investiga e diagnostica. As fontes de dados sao ficticias: arquivos de cenario que imitam o que Prometheus, logs e CI responderiam.

```text
Alertmanager / CloudWatch (SNS) / generico
    |
    v
adaptador -> Incidente -> registro (deduplicacao) -> 202
                              |
                              v  background
               RunnableParallel
                 +--> metricas: consultar_metricas -> LLM -> Analise
                 +--> logs:     consultar_logs     -> LLM -> Analise
                 +--> deploys:  consultar_deploys  -> LLM -> Analise
                              |
                              v
                 consolidacao -> Diagnostico -> GET /incidentes/{id}
```

## Onde esta cada parte

```text
src/
├── app.py        # endpoints, processamento em background
├── config.py     # leitura e validacao do ambiente na inicializacao
├── contratos.py  # Incidente, payloads das origens, dados das fontes, Diagnostico
├── fontes.py     # interface Fontes e a implementacao ficticia FonteCenarios
├── registro.py   # registro em memoria com deduplicacao por fingerprint
├── webhooks.py   # adaptadores: payload da origem -> Incidente
└── workflow.py   # modelo, prompts, ramos paralelos e consolidacao
cenarios/         # um JSON por servico, com metricas, logs e deploys
entradas/         # payloads de exemplo de cada origem
tests/            # testes com modelo falso, sem chamar API
```

## Executar

```bash
cp .env.example .env   # preencha OPENAI_API_KEY
uv sync
uv run fastapi dev src/app.py
```

Sem a chave, o app nao sobe e informa qual variavel falta.

Envie um alerta e consulte o resultado com o `id` devolvido:

```bash
curl --fail-with-body -X POST -H 'Content-Type: application/json' \
  --data @entradas/alertmanager.json \
  http://127.0.0.1:8000/webhooks/alertmanager

curl http://127.0.0.1:8000/incidentes/<id>
```

O CloudWatch chega como o SNS envia, com `Content-Type: text/plain`:

```bash
curl --fail-with-body -X POST -H 'Content-Type: text/plain; charset=UTF-8' \
  --data @entradas/cloudwatch-alarme.json \
  http://127.0.0.1:8000/webhooks/cloudwatch
```

Testes, lint e formatacao:

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format src tests
```

## Endpoints

| Metodo e rota | Funcao |
| --- | --- |
| `POST /webhooks/alertmanager` | payload do Alertmanager (`version: "4"`); cada item de `alerts[]` vira um incidente |
| `POST /webhooks/cloudwatch` | mensagem SNS; `Notification` com o alarme em `Message`, ou `SubscriptionConfirmation` |
| `POST /webhooks/generico` | `EventoGenerico`, para outras origens e testes |
| `GET /incidentes/{id}` | incidente normalizado, status do processamento e diagnostico |

Os webhooks devolvem `202` imediatamente. A acao de cada item indica o que aconteceu:

| Acao | Quando |
| --- | --- |
| `processando` | alerta novo; a investigacao roda em background |
| `duplicado` | mesmo `fingerprint` de um incidente ainda aberto, como no reenvio do Alertmanager |
| `resolvido` | `resolved` (ou `OK` no CloudWatch) para um incidente aberto |
| `ignorado` | `resolved` sem incidente aberto, ou `INSUFFICIENT_DATA` no CloudWatch |

## Fontes ficticias

`cenarios/<servico>.json` contem `metricas`, `logs` e `deploys`. O servico vem do incidente: `labels.service` (ou `servico`, `app`, `job`) no Alertmanager, e a dimensao `ServiceName` (ou `FunctionName`, `DBInstanceIdentifier`) no CloudWatch.

| Cenario | O que demonstra |
| --- | --- |
| `checkout-api` | deploy seguido de esgotamento do pool de conexoes; as tres fontes respondem (`completo`) |
| `pagamentos-worker` | `fontes_indisponiveis: ["logs"]` simula uma fonte fora do ar (`parcial`) |
| servico sem arquivo (`busca-api` em `entradas/generico.json`) | nenhuma fonte tem dados (`inconclusivo`) |

Para usar fontes reais, escreva uma classe com os mesmos tres metodos de `Fontes` e passe-a para `Investigacao` em `app.py`.

## Status do diagnostico

| Status | Significado |
| --- | --- |
| `completo` | as tres perspectivas concluiram |
| `parcial` | ao menos uma concluiu e outra falhou; a consolidacao registra a lacuna |
| `inconclusivo` | nenhuma fonte tinha dados; a consolidacao nao e chamada |

Se todos os ramos falharem e algum falhar por erro de modelo ou de codigo (`origem_falha: "processamento"`), o registro fica com `processamento: "falha"`, e nao `inconclusivo`. Um novo disparo do mesmo alerta tenta investigar de novo.

## O que observar

- a `perspectiva` de cada analise vem do codigo, nao do modelo;
- um ramo que falha vira `Analise(status="falha")` via `.with_fallbacks(..., exception_key="erro")` e nao derruba os demais;
- o modelo e configuravel por `MODELO` (`init_chat_model`), com `timeout` e `max_retries`;
- os modulos nao tem efeito colateral no import: a configuracao e lida no `lifespan`, o que permite aos testes injetar um modelo falso via `criar_app(investigacao)`;
- o nome do servico e validado antes de virar caminho de arquivo, porque vem de um payload externo.

## Limites intencionais

- registro em memoria: reiniciar perde o historico e varias replicas nao compartilham estado;
- background no proprio processo, sem fila externa;
- sem autenticacao nos webhooks e sem validar a assinatura do SNS: nao exponha em rede assim;
- a confirmacao de inscricao do SNS e registrada no log, nao executada;
- sem fallback para outro modelo ou provider quando o modelo configurado falha;
- sem triagem (modelo 06) e sem execucao de acoes (modelo 07).
