"""The stage context: what a stage function receives.

A stage function is `fn(ctx) -> body`. Inside an isolated check there are no
services: `ctx.pages()`, `ctx.crop()` and `ctx.model()` raise `Unavailable`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence

from .defs import Question, Region

if TYPE_CHECKING:
    from PIL.Image import Image


class Unavailable(RuntimeError):
    """The stage asked for something this run cannot provide (pages, the model)."""


class Blocked(RuntimeError):
    """The stage cannot run yet. The message says why, in a few words."""


class BadOutput(RuntimeError):
    """A model's output did not have the required structure. The message is
    structural only: it never quotes the output."""


class Services(Protocol):
    """What the runner lends a stage that runs in-process."""

    def pages(self, submission: str, question: Question) -> list['Image']: ...
    def crop(self, submission: str, region: Region) -> 'Image': ...
    def model(self, prompt: str, *, images: Sequence['Image'], text: str | None,
              expect: str, reasoning: bool, tag: tuple[str, str, str]) -> str: ...
    def memo(self, submission: str, key: str) -> str | None: ...
    def set_memo(self, submission: str, key: str, value: str) -> None: ...


@dataclass
class Context:
    submission: str
    question: Question
    stage: str
    variant: str | None = None                  # resolved variant name, if any
    inputs: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)   # per-run, from the grader
    note: str = ""                              # per-submission page note, for prompts
    rerun: bool = False                         # the stage's artifact already exists
    reasoning: bool = False                     # "retry with reasoning" for this run
    services: Services | None = None

    def input(self, kind: str) -> Any:
        """Body of an upstream artifact named in the stage's `inputs`."""
        return self.inputs[kind]

    def pages(self) -> list['Image']:
        """Rendered images of the question's selected pages for this submission."""
        return self._services().pages(self.submission, self.question)

    def crop(self, region: Region) -> 'Image':
        """One rendered region, mapped through the scan layout."""
        return self._services().crop(self.submission, region)

    def model(self, prompt: str, *, images: Sequence['Image'] = (), text: str | None = None,
              expect: str = 'text') -> str:
        """Call the loopback model. `expect` is 'text', 'json' or 'code'."""
        return self._services().model(
            prompt, images=images, text=text, expect=expect, reasoning=self.reasoning,
            tag=(self.submission, self.question.id, self.stage))

    def remember(self, key: str, compute: Callable[[], str], *, refresh: bool = False) -> str:
        """A per-submission memo shared across questions (e.g. one cue read once)."""
        s = self._services()
        if not refresh:
            hit = s.memo(self.submission, key)
            if hit is not None:
                return hit
        value = compute()
        s.set_memo(self.submission, key, value)
        return value

    def _services(self) -> Services:
        if self.services is None:
            raise Unavailable("not available inside an isolated stage")
        return self.services
