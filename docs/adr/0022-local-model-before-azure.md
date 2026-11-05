# ADR-0022: A local open-weight model before Azure, not instead of it

Status: accepted · 2026-11-05 · amends ADR-0009

## Context
Until now the plan has the agent meet a real LLM only in Phase B. By then the $200 Azure credit has a 30-day
clock, and the personal budget is $5 (ADR-0009). The deterministic stand-in model of M5 shows the graph is
wired correctly. It cannot show how a real model behaves: whether its tool calls are valid, whether the
`Decision` it produces parses and passes the verifier, how often repair fires, or how it reacts to injected
payment references. Prompt, `k` and budget iteration in M8 is the most model-hungry part of the project.

The development machine is an Apple M1 Max with 64 GB of unified memory; macOS gives the GPU about 52 GB by
default. Ollama is installed. Candidates found on 2026-11-05 (to be re-checked before download):
- `Qwen3.6-35B-A3B`, a mixture of experts with about 3B active parameters, ~34 GiB at Q8. On Artificial
  Analysis' intelligence index it scores 32 against an estimated 31 for `gpt-5-mini (medium)`. No head-to-head
  tool-calling benchmark was found.
- `Qwen3.8 27B`, ~27 GiB at Q8, recommended for 64 GB in a single October 2026 guide; not verified.
- `gpt-oss-120b` does not fit (~59 GiB).

## Decision
- Add M6.6 to Phase A: run the agent on a local open-weight model served by Ollama, before the Azure account
  exists. Azure stays: `gpt-5-mini` (ADR-0007) remains the model whose numbers the project reports.
- The local model is one more `BaseChatModel` in `investigation/models/`, selected by a CLI option. The graph,
  tools and verifier do not change.
- The model is chosen by a short spike, not by benchmarks: the first candidate that makes valid tool calls and
  returns a parseable `Decision` through LangChain on a handful of validation alerts. The licence must permit
  this use.
- Prefer `ChatOpenAI` against Ollama's OpenAI-compatible endpoint, because M7's `AzureChatOpenAI` comes from
  the same `langchain-openai` package. Fall back to `ChatOllama` (`langchain-ollama`) if the spike shows tool
  calls or structured output do not work through the compatible endpoint.
- Spike result (2026-11-05, `qwen3.6:35b-a3b-q8_0`, Apache 2.0, on a synthetic evidence conversation):
  through the compatible endpoint `ChatOpenAI` returned no tool calls, and its `json_schema` structured output
  raised a pydantic `ValidationError` instead of returning `parsing_error`, which would crash `draft_decision`.
  `ChatOllama` with thinking off failed both steps too (no tool calls, `Decision` wrapped in markdown).
  `ChatOllama` with thinking on made two valid tool calls (68 s) and returned a parseable `Decision` with the
  default `json_schema` method (152 s); the `function_calling` method also parsed but took 271 s. So the
  fallback applies: the local model is `ChatOllama(reasoning=True, temperature=0)`.
- Local runs use the validation split only. The test split is not opened with the local model.
- Responses recorded from the local model may seed the CI replay evaluation planned for M7.
- Whether the local model also appears as its own system in the final results is decided in M8.

## Consequences
- Most model behaviour, prompt work and failure modes are explored at $0 and without the 30-day clock;
  Phase B is spent on confirming them with `gpt-5-mini`, measuring cost and the demo.
- Prompts tuned on one model may not transfer to another. M7 re-runs the same validation alerts on
  `gpt-5-mini` and compares before any M8 tuning is trusted.
- Local latency and token counts say nothing about Azure cost; cost calibration stays in M7.
- Embeddings stay in M7; M6.6 uses the existing full-text search.
- M7 must check that `AzureChatOpenAI`'s structured output returns `parsing_error` rather than raising; the
  spike saw the same `langchain-openai` path raise against Ollama.
