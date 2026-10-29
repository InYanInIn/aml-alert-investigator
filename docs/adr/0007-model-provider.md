# ADR-0007: Azure OpenAI gpt-5-mini through LangChain's chat-model interface

Status: accepted · 2026-10-28

## Context
The brief requires Azure OpenAI, kept swappable. In `polandcentral`, `gpt-5-mini` costs $0.275 input /
$0.0275 cached / $2.20 output per 1M tokens on Data Zone EU (Azure Retail Prices API). A new subscription may
start in quota Tier 0, where `gpt-5-mini` is offered only as Global Standard.

## Decision
- Use `gpt-5-mini` with low reasoning effort via `AzureChatOpenAI`.
- Prefer Data Zone EU; fall back to Global Standard if the subscription has no Data Zone quota. Data is
  synthetic, so this is acceptable here; a bank would require Data Zone EU or regional deployment.
- No project-level provider abstraction: LangChain's interface is the seam; tests use its fake chat model.

## Consequences
- Swapping providers is a constructor change, not a refactor.
- Model name, version and prompt version are stored with every investigation.
