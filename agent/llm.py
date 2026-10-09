"""The only place that talks to the LLM (Claude, via the Anthropic API).

Keeping it in one small class means the model can be swapped, and tests can use
a scripted stand-in (tests/fake_llm.py) without changing the agent.
"""

import anthropic


class ClaudeLLM:
    def __init__(self, model: str, effort: str):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self.model = model
        self.effort = effort
        self.use_fallbacks = True

    def create(self, system: str, tools: list[dict], messages: list[dict]):
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},  # show a summary of the model's reasoning
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},  # re-use the unchanged conversation prefix: cheaper and faster
        )
        if self.use_fallbacks:
            try:
                # If a safety classifier declines, the API retries on a fallback model inside the same call.
                return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            except anthropic.BadRequestError:
                # This account or model does not accept the fallback beta: carry on without it.
                # (If the request itself is broken, the plain call below raises the real error.)
                self.use_fallbacks = False
        return self.client.messages.create(**kwargs)
