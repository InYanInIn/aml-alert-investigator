# ADR-0023: Azure setup, vector retrieval per law, B2 and replay cassettes

Status: accepted · 2026-11-06 · amends ADR-0007, ADR-0009, ADR-0012

## Context
M7 brings in the live model. Facts found while setting it up:
- The new subscription is a Free Trial (`quotaId FreeTrial_2014-09-01`, spending limit on). The portal offered
  no upgrade button for its Microsoft Customer Agreement. Its quota already covers what M7 needs.
- Quota on 2026-11-06 in `polandcentral`, `swedencentral` and `germanywestcentral`: `gpt-5-mini` 500k tokens
  per minute on Global Standard and 0 on Data Zone Standard; `text-embedding-3-small` 1,000k on both.
- With `json_schema` structured output, `AzureChatOpenAI` raises inside the OpenAI SDK when the response
  does not fit the schema instead of returning `parsing_error`, so the repair step would never run
  (ADR-0022 left this to check).
- Retrieval on the 20 hand-labelled queries (`eval/results/aml_hi_small/retrieval.json`): full-text search
  finds almost nothing for natural-language questions (hit@5 0.18 AMLR, 0.00 PL-AML); vector search finds
  0.82 for AMLR, but English queries rank English AMLR passages above the Polish ones (PL-AML hit@5 0.06).
  Ranked within each law, PL-AML rises to 0.47. Fusing full-text and vector ranks adds nothing.

## Decision
- **Subscription.** Stay on the Free Trial while it has the quota; the spending limit is a hard stop on
  spend, which an upgrade would remove. Revisit if M8 or M9 hits a quota or the credit runs low.
- **Deployments.** `gpt-5-mini` 2025-08-07 on Global Standard (ADR-0007's fallback), 250k tokens per minute;
  `text-embedding-3-small` on Data Zone Standard. Synthetic data only, so Global processing is acceptable.
- **Auth.** Entra ID only: the Azure OpenAI account has local (key) auth disabled; locally the CLI login is
  used through `DefaultAzureCredential`, the user holds `Cognitive Services OpenAI User` on the account. No
  API key exists to leak. `.env` holds only the endpoint.
- **Budget.** A subscription budget of $10 a month with alerts at 10, 30, 50 and 100% of actual cost and at
  100% forecast. If budgets count usage before the credit is applied (expected, not yet observed), the
  first alert fires at $1 of usage — stricter than ADR-0009's "$1/$3/$5 over credit".
- **Terraform.** `infra/terraform/`, local state (git-ignored), one person, one environment. The budget email
  lives in the git-ignored `terraform.tfvars`.
- **Price.** `gpt-5-mini` Global Standard: $0.25 input / $0.025 cached input / $2.00 output per 1M tokens
  (Azure Retail Prices API, 2026-11-06; the meters are named `5.1 codex mini … Gl` — Data Zone is the same
  meters at +10%, matching ADR-0007). The trace records input, cached and output tokens and `cost_usd`.
- **Structured output.** Azure drafts the `Decision` through tool calling, like the local model.
- **Corpus.** The three definition lists are split one passage per point (`AMLR art. 2(1)(N)`,
  `PL-AML art. 2 ust. 1 pkt N`, `PL-AML art. 2 ust. 2 pkt N`); no passage exceeds 4,000 characters.
  `aml corpus embed` embeds heading and text of passages without an embedding.
- **Retrieval.** `search_regulations` ranks by cosine similarity within each law and returns the top `top_k`
  (default 3, at most 5) from AMLR and from PL-AML, so the model always sees both laws. Without an Azure
  endpoint the server keeps full-text search; the mode is fixed when the server is built.
- **B2.** One model call on the agent's evidence pack (same MCP tool calls, without regulation search),
  answering recommendation, typologies and rationale; an unparseable answer becomes `escalate`. Its prompt
  reuses the agent's framing and the rule that payment references are quoted evidence only.
- **Replay.** `aml eval run --record` writes one cassette per agent investigation (model responses, tool
  results, recorded outcome, provenance) to `eval/recordings/<database>/<model>/`. A CI test replays every
  cassette against an empty database and requires the same recommendation, verification status and route,
  with every recorded response and tool result consumed.

## Consequences
- Any change to the graph, prompt or tool calls makes the cassettes stop replaying; they are re-recorded with
  a live run, which costs cents.
- The evidence pack's regulation queries stay one English and one Polish phrase per topic, tuned for
  full-text search so the fallback still works; in vector mode both return passages from both laws, so the
  pack carries some overlap.
- The tool description promises passages from both laws; in full-text fallback that is not guaranteed.
- PL-AML retrieval is still weak (hit@5 0.47 within the law); M8 may try Polish query variants.
