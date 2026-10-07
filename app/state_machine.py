"""Explicit lead state machine.

Every status change in the system must go through this module, so the legal
lifecycle of a lead is defined in exactly one place::

    new ─────────► qualifying ──► qualified / nurture / disqualified / failed
     │                ▲  │
     │                │  └──────► (terminal, may re-qualify or archive)
     └──► archived ◄──┴──────────── qualified / nurture / disqualified / failed
                    archived ──► new (restore)
"""

from __future__ import annotations

from enum import StrEnum


class LeadStatus(StrEnum):
    """Lifecycle states of a lead."""

    NEW = "new"
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    NURTURE = "nurture"
    DISQUALIFIED = "disqualified"
    FAILED = "failed"
    ARCHIVED = "archived"


# The single source of truth for legal transitions.
ALLOWED_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.NEW: frozenset({LeadStatus.QUALIFYING, LeadStatus.ARCHIVED}),
    LeadStatus.QUALIFYING: frozenset(
        {
            LeadStatus.QUALIFIED,
            LeadStatus.NURTURE,
            LeadStatus.DISQUALIFIED,
            LeadStatus.FAILED,
        }
    ),
    LeadStatus.QUALIFIED: frozenset(
        {LeadStatus.QUALIFYING, LeadStatus.DISQUALIFIED, LeadStatus.ARCHIVED}
    ),
    LeadStatus.NURTURE: frozenset(
        {LeadStatus.QUALIFYING, LeadStatus.QUALIFIED, LeadStatus.ARCHIVED}
    ),
    LeadStatus.DISQUALIFIED: frozenset({LeadStatus.QUALIFYING, LeadStatus.ARCHIVED}),
    LeadStatus.FAILED: frozenset({LeadStatus.QUALIFYING, LeadStatus.ARCHIVED}),
    LeadStatus.ARCHIVED: frozenset({LeadStatus.NEW}),
}


class InvalidTransitionError(Exception):
    """Raised on an illegal state transition."""

    def __init__(self, current: LeadStatus | str, target: LeadStatus | str) -> None:
        self.current = str(current)
        self.target = str(target)
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[LeadStatus(current)]))
        super().__init__(
            f"Cannot move lead from '{self.current}' to '{self.target}'. Allowed: [{allowed}]"
        )


def can_transition(current: LeadStatus | str, target: LeadStatus | str) -> bool:
    """Return True when ``current -> target`` is a legal transition."""
    return target in ALLOWED_TRANSITIONS[LeadStatus(current)]


def assert_transition(current: LeadStatus | str, target: LeadStatus | str) -> None:
    """Raise :class:`InvalidTransitionError` when the transition is illegal."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)
