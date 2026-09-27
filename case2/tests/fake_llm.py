from langchain_core.messages import AIMessage


class ScriptedLLM:
    """Stand-in for ChatOpenAI: returns scripted AIMessages in order, then plain 'tamam'."""

    def __init__(self, script: list[AIMessage] | None = None):
        self.script = list(script or [])
        self.calls: list[list] = []

    def bind_tools(self, tools):
        self.bound = [t.name for t in tools]
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return self.script.pop(0) if self.script else AIMessage("tamam")


def call(name: str, args: dict | None = None, i: int = 0) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": f"c{name}{i}"}])
