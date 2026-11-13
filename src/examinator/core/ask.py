"""Asking the model one thing about one region: the one model call behind
printed fields, handwritten fields such as a student's name, and intake's
landmarks.

    Field('name', Region(1, 100, 200, 800, 280), read=Ask("the student's full name"))
    Field('side', region=Region(2, 0, 60, 1000, 240),
          read=Printed("the printed side length of the square, as a number (2, 3 or 5)"))

`Ask` asks `ask` about the field's region with its `prompt` (by default about
handwriting); `Printed` is `Ask` with a prompt about the PRINTED text. The
reading is the value: what to expect, and in what form, is the `ask`'s to say.
What the reply makes of the field is the reading layer's one rule
(`core.reading.value`): BLANK is the blank value, and a reply that states no
value (null, empty, missing) is not read: a review marker, with the printed
quote if the model gave one, which waits for a person.

A reader **vouches** for a value when `verify` is off and the value carries no
review marker: such a value is verified by the reader, and nobody need verify
it. `Printed` vouches by default (printed text is read reliably); `Ask`, about
handwriting, does not (a person verifies). A value nothing expects is caught
where it matters: a check that has no key for it is undecided, and says so.
A field with no region is not read. Every read asks the model; a read happens
only when a person asks for one.

A reader is any object with `read(ctx, fields) -> {label: text}`; it may also
have `vouches(field, text) -> bool`, `needs_region` and, for synthetic
workspaces, `script(fields, given) -> {step: reply}` (`examinator.synth`).
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .defs import has_marker
from .reading import BLANK, json_object, statement, value

if TYPE_CHECKING:
    from .context import ReadContext
    from .defs import Field

HANDWRITTEN_PROMPT = """\
Read the HANDWRITTEN text in this image of a region of an exam page.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"value": "the text as written"}}
Copy the handwriting character by character, even where it looks misspelled or incomplete. \
If nothing is handwritten there, the value is "BLANK". If the writing is unreadable, or the image \
does not show such a region the right way up, the value is JSON null (not the string "null")."""

PRINTED_PROMPT = """\
Read the PRINTED text in this image of an exam page region. Ignore all handwriting.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"quote": "a short quote of the printed text you relied on", "value": "the value"}}
Give the value in exactly the form the request above asks for (a number, a word, a JSON list); \
within that form copy what is printed exactly, with no added words or units. If the printed \
text is unreadable or absent, give JSON null for the value (not the string "null"). Do not infer \
the value from anything other than the printed text in this image."""


@dataclass(frozen=True)
class Ask:
    """Read a field's value by asking the model `ask` about its region (see the module)."""
    ask: str
    prompt: str = HANDWRITTEN_PROMPT
    verify: bool = True       # a person verifies every value read (else the reader vouches for it)
    needs_region = True

    def validate(self, what: str) -> None:
        if not self.ask.strip():
            raise ValueError(f"{what}: a field read by asking needs an `ask`")
        if not self.prompt.strip():
            raise ValueError(f"{what}: the prompt is empty")
        try:
            self.text()
        except (KeyError, IndexError, ValueError):
            raise ValueError(f"{what}: the prompt may have {{ask}} and no other braces "
                             f"(write a literal brace doubled: {{{{ }}}})") from None

    def text(self) -> str:
        """The prompt."""
        return self.prompt.format(ask=self.ask)

    def reply(self, complete: Callable[..., str], image: Any) -> dict:
        """The one model call: `complete(prompt, images=[image], expect='json')` (a
        model's `complete`, perhaps with a tag bound), as a JSON object ({} if it is not one)."""
        return json_object(complete(self.text(), images=[image], expect='json'))

    def call(self, complete: Callable[..., str], image: Any) -> str | None:
        """The reading the reply states (`reading.statement`), or None."""
        return statement(self.reply(complete, image).get('value'))

    def value(self, reply: Mapping[str, Any]) -> str:
        """The field's value for one reply (`reading.value`)."""
        return value(reply.get('value'), also=reply.get('quote'))

    def vouches(self, f: 'Field', text: str) -> bool:
        """A person need not verify this value."""
        return not self.verify and bool(text.strip()) and not has_marker(text)

    def read(self, ctx: 'ReadContext', fields: Sequence['Field']) -> dict[str, str]:
        return {f.label: self.value(self.reply(functools.partial(ctx.model, step=f'read:{f.label}'), ctx.crop(f.region)))
                for f in fields}

    def script(self, fields: Sequence['Field'], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: text, '' blank, None unreadable})."""
        def reply(text):
            return {'quote': None, 'value': None} if text is None else {'quote': "synthetic", 'value': text or BLANK}
        return {f'read:{f.label}': json.dumps(reply(given[f.label])) for f in fields if f.label in given}


@dataclass(frozen=True)
class Printed(Ask):
    """Read a field's value from the printed text in its region: `Ask` about
    the PRINTED text (see the module); it vouches for what it reads."""
    prompt: str = PRINTED_PROMPT
    verify: bool = False
