from langchain_openai import ChatOpenAI

from .config import CFG


def make_llm(**overrides) -> ChatOpenAI:
    """GLM-5.3-flash via the OpenAI-compatible gateway.

    Notes from the task PDF: the model always reasons first, so never set a small
    max_tokens; use reasoning_effort instead of a `thinking` param; max 4 parallel requests.
    """
    kw = dict(
        model=CFG.model,
        base_url=CFG.base_url,
        api_key=CFG.api_key,
        reasoning_effort=CFG.reasoning_effort,
        max_retries=5,
        timeout=180,
    )
    kw.update(overrides)
    return ChatOpenAI(**kw)
