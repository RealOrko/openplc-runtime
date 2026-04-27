"""
Patches for asyncua 1.1.8.

OPC UA Part 4 §7.7.4.5 defines SimpleAttributeOperand.BrowsePath as a
QualifiedName array; an empty array means "the Node is the instance of the
TypeDefinition" — the canonical form mandated by Part 9 §5.5.2 Table 1010
for retrieving ConditionId in an event filter
(typeDefinitionId=ConditionType, browsePath=[], attributeId=NodeId).

OPC UA Part 6 binary encoding allows an empty array on the wire as either
length=0 (decoded as []) or length=-1 (decoded as None). open62541 emits
length=-1 for an unset/empty browsePath, and asyncua's binary decoder
turns that into Python None. asyncua's Event.to_event_fields and
Event.from_event_fields then call len(sattr.BrowsePath) and crash with
"object of type 'NoneType' has no len()", silently dropping every
condition-rooted alarm event for the affected subscription.

asyncua's own where-clause evaluator already handles this correctly
(server/monitored_item_service.py:354 uses a truthy check). The bug is
isolated to events.py.

This module restores spec conformance by coercing BrowsePath to []
before invoking the upstream methods. Imported from the package
__init__ so any sibling import picks it up before asyncua's event
machinery runs.

Remove this file once a fix lands upstream and the pinned asyncua
version in requirements.txt includes it.
"""

from asyncua.common import events as _events


def _normalize(select_clauses):
    for sattr in select_clauses:
        if sattr.BrowsePath is None:
            sattr.BrowsePath = []


_orig_to_event_fields = _events.Event.to_event_fields
_orig_from_event_fields = _events.Event.from_event_fields


def _patched_to_event_fields(self, select_clauses):
    _normalize(select_clauses)
    return _orig_to_event_fields(self, select_clauses)


def _patched_from_event_fields(select_clauses, fields):
    _normalize(select_clauses)
    return _orig_from_event_fields(select_clauses, fields)


_events.Event.to_event_fields = _patched_to_event_fields
_events.Event.from_event_fields = staticmethod(_patched_from_event_fields)
