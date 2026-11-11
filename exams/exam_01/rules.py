"""Grade rules for this exam's credit criteria."""
from examinator import Grade


def at_least(k: int, required: tuple[str, ...] = ()):
    """Correct when at least `k` fields are correct, all of `required` among them.

    Incorrect once that is out of reach; undecided while undecided fields could
    still tip it; all blank is blank.
    """
    def rule(evidence):
        outcomes = {label: f.get('outcome') for label, f in evidence.fields.items()}
        values = list(outcomes.values())
        if values and all(o == 'blank' for o in values):
            return 'blank'
        correct, open_ = values.count('correct'), values.count(None)
        must = [outcomes.get(label) for label in required]
        if any(o in ('incorrect', 'blank') for o in must) or correct + open_ < k:
            return 'incorrect'
        if correct >= k and all(o == 'correct' for o in must):
            return 'correct'
        waiting = [label for label, o in outcomes.items() if o is None]
        return Grade(None, reason=f"undecided: {', '.join(waiting)}")
    return rule


def single(label: str):
    """The question's result is one field's outcome; its note explains an undecided one."""
    def rule(evidence):
        f = evidence.fields.get(label) or {}
        if f.get('outcome') is None:
            return Grade(None, reason=f.get('note') or "undecided")
        return f['outcome']
    return rule
