"""The evidence block vocabulary, as an exam writes it: `Result(grade, fields,
blocks=[Image(png), Details("Samples", Table(...))])`. The grammar and its
limits are the core's (`core.result`)."""
from ..core.result import Code, Details, Image, Note, Table

__all__ = ['Code', 'Details', 'Image', 'Note', 'Table']
