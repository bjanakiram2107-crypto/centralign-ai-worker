"""The shape every tool follows.

A tool is a plain Python function plus a JSON schema the LLM sees. Tools are
*constrained*: each one does a single, narrow, checkable operation. The LLM
can only act on the world through these functions, never directly.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """Raised by a tool when an action fails. The message is shown to the LLM so it can adapt."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]          # fn(ctx, **tool_input) -> result text
    irreversible: bool = False      # True = needs approval before running

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required if required is not None else list(properties), "additionalProperties": False}


@dataclass
class ToolContext:
    """What tools are allowed to touch: the state, the browser, the human, the config."""
    state: Any
    config: Any
    browser: Any = None
    human: Any = None
    extras: dict = field(default_factory=dict)
