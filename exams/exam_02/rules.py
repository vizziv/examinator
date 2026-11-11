"""Grade rules: from field outcomes to this exam's marks."""
from examinator import Grade

WORK = "judge the work"


def all_of(outcomes) -> str | None:
    """`math.all_correct` over some of the outcomes: a mistake outranks an
    undecided field, which outranks a partly blank answer (incorrect)."""
    outcomes = list(outcomes)
    if 'incorrect' in outcomes:
        return 'incorrect'
    if not outcomes or None in outcomes:
        return None
    if all(o == 'blank' for o in outcomes):
        return 'blank'
    return 'incorrect' if 'blank' in outcomes else 'correct'


def at_least(k: int):
    """Credit for at least k correct fields. Undecided only if the undecided fields could tip it."""
    def rule(evidence):
        outcomes = evidence.outcomes()
        correct, open_ = outcomes.count('correct'), outcomes.count(None)
        if correct >= k:
            return 'correct'
        if correct + open_ >= k:
            return None
        return 'blank' if outcomes and all(o == 'blank' for o in outcomes) else 'incorrect'
    return rule


def tiers(**parts):
    """Each named mark needs all of its listed fields: `tiers(check=[...], shiny=[...])`."""
    def rule(evidence):
        return {name: all_of(evidence.outcome(label) for label in labels) for name, labels in parts.items()}
    return rule


def with_work(mark: str):
    """Two parts: 'answer' follows the fields; `mark` is the credit, which also
    needs work on the page. A right answer leaves it for a person; a wrong or
    blank one settles it."""
    def rule(evidence):
        answer = all_of(evidence.outcomes())
        return {'answer': answer, mark: Grade(None, reason=WORK) if answer == 'correct' else answer}
    return rule


def field(label: str):
    """The mark is one field's outcome."""
    def rule(evidence):
        return evidence.outcome(label)
    return rule
