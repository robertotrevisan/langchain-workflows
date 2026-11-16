---
adr_number: "001"
status: aceito
created: 2026-11-16
supersedes: ""
superseded_by: ""
---

# ADR 001: Evolucao dos exemplos 01-04 para tres modelos AIOps prontos para uso real

## Contexto

Os exemplos 01 a 04 sao conceituais. Cada um isola uma primitiva do LangChain: sequencia, contratos, `RunnableBranch` e `RunnableParallel`. Nenhum deles serve como ponto de partida para um agente de AIOps real, pelos seguintes motivos:

- **Nao ha entrada real.** Recebem JSON didatico, e nao eventos de Alertmanager, CloudWatch ou outro monitoramento.
- **Nao buscam dados.** As tres analises do 04 sao opinioes sobre o mesmo texto de entrada; nenhuma consulta metricas, logs ou deploys.
- **Nao agem.** Nenhum executa acoes nem verifica se o problema foi resolvido.
- **Faltam requisitos de producao:** retry, timeout, resultado parcial, limites de entrada, testes e observabilidade (ver "Lacunas para producao" em `docs/padroes-workflow.md`).

Na pergunta "routing ou parallel como base de um agente", a analise mostrou que os dois padroes nao competem: routing corresponde a triagem, e parallel corresponde a investigacao. O salto de valor esta em ligar ferramentas aos ramos, e nao em trocar um padrao pelo outro.

## Alternativas Consideradas

- **Evoluir somente o 03 (routing)**
  - A favor: e a porta de entrada natural, ja tem fallback e e o caminho direto ate o tool-calling.
  - Contra: nao tem investigacao com varias fontes; a rota de incidente continuaria sendo uma unica chamada de LLM sobre texto.
- **Evoluir somente o 04 (parallel)**
  - A favor: ja investiga por varias perspectivas e consolida.
  - Contra: aceita um unico tipo de entrada, nao decide nada e nao tem caminho natural ate a autonomia.
- **Pular direto para um agente (07)**
  - A favor: entrega a autonomia mais cedo.
  - Contra: mistura de uma vez webhook, ferramentas, ambiente simulado, loop e limites de seguranca. Fica dificil isolar falhas e quebra a progressao didatica de um conceito por exemplo.
- **Tres modelos incrementais: 05 investigacao, 06 triagem + investigacao, 07 agente**
  - A favor: cada passo introduz um conceito novo, reaproveita o anterior e pode ser usado sozinho como boilerplate.
  - Contra: mais codigo duplicado entre os projetos.

## Decisao

Criar tres novos projetos incrementais, mantendo os exemplos 01 a 04 intactos como referencia conceitual:

1. **05-investigacao**
   - Webhook de entrada com normalizacao para um contrato unico `Incidente`.
   - `RunnableParallel` cujos ramos consultam fontes de dados ficticias antes de chamar o LLM, e depois consolidacao.
   - Saida: diagnostico. Nao age.
2. **06-triagem-investigacao**
   - Roteador com confianca e justificativa na entrada.
   - A rota de incidente executa a investigacao do 05; as demais rotas tem tratativas proprias ou escalam para humano.
   - Saida: diagnostico mais recomendacao de runbook. Nao age.
3. **07-agente**
   - Um modelo com tool-calling em loop decide quais consultas e acoes executar.
   - Ciclo: observar -> hipotese -> agir -> verificar -> repetir, ate resolver ou esgotar os limites.
   - Age somente sobre um ambiente simulado com estado.

Regras validas para os tres:

- **Entrada por webhook.**
  - Um adaptador por origem: Alertmanager, CloudWatch via SNS e um formato generico.
  - Resposta `202` imediata, com processamento em background.
  - Deduplicacao por `fingerprint` ou ID do alarme.
- **Fontes de dados ficticias**, dirigidas por cenarios em arquivo. No 07, o ambiente simulado tem estado: uma acao altera as leituras seguintes.
- **Contratos Pydantic em todas as fronteiras**: webhook -> `Incidente` -> etapas -> resposta.
- **Projetos independentes.** Cada um e um projeto `uv` proprio, seguindo a convencao do repo. O codigo comum (adaptadores de webhook, fontes ficticias) e copiado, nao compartilhado. *(premissa — confirme ou corrija)*
- **Limites de autonomia no 07, obrigatorios desde a primeira versao:**
  - maximo de iteracoes;
  - lista fechada de acoes permitidas;
  - aprovacao humana para acoes destrutivas;
  - escalacao para humano ao esgotar os limites;
  - protecao contra reagir a alertas gerados pela propria acao.
- **Reentrada no 07.** Um novo evento do webhook com o mesmo `fingerprint` (`resolved` ou novo `firing`) retoma o mesmo incidente com o estado persistido; nao abre outro.
- **Implementacao do 07** com `create_agent` do LangChain 1.x, usando checkpointer para o estado por incidente e middleware de aprovacao humana. Se isso nao bastar, migrar para LangGraph explicito. *(premissa — confirme ou corrija)*

O que pesou na decisao: a progressao incremental permite validar cada camada antes da seguinte (entrada real, depois dados, depois decisao, depois acao), e cada modelo continua util sozinho como boilerplate.

## Consequencias

- **Positivas:**
  - Boilerplates prontos para tres niveis de maturidade. Um projeto real pode comecar pelo 05 (so diagnostico) e subir de nivel quando houver confianca.
  - O webhook e os contratos de entrada sao resolvidos uma vez, no 05, e reaproveitados.
  - O ambiente simulado com estado torna o loop do agente testavel sem tocar infraestrutura real.
- **Negativas:**
  - Codigo duplicado entre 05, 06 e 07 (adaptadores, fontes, contratos). Uma correcao num deles precisa ser replicada manualmente.
  - As fontes ficticias exigem manter cenarios coerentes: metricas, logs e deploys que contam a mesma historia.
  - O 07 adiciona dependencia de persistencia de estado e um fluxo de aprovacao humana, que nao existiam no repo.
- **Neutras / trade-offs aceitos:**
  - Os exemplos 01 a 04 continuam didaticos. As lacunas de producao sao resolvidas nos modelos novos, nao neles.
  - Substituir fontes ficticias por reais (Prometheus, CloudWatch Logs, CI) fica fora do escopo; a interface das fontes deve permitir essa troca.
