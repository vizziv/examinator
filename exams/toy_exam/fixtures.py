"""Synthetic cases for the toy exam."""
from examinator.synth import Case


def cases():
    return [
        Case('toy-red', printed={'areas': {'side': '2'}},
             answers={'areas': {'A.1': '4', 'A.2': '4x', 'A.3': 'x**2 * 2'}, 'parity': {'B': 'even'}},
             expect={'areas': 3, 'parity': True}),
        Case('toy-green', printed={'areas': {'side': '3'}},
             answers={'areas': {'A.1': '9', 'A.2': '4*x', 'A.3': '3*x^2'}, 'parity': {'B': 'odd'}},
             expect={'areas': 2, 'parity': False}),
        Case('toy-blue', printed={'areas': {'side': '5'}},
             answers={'areas': {'A.1': None, 'A.2': None, 'A.3': None}, 'parity': {'B': None}},
             expect={'areas': 'blank', 'parity': 'blank'}),
        Case('toy-unread', printed={'areas': {'side': None}},
             answers={'areas': {'A.1': '4', 'A.2': '4*x', 'A.3': '2*x**2'}, 'parity': {'B': '(E)'}},
             expect={'areas': None, 'parity': True}),
        # A printed side with no key: the check is undecided, and says so.
        Case('toy-unexpected', printed={'areas': {'side': '7'}},
             answers={'areas': {'A.1': '4', 'A.2': '4*x', 'A.3': '2*x**2'}, 'parity': {'B': 'even'}},
             expect={'areas': None, 'parity': True}),
    ]
