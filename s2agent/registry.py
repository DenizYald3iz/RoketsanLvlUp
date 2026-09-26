"""Tool registry. Teammates add tools with @stage_tool — nothing else to wire.

    @stage_tool("MOTION", writes="kinematics", key_by="track_id")
    def get_track_kinematics(track_id: str, *, ctx: ToolContext) -> dict:
        '''Docstring = what the LLM sees. Say what it returns and when to call it.'''
        ...
        return {...}   # compact, JSON-serialisable

Contract
- `ctx` (keyword-only) is injected: ctx.image_id, ctx.data, ctx.evidence (read-only results so far).
  All other params become the tool's JSON schema (type hints + defaults required).
- The returned dict is stored in state.evidence[writes] (or evidence[writes][args[key_by]]) and a
  truncated JSON copy goes back to the LLM. Later stages read from evidence, so the LLM never
  has to copy numbers around.
- Return {"_image": data_url, ...} to show the LLM an image on its next turn.
- Return {"_evidence": {key: value}} to fill extra evidence keys (not shown to the LLM twice).
- Don't raise for expected problems; return {"error": "..."}. Unexpected exceptions are caught anyway.
"""
import inspect
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import create_model

from .config import CFG
from .data import Data, get_data

ANY_STAGE = "*"


@dataclass
class ToolContext:
    image_id: str
    evidence: dict
    data: Data = field(default_factory=get_data)


@dataclass
class ToolSpec:
    name: str
    stages: tuple[str, ...]
    fn: Callable
    writes: str | None
    key_by: str | None
    lc_tool: StructuredTool  # schema only; used for bind_tools


REGISTRY: dict[str, ToolSpec] = {}


def stage_tool(*stages: str, writes: str | None = None, key_by: str | None = None):
    def deco(fn: Callable) -> Callable:
        sig = inspect.signature(fn)
        fields = {
            p.name: (p.annotation, ... if p.default is inspect._empty else p.default)
            for p in sig.parameters.values()
            if p.name != "ctx"
        }
        schema = create_model(f"{fn.__name__}_args", **fields)
        lc = StructuredTool.from_function(
            func=lambda **_: None,
            name=fn.__name__,
            description=inspect.cleandoc(fn.__doc__ or fn.__name__),
            args_schema=schema,
        )
        REGISTRY[fn.__name__] = ToolSpec(fn.__name__, stages or (ANY_STAGE,), fn, writes, key_by, lc)
        return fn

    return deco


def tools_for_stage(stage: str) -> list[ToolSpec]:
    return [t for t in REGISTRY.values() if stage in t.stages or ANY_STAGE in t.stages]


@dataclass
class ToolRun:
    name: str
    args: dict
    result: dict
    llm_text: str  # what goes back to the model
    image: str | None
    evidence_update: dict


def run_tool(name: str, args: dict, *, stage: str, image_id: str, evidence: dict) -> ToolRun:
    """Execute one tool call. Never raises — errors come back as data for the LLM."""
    spec = REGISTRY.get(name)
    allowed = {t.name for t in tools_for_stage(stage)}
    if spec is None or name not in allowed:
        res = {"error": f"'{name}' bu aşamada ({stage}) yok. Kullanılabilir: {sorted(allowed)}"}
        return ToolRun(name, args, res, json.dumps(res, ensure_ascii=False), None, {})
    try:
        clean = spec.lc_tool.args_schema(**args).model_dump()
        res = spec.fn(**clean, ctx=ToolContext(image_id=image_id, evidence=evidence))
        if not isinstance(res, dict):
            res = {"result": res}
    except Exception as e:  # noqa: BLE001 — surface to the LLM, keep the graph alive
        res = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=2)[-500:]}
        clean = args

    image = res.pop("_image", None)
    update: dict[str, Any] = dict(res.pop("_evidence", None) or {})
    if spec.writes and "error" not in res:
        if spec.key_by:
            update[spec.writes] = {**evidence.get(spec.writes, {}), str(clean[spec.key_by]): res}
        else:
            update[spec.writes] = res
    text = json.dumps(res, ensure_ascii=False, default=str)
    if len(text) > CFG.max_tool_chars:
        text = text[: CFG.max_tool_chars] + ' …"(kısaltıldı)"'
    return ToolRun(name, clean, res, text, image, update)
