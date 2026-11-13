"""Examinator: a small language for building exam graders."""
from .core.context import BadOutput, CheckContext, ReadContext, Unavailable
from .core.ask import Ask, Printed
from .core.defs import Bool, Check, Exam, Field, Grade, Landmark, Number, Question, Region, load_exam
from .core.focus import Facet
from .core.result import Judgement, Result, Undecided

__all__ = ['Ask', 'BadOutput', 'Bool', 'Check', 'CheckContext', 'Exam', 'Facet', 'Field', 'Grade',
           'Judgement', 'Landmark', 'Number', 'Printed', 'Question', 'ReadContext', 'Region', 'Result',
           'Unavailable', 'Undecided', 'load_exam']
