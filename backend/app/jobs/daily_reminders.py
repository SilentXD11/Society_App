"""Daily reminder job. Run once a day (cron, EventBridge, or a k8s CronJob):

    python -m app.jobs.daily_reminders

Each society is processed in its own transaction and its own time zone.
Re-running on the same day is safe: notification_log.dedupe_key stops repeats.
"""
import asyncio
import json
import logging

from sqlalchemy import select

from app.db import dispose_engine, sessionmaker
from app.models import Society
from app.services.reminders import run_for_society

log = logging.getLogger("daily_reminders")


async def main() -> list[dict]:
    results = []
    async with sessionmaker()() as session:
        ids = (await session.execute(select(Society.id))).scalars().all()
    for sid in ids:
        async with sessionmaker()() as session:
            try:
                society = await session.get(Society, sid)
                results.append(await run_for_society(session, society))
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("Reminder run failed for society %s", sid)
                results.append({"society_id": str(sid), "error": True})
    await dispose_engine()
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(asyncio.run(main()), indent=2))
