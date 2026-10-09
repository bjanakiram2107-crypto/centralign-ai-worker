"""Recovery policy: what the runtime does when actions fail.

The LLM handles *reasoning* about a failure (why did it fail, what to try instead).
This module handles the *limits*, so a confused agent cannot loop forever:
  - the same failing action twice in a row -> tell the agent to change approach
  - too many failures in total            -> stop and hand over to a human
"""

import json

MAX_TOTAL_FAILURES = 8


class RecoveryPolicy:
    def __init__(self):
        self.last_failed_signature = None

    @staticmethod
    def signature(tool_name: str, tool_input: dict) -> str:
        return tool_name + json.dumps(tool_input, sort_keys=True)

    def advice_after_failure(self, state, tool_name: str, tool_input: dict) -> str:
        sig = self.signature(tool_name, tool_input)
        repeated = sig == self.last_failed_signature
        self.last_failed_signature = sig
        if len(state.errors) >= MAX_TOTAL_FAILURES:
            return "ESCALATE"
        if repeated:
            return ("RECOVERY NOTE: you repeated an action that just failed with the same input. "
                    "Change your approach (different input, different page, or ask_user).")
        return "RECOVERY NOTE: the action failed. Read the error above, decide why, and adapt."

    def reset(self):
        self.last_failed_signature = None
