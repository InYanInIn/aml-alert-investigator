import json
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from aml_investigator.investigation.decision import Claim, Decision, Fact, FactField, TypologyVerdict
from aml_investigator.investigation.verify import TRANSACTION_TOOLS
from aml_investigator.monitoring.thresholds import load_thresholds, rule_score
from aml_investigator.monitoring.typologies import Typology

B1_VALIDATION_RESULT = "eval/results/{database}/val/b1.json"
CITED_PAYMENTS = 3
DONE = "No further tool calls: the evidence pack decides the recommendation."
PAID: tuple[str, FactField, FactField] = ("paid", "amount_paid", "paid_currency")
RECEIVED: tuple[str, FactField, FactField] = ("received", "amount_received", "received_currency")


class RuleScoreChatModel(BaseChatModel):
    thresholds: dict[str, float]
    close_below: float
    name: str | None = "rule-score"

    @property
    def _llm_type(self) -> str:
        return "rule-score"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> ChatResult:
        requested = {tool["function"]["name"] for tool in kwargs.get("tools", [])}
        if Decision.__name__ not in requested:
            return result(AIMessage(DONE))
        decision = self.draft(tool_results(messages))
        call = {"name": Decision.__name__, "args": decision.model_dump(), "id": "rule-score-decision"}
        return result(AIMessage("", tool_calls=[call]))

    def draft(self, results: list[tuple[str, dict[str, Any]]]) -> Decision:
        alert = next(content for tool, content in results if tool == "get_alert")
        hits = [(hit["rule_id"], float(hit["score"])) for hit in alert["rule_hits"]]
        recommendation = "close" if rule_score(hits, self.thresholds) < self.close_below else "escalate"
        seen = {
            txn["txn_id"]: txn
            for tool, content in results
            if tool in TRANSACTION_TOOLS
            for txn in content["transactions"]
        }
        flagged = dict.fromkeys(txn_id for hit in alert["rule_hits"] for txn_id in hit["evidence_txn_ids"])
        evidence = [txn_id for txn_id in flagged if txn_id in seen] or list(seen)[:1]
        passages = list(
            dict.fromkeys(
                content["passages"][0]["passage_id"]
                for tool, content in results
                if tool == "search_regulations" and content["passages"]
            )
        )
        candidates = [content for tool, content in results if tool == "get_typology_candidates"]
        return Decision(
            recommendation=recommendation,
            typologies=typology_verdicts(candidates, seen, escalate=recommendation == "escalate"),
            who=[],
            what=[payment_claim(seen[txn_id], alert["account_id"]) for txn_id in evidence[:CITED_PAYMENTS]],
            when=[],
            where=[],
            why=[reason_claim(recommendation, evidence, passages)],
            how=[],
        )


def rule_score_model(database: str) -> RuleScoreChatModel:
    operating_point = json.loads(Path(B1_VALIDATION_RESULT.format(database=database)).read_text())["operating_point"]
    return RuleScoreChatModel(thresholds=load_thresholds(database), close_below=float(operating_point["threshold"]))


def result(message: AIMessage) -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=message)])


def tool_results(messages: list[BaseMessage]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(message.name), message.artifact["structured_content"])
        for message in messages
        if isinstance(message, ToolMessage) and message.status == "success" and message.artifact
    ]


def typology_verdicts(
    candidates: list[dict[str, Any]], seen: dict[int, dict[str, Any]], *, escalate: bool
) -> list[TypologyVerdict]:
    verdicts: dict[Typology, TypologyVerdict] = {}
    for candidate in (item for content in candidates for item in content["candidates"]):
        cited = [txn_id for txn_id in candidate["evidence_txn_ids"] if txn_id in seen]
        for position, typology in enumerate(candidate["typologies"]):
            if escalate and position == 0 and cited:
                verdicts[typology] = TypologyVerdict(typology=typology, verdict="supported", txn_ids=cited)
            else:
                verdicts.setdefault(
                    typology, TypologyVerdict(typology=typology, verdict="insufficient_evidence", txn_ids=[])
                )
    return list(verdicts.values())


def payment_claim(txn: dict[str, Any], account_id: str) -> Claim:
    outgoing = txn["from_account"] == account_id
    verb, amount_field, currency_field = PAID if outgoing else RECEIVED
    amount = Decimal(str(txn[amount_field]))
    day = datetime.fromisoformat(txn["ts"]).date().isoformat()
    return Claim(
        text=f"The account {verb} {amount:,.2f} {txn[currency_field]} on {day}.",
        txn_ids=[txn["txn_id"]],
        passage_ids=[],
        facts=[
            Fact(txn_id=txn["txn_id"], field=amount_field, value=f"{amount:.2f}"),
            Fact(txn_id=txn["txn_id"], field=currency_field, value=txn[currency_field]),
            Fact(txn_id=txn["txn_id"], field="date", value=day),
        ],
    )


def reason_claim(recommendation: str, evidence: list[int], passages: list[str]) -> Claim:
    position, outcome = ("below", "can be closed") if recommendation == "close" else ("at or above", "is escalated")
    return Claim(
        text=(
            f"The alert's rule scores, each relative to its calibrated rule threshold, sum to a value {position} "
            f"the operating point of the rule-score baseline, so the alert {outcome}."
        ),
        txn_ids=evidence,
        passage_ids=passages,
        facts=[],
    )
