"""CRM integration boundary.

Builds a plain, CRM-ready record from persisted application data. No external
CRM is contacted: downstream systems (n8n, an iPaaS, an export job) consume
this record through ``GET /api/v1/leads/{id}/crm-record`` or by fetching the
qualification payload from the notification webhook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import Lead, QualificationResult


def build_crm_record(lead: Lead, result: QualificationResult) -> dict[str, Any]:
    """Return a stable, CRM-oriented view of a qualified lead."""
    return {
        "external_id": f"lead-{lead.id}",
        "source": lead.source,
        "lead_status": lead.status,
        "contact": {
            "email": lead.email,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "phone": lead.phone,
            "job_title": lead.job_title,
        },
        "company": {
            "name": lead.company,
            "website": lead.website,
            "industry": lead.industry,
            "country": lead.country,
            "country_code": lead.country_code,
            "employee_count": lead.company_size,
            "declared_budget": float(lead.budget) if lead.budget is not None else None,
        },
        "qualification": {
            "qualification_id": result.id,
            "decision": result.decision,
            "score": result.score,
            "recommended_action": result.recommended_action,
            "reason": result.reason,
            "ai_used": result.ai_used,
            "model": result.model,
            "qualified_at": result.created_at.isoformat() if result.created_at else None,
        },
        "notes": lead.message,
    }
