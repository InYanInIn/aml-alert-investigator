from langchain_ollama import ChatOllama

LOCAL_MODEL = "qwen3.6:35b-a3b-q8_0"
CONTEXT_TOKENS = 65_536
MAX_OUTPUT_TOKENS = 8_192
SEED = 20261105


def local_model() -> ChatOllama:
    return ChatOllama(
        model=LOCAL_MODEL,
        name=LOCAL_MODEL,
        reasoning=True,
        seed=SEED,
        num_ctx=CONTEXT_TOKENS,
        num_predict=MAX_OUTPUT_TOKENS,
    )
