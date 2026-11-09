import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources

from langchain_core.messages import HumanMessage, SystemMessage

PROMPTS = {
    name: resources.files(__package__).joinpath("prompts", f"{name}.md").read_text()
    for name in ("system", "investigate", "draft", "repair")
}
PROFILE_HOW_TO_DECIDE = resources.files(__package__).joinpath("prompts", "how_to_decide_profile.md").read_text()
HOW_TO_DECIDE_HEADING = "How to decide:"
DATA_RULES_HEADING = "Data rules:"


def version_of(prompts: Mapping[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{name}\n{text}" for name, text in sorted(prompts.items())).encode()).hexdigest()[
        :12
    ]


def with_profile_decision(system: str) -> str:
    introduction, _, rest = system.partition(HOW_TO_DECIDE_HEADING)
    _, _, shared_rules = rest.partition(DATA_RULES_HEADING)
    return f"{introduction}{PROFILE_HOW_TO_DECIDE.strip()}\n\n{DATA_RULES_HEADING}{shared_rules}"


@dataclass(frozen=True)
class AgentPrompt:
    system: str
    version: str


PROMPT_VERSION = version_of(PROMPTS)
STANDARD_PROMPT = AgentPrompt(PROMPTS["system"], PROMPT_VERSION)
PROFILE_SYSTEM = with_profile_decision(PROMPTS["system"])
PROFILE_PROMPT = AgentPrompt(PROFILE_SYSTEM, version_of({**PROMPTS, "system": PROFILE_SYSTEM}))


def system_message(prompt: AgentPrompt) -> SystemMessage:
    return SystemMessage(prompt.system)


def investigate_request(max_tool_calls: int) -> HumanMessage:
    return HumanMessage(PROMPTS["investigate"].format(max_tool_calls=max_tool_calls))


def draft_request() -> HumanMessage:
    return HumanMessage(PROMPTS["draft"])


def repair_request(previous: str, errors: list[str]) -> HumanMessage:
    listed = "\n".join(f"- {error}" for error in errors)
    return HumanMessage(PROMPTS["repair"].format(errors=listed, previous=previous))
