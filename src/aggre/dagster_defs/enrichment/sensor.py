"""Enrichment sensor -- watches for content ready for cross-source discovery."""

import sqlalchemy as sa

from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverContent

enrichment_sensor = make_processing_sensor(
    name="enrichment_sensor",
    target=enrich_job,
    query=sa.select(SilverContent.id).where(
        SilverContent.text.isnot(None),
        SilverContent.canonical_url.isnot(None),
        SilverContent.enriched_at.is_(None),
    ),
    run_key_prefix="enrich",
    skip_message="No content needs enrichment",
    minimum_interval_seconds=120,
)
