"""Domain and application errors."""

from __future__ import annotations

import uuid


class LeadError(Exception):
    """Base class for lead-related application errors."""


class LeadNotFoundError(LeadError):
    """Raised when a lead id does not exist."""

    def __init__(self, lead_id: uuid.UUID | str) -> None:
        self.lead_id = str(lead_id)
        super().__init__(f"Lead '{self.lead_id}' was not found")


class DuplicateLeadError(LeadError):
    """Raised when a lead with the same normalized email already exists."""

    def __init__(self, email: str, existing_id: uuid.UUID | str) -> None:
        self.email = email
        self.existing_id = str(existing_id)
        super().__init__(f"A lead with email '{email}' already exists (id={self.existing_id})")
