"""What a check and a reader receive.

A check is `fn(ctx: CheckContext) -> Result`. It reads field values with
`ctx.read(label)` (its own question's field) or `ctx.read(label,
question='info')` (another question's). Every read is recorded with the
field's revision, so the result is outdated when a field it read changes, and
its grade counts only once every field it read is settled.

A reader is called with a `ReadContext`: the pages and crops of one
submission, and the loopback model. Inside an isolated check there are no
pages and no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence

from .defs import Question, Region, has_marker, key
from .result import Undecided

if TYPE_CHECKING:
    from PIL.Image import Image

    from ..pdf.layout import Layout


class Unavailable(RuntimeError):
    """Asked for something this run cannot provide (pages, the model)."""


class BadOutput(RuntimeError):
    """A model's output did not have the required structure. The message is
    structural only: it never quotes the output."""


@dataclass
class CheckContext:
    submission: str
    question: Question
    check: str
    # every field's stored value in this submission: {'question/label': (rev, text)}
    values: Mapping[str, tuple[int, str]] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)   # per-run, from a grader
    fields: frozenset[str] = frozenset()      # the exam's fields ('question/label'): what may be read
    reads: dict[str, int | None] = field(default_factory=dict)     # what was read: revision, None if not read

    def read(self, label: str, *, question: str | None = None) -> str:
        """One field's value (text; '' is blank). Recorded. A field that is not
        read, or whose value carries a review marker, raises `Undecided`."""
        q = question or self.question.id
        k = key(q, label)
        if k not in self.fields:
            raise KeyError(f"no field {label!r} in {q}")
        got = self.values.get(k)
        self.reads[k] = got[0] if got is not None else None
        name = label if q == self.question.id else f"{q} {label}"
        if got is None:
            raise Undecided(f"{name} not read")
        if has_marker(got[1]):
            raise Undecided(f"{name} has a review marker")
        return got[1]


class Services(Protocol):
    """What the runner lends a reader."""

    def pages(self, submission: str, question: Question) -> list['Image']: ...
    def crop(self, submission: str, region: Region) -> 'Image': ...
    def layout(self, submission: str) -> 'Layout': ...
    def model(self, prompt: str, *, images: Sequence['Image'], text: str | None,
              expect: str, reasoning: bool, tag: tuple[str, str, str]) -> str: ...
    def memo(self, submission: str, key: str) -> str | None: ...
    def set_memo(self, submission: str, key: str, value: str) -> None: ...


@dataclass
class ReadContext:
    submission: str
    question: Question
    note: str = ""                  # the grader's note about this submission's pages, for prompts
    services: Services | None = None
    reasoning: bool = False         # "retry with reasoning" for this run
    fresh: bool = False             # ask again rather than use a remembered reading

    def pages(self) -> list['Image']:
        """Rendered images of the question's selected pages for this submission."""
        return self._services().pages(self.submission, self.question)

    def crop(self, region: Region) -> 'Image':
        """One rendered region, mapped through the scan layout."""
        return self._services().crop(self.submission, region)

    def layout(self) -> 'Layout':
        """This submission's scan layout (cover pages, print area): what a crop depends on."""
        return self._services().layout(self.submission)

    def model(self, prompt: str, *, images: Sequence['Image'] = (), text: str | None = None,
              expect: str = 'text', step: str = 'read') -> str:
        """Call the loopback model. `expect` is 'text', 'json' or 'code'; `step`
        names the call (it tags it, for a scripted model and for diagnostics)."""
        return self._services().model(
            prompt, images=images, text=text, expect=expect, reasoning=self.reasoning,
            tag=(self.submission, self.question.id, step))

    def remember(self, key: str, compute: Callable[[], str]) -> str:
        """A per-submission memo (a printed field's reading); `fresh` asks again."""
        s = self._services()
        if not self.fresh:
            hit = s.memo(self.submission, key)
            if hit is not None:
                return hit
        value = compute()
        s.set_memo(self.submission, key, value)
        return value

    def _services(self) -> Services:
        if self.services is None:
            raise Unavailable("no pages or model here")
        return self.services
