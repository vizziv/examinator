"""Synthetic cases for the toy exam."""
from examinator.synth import Case


def cases():
    return [
        Case('toy-red', variants={'areas': 'red'},
             answers={'areas': {'A.1': '4', 'A.2': '4x', 'A.3': 'x**2 * 2'}, 'parity': {'B': 'even'}},
             expect={'areas': 3, 'parity': True}),
        Case('toy-green', variants={'areas': 'green'},
             answers={'areas': {'A.1': '9', 'A.2': '4*x', 'A.3': '3*x^2'}, 'parity': {'B': 'odd'}},
             expect={'areas': 2, 'parity': False}),
        Case('toy-blue', variants={'areas': 'blue'},
             answers={'areas': {'A.1': None, 'A.2': None, 'A.3': None}, 'parity': {'B': None}},
             expect={'areas': 'blank', 'parity': 'blank'}),
        Case('toy-unread', variants={'areas': 'red'}, readings={'areas_side': None},
             answers={'areas': {'A.1': '4', 'A.2': '4*x', 'A.3': '2*x**2'}, 'parity': {'B': '(E)'}},
             expect={'areas': None, 'parity': True}),
    ]
