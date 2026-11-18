# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que e este repo

Quatro exemplos de workflows LangChain para AIOps (`01-workflow-sequencial`, `02-outputs-contratos`, `03-routing-workflow`, `04-parallel-workflow`). Eles nasceram como material didatico e agora servem de base para boilerplates de projetos reais. Comparativo, casos de uso e lacunas para producao: @docs/padroes-workflow.md

Os exemplos 01 a 04 ficam **didaticos e intactos**. A evolucao para uso real acontece nos modelos 05, 06 e 07 (webhook, fontes ficticias, agente), ja implementados, conforme `docs/adrs/001-evolucao-para-agente-aiops.md` e a secao "Roadmap" de `docs/padroes-workflow.md`. Decisoes duraveis viram ADR em `docs/adrs/` (`NNN-slug.md`), e ADR `aceito` nao se edita: revisar uma decisao significa criar um ADR novo que o supersede. Ao mudar o comportamento de um projeto, atualize o `README.md` dele e a tabela de lacunas em `docs/padroes-workflow.md`.

## Estrutura e comandos

- Nao ha manifest na raiz. Cada pasta numerada e um projeto `uv` independente, com `pyproject.toml`, `uv.lock` e `.env` proprios. Rode todos os comandos **de dentro da pasta do projeto**.
- Setup: `cp .env.example .env && uv sync`. As chaves variam por projeto: `GOOGLE_API_KEY` em 01/02, `OPENAI_API_KEY` + `ANTHROPIC_API_KEY` em 03, `OPENAI_API_KEY` em 04.
- 01 e um script: `uv run src/app.py`. Ele le `alerta.txt` pelo cwd.
- 02 a 07 sao APIs: `uv run fastapi dev src/app.py`, depois `curl --data @entrada.json ...` (02) ou `--data @entradas/<arquivo>.json` (03 a 07). Os endpoints estao nos READMEs.
- 05 a 07: o webhook do CloudWatch le o corpo cru, porque o SNS envia `Content-Type: text/plain`. A chave depende de `MODELO` (`provider:modelo`); o default e `openai:gpt-5.6-luna`. Para OpenRouter ou outro gateway compativel com a OpenAI: `OPENAI_BASE_URL` no `.env` e `MODELO=openai:<id-do-gateway>`, sem mudar codigo.
- Os imports dentro de `src/` sao planos (`from contratos import ...`, `from workflow import ...`). Nao converta para pacote/imports relativos sem ajustar a forma de execucao.
- Lint/format: `uv run ruff check src` e `uv run ruff format src` (config em `[tool.ruff]` de cada `pyproject.toml`).
- Testes so existem a partir do 05: `uv run pytest` (com `pythonpath = ["src"]`). Eles nunca chamam API de LLM; mantenha assim. 05/06 usam `ModeloFalso` e 07 usa `ModeloRoteirizado` (roteiro de tool calls), ambos em `tests/conftest.py`. O app e testado via `criar_app(...)` com modelo injetado.
- 06: `RODAR_LLM=1 uv run pytest tests/test_roteador_llm.py -v` avalia o roteador com o modelo real (custa chamadas de API). Rode depois de mudar `SYSTEM_ROTEADOR` ou `MODELO_ROTEADOR`.
- 05, 06 e 07 compartilham codigo por copia (ADR 001): uma correcao em `webhooks.py` ou `autenticacao.py` vale para os tres, e em `fontes.py`, `workflow.py` ou `registro.py`, para 05 e 06. No 07, `registro.py` e outro (maquina de estados com aprovacao e reentrada) e `ambiente.py` substitui `fontes.py`; as secoes 1 a 3 de `contratos.py` sao as mesmas nos tres.
- 07: os limites do agente ficam no codigo (`PoliticaDeAcoes`, middlewares, `validar_desfecho`), nao no prompt. A politica precisa vir antes do `HumanInTheLoopMiddleware` na lista, para que acao bloqueada nunca chegue a aprovacao.
- A partir do 05, nenhum modulo de `src/` pode ter efeito colateral no import (ler env, instanciar modelo). A configuracao e lida no `lifespan`.
- Em 01, `import time` sem uso e intencional: o bloco `RunnableSequence` do README, que substitui o final de `app.py`, usa esse import. Nao remova.

## Convencoes

- Separacao de 02 a 04, a seguir em projetos novos:
  - `app.py`: so o endpoint FastAPI, fino;
  - `contratos.py`: modelos Pydantic de entrada, intermediarios e saida;
  - `workflow.py`: secoes numeradas por comentario (modelo, system prompt e chain de cada etapa), terminando em `executar_workflow(entrada) -> Resultado`.
- Codigo, identificadores, prompts, comentarios e docs em **portugues sem acentos** (ASCII).
- Os IDs de modelo (`gemini-3.8-flash`, `gpt-5.6-luna`, `gpt-5.6-sol`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`) sao intencionais. Nao "corrija" nem troque sem pedido.
- Em 03, `provider`/`modelo` aparecem duplicados: na construcao do modelo e no `Resultado` de cada `tratar_*`. Ao trocar um modelo, atualize os dois lugares e a tabela do README.
- Commits: Conventional Commits em portugues (`feat: adiciona ...`).
