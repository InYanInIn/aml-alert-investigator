from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from aml_investigator.core.settings import Settings


def tracing_callbacks(settings: Settings) -> list[BaseCallbackHandler]:
    public_key, secret_key, host = settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host
    if not public_key or not host or secret_key is None or not secret_key.get_secret_value():
        return []
    Langfuse(public_key=public_key, secret_key=secret_key.get_secret_value(), base_url=host)
    return [CallbackHandler(public_key=public_key)]
