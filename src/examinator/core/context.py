"""What a check and a reader receive.

A check is `fn(ctx: CheckContext) -> Result`. It reads field values with
`ctx.read(label)` (its own question's field) or `ctx.read(label,
question='info')` (another question's). Every read is recorded with the
field's revision (`DEFAULT` for a field read at its default), so the result is
outdated when a field it read changes, and its grade counts only once every
field it read is verified.

A reader is called with a `ReadContext`: the pages and crops of one
submission, and the loopback model. Every read asks the model: nothing but
the value a read writes is kept. Inside an isolated check there are no pages
and no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Protocol, Sequence

from .defs import DEFAULT, Question, Region, key
from .result import Undecided

if TYPE_CHECKING:
    from PIL.Image import Image


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
    fields: Mapping[str, str | None] = field(default_factory=dict)    # `Exam.fields`: what may be read
    reads: dict[str, int | None] = field(default_factory=dict)     # what was read: revision, None if not read

    def read(self, label: str, *, question: str | None = None) -> str:
        """One field's value (text; '' is blank), or its default while it has no
        stored value. Recorded. A field that is not read raises `Undecided` (one
        that needs review holds the grade back in the status rules)."""
        q = question or self.question.id
        k = key(q, label)
        if k not in self.fields:
            raise KeyError(f"no field {label!r} in {q}")
        got = self.values.get(k)
        if got is None and self.fields[k] is not None:
            got = (DEFAULT, self.fields[k])
        self.reads[k] = got[0] if got is not None else None
        name = label if q == self.question.id else f"{q} {label}"
        if got is None:
            raise Undecided(f"{name} not read")
        return got[1]


class Services(Protocol):
    """What the runner lends a reader."""

    def pages(self, submission: str, question: Question) -> list['Image']: ...
    def crop(self, submission: str, region: Region) -> 'Image': ...
    def model(self, prompt: str, *, images: Sequence['Image'], text: str | None,
              expect: str, tag: tuple[str, str, str]) -> str: ...


@dataclass
class ReadContext:
    submission: str
    question: Question
    services: Services | None = None

    def pages(self) -> list['Image']:
        """Rendered images of the question's selected pages for this submission."""
        return self._services().pages(self.submission, self.question)

    def crop(self, region: Region) -> 'Image':
        """One rendered region, mapped through the scan layout."""
        return self._services().crop(self.submission, region)

    def model(self, prompt: str, *, images: Sequence['Image'] = (), text: str | None = None,
              expect: str = 'text', step: str = 'read') -> str:
        """Call the loopback model. `expect` is 'text', 'json' or 'code'; `step`
        names the call (it tags it, for a scripted model and for diagnostics)."""
        return self._services().model(
            prompt, images=images, text=text, expect=expect, tag=(self.submission, self.question.id, step))

    def _services(self) -> Services:
        if self.services is None:
            raise Unavailable("no pages or model here")
        return self.services
