"""A scripted model for deterministic offline pipelines and tests.

It has the real client's `complete` signature, answers from a script instead
of a server, and records every call so tests can inspect what a pipeline
would have sent (for example, that no answer-key text reaches a prompt).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .client import ModelError, strip_fence


@dataclass(frozen=True)
class Call:
    """One call to the model. `tag` is `(submission, question, stage)`."""
    tag: tuple[str, str, str] | None
    prompt: str
    text: str | None
    images: tuple[Any, ...]
    expect: str
    reasoning: bool


class FakeModel:
    """Answers from `script`: a callable `(call) -> str | None`, or a dict
    keyed by `(submission, question, stage)`, `(question, stage)` or `stage`
    (the most specific key wins) whose values are strings or callables
    `(call) -> str`. A call with no scripted answer raises `ModelError`."""

    def __init__(self, script: Callable[[Call], str | None] | Mapping):
        self.script = script
        self.calls: list[Call] = []

    def complete(self, prompt: str, *, images: Sequence[Any] = (), text: str | None = None,
                 expect: str = 'text', reasoning: bool = False,
                 tag: tuple[str, str, str] | None = None) -> str:
        call = Call(tuple(tag) if tag is not None else None, prompt, text, tuple(images),
                    expect, reasoning)
        self.calls.append(call)
        answer = self._answer(call)
        if answer is None:
            raise ModelError("no scripted response")
        # Same fence handling as the real client, so pipelines behave alike.
        return strip_fence(answer) if expect in ('json', 'code') else answer

    def _answer(self, call: Call) -> str | None:
        if callable(self.script):
            return self.script(call)
        if call.tag is None:
            return None
        submission, question, stage = call.tag
        for key in ((submission, question, stage), (question, stage), stage):
            if key in self.script:
                value = self.script[key]
                return value(call) if callable(value) else value
        return None
