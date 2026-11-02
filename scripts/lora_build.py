"""LoRA data rendering under Spec 0.3 BUILD-RENDER."""

from __future__ import annotations

import json


def render_messages(messages: list[dict]) -> list[dict]:
    """BUILD-RENDER: select chat fields and parse tool argument JSON without mutation."""
    selected = []
    for message in messages:
        message = {k: v for k, v in message.items()
                   if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
        if message.get("tool_calls"):
            message["tool_calls"] = [
                {**call, "function": {
                    **call["function"],
                    "arguments": (json.loads(call["function"]["arguments"])
                                  if isinstance(call["function"].get("arguments"), str)
                                  else call["function"].get("arguments")),
                }} for call in message["tool_calls"]
            ]
        selected.append(message)
    return selected


def render_row(tokenizer, row: dict, *, enable_thinking: bool,
               add_generation_prompt: bool = False) -> list[int]:
    """BUILD-RENDER: render tools and the explicit thinking value in one tokenization."""
    return tokenizer.apply_chat_template(
        render_messages(row["messages"]), tools=row.get("tools"),
        enable_thinking=enable_thinking, add_generation_prompt=add_generation_prompt,
        tokenize=True, return_dict=False,
    )
