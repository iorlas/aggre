"""Reset stage_tracking rows where extract=done but silver_content.text is NULL.

These rows were created by a bug where trafilatura.extract() returned None
but the code still called upsert_done(). Setting them to 'failed' allows
the retry mechanism to re-attempt extraction.

Revision ID: 008
Revises: 007
Create Date: 2026-03-01
"""

from __future__ import annotations

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE stage_tracking st
        SET status = 'failed',
            error = 'no_extractable_content (reset by migration)'
        FROM silver_content sc
        WHERE st.source = 'webpage'
          AND st.stage = 'extract'
          AND st.status = 'done'
          AND st.external_id = sc.canonical_url
          AND sc.text IS NULL
    """)


def downgrade() -> None:
    pass  # Non-reversible data fix
