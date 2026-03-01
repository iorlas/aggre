"""Content sensor -- watches for content needing text extraction."""

import sqlalchemy as sa

from aggre.dagster_defs.content.job import SKIP_DOMAINS, content_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverContent

content_sensor = make_processing_sensor(
    name="content_sensor",
    target=content_job,
    query=sa.select(SilverContent.id).where(
        SilverContent.text.is_(None),
        SilverContent.error.is_(None),
        sa.or_(
            SilverContent.domain.notin_(SKIP_DOMAINS),
            SilverContent.domain.is_(None),
        ),
    ),
    run_key_prefix="content",
    skip_message="No content needs processing",
)
