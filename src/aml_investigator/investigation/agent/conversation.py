import hashlib
from importlib import resources

from langchain_core.messages import HumanMessage, SystemMessage

PROMPTS = {
    name: resources.files(__package__).joinpath("prompts", f"{name}.md").read_text()
    for name in ("system", "investigate", "draft", "repair")
}
PROMPT_VERSION = hashlib.sha256(
    "\n".join(f"{name}\n{text}" for name, text in sorted(PROMPTS.items())).encode()
).hexdigest()[:12]


def system_message() -> SystemMessage:
    return SystemMessage(PROMPTS["system"])


def investigate_request(max_tool_calls: int) -> HumanMessage:
    return HumanMessage(PROMPTS["investigate"].format(max_tool_calls=max_tool_calls))


def draft_request() -> HumanMessage:
    return HumanMessage(PROMPTS["draft"])


def repair_request(previous: str, errors: list[str]) -> HumanMessage:
    listed = "\n".join(f"- {error}" for error in errors)
    return HumanMessage(PROMPTS["repair"].format(errors=listed, previous=previous))
