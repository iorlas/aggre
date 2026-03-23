"""One-off: emit item.new events for untranscribed YouTube videos."""

import sqlalchemy as sa
from hatchet_sdk.clients.events import PushEventOptions

from aggre.config import load_config
from aggre.db import SilverContent, SilverDiscussion
from aggre.utils.db import get_engine
from aggre.workflows.models import SilverContentRef
from aggre.workflows.worker import get_hatchet

cfg = load_config()
engine = get_engine(cfg.settings.database_url)
h = get_hatchet()

with engine.connect() as conn:
    rows = conn.execute(
        sa.select(
            SilverDiscussion.id,
            SilverDiscussion.content_id,
            SilverDiscussion.external_id,
            SilverContent.domain,
        )
        .join(SilverContent, SilverContent.id == SilverDiscussion.content_id)
        .where(
            SilverDiscussion.source_type == "youtube",
            SilverContent.text.is_(None),
        )
        # No limit — backfill all untranscribed videos
    ).all()

for row in rows:
    event = SilverContentRef(
        content_id=row.content_id,
        discussion_id=row.id,
        source="youtube",
        domain=row.domain,
    )
    h.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
    print(f"Pushed item.new for content_id={row.content_id} external_id={row.external_id}")

print(f"Done. Pushed {len(rows)} events.")
