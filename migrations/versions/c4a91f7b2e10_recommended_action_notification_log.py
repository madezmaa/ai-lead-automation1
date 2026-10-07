"""recommended action + notification log

Revision ID: c4a91f7b2e10
Revises: 3abd2a8db0cc
Create Date: 2026-10-07 22:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a91f7b2e10"
down_revision: str | Sequence[str] | None = "3abd2a8db0cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "qualification_results",
        sa.Column("recommended_action", sa.String(length=64), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE qualification_results SET recommended_action = "
        "CASE decision "
        "WHEN 'qualified' THEN 'sales_follow_up' "
        "WHEN 'nurture' THEN 'add_to_nurture' "
        "WHEN 'disqualified' THEN 'disqualify' "
        "ELSE 'manual_review' END"
    )
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="webhook"),
        sa.Column("target", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_logs_lead_id", "notification_logs", ["lead_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_notification_logs_lead_id", table_name="notification_logs")
    op.drop_table("notification_logs")
    op.drop_column("qualification_results", "recommended_action")
