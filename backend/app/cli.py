"""Bootstrap a society and its first committee member (who can then add everyone else):

    python -m app.cli create-society --name "Shanti Kunj CHS" --admin-name "R. Shah" \
        --admin-phone +919820000001 --monthly-charge 2500
"""
import argparse
import asyncio
from decimal import Decimal

from app.db import dispose_engine, sessionmaker
from app.models import Membership, Society
from app.routers.society import get_or_create_user
from app.services import audit


async def create_society(a) -> None:
    async with sessionmaker()() as session:
        s = Society(name=a.name, address=a.address, monthly_charge=Decimal(a.monthly_charge), due_day=a.due_day,
                    timezone=a.timezone, currency=a.currency)
        session.add(s)
        await session.flush()
        u = await get_or_create_user(session, a.admin_name, a.admin_phone, a.admin_email)
        session.add(Membership(society_id=s.id, user_id=u.id, role="committee", title=a.admin_title,
                               is_signatory=True))
        await audit.record(session, society_id=s.id, actor=None, action="society.create", entity="society",
                           entity_id=s.id, detail={"admin": u.id})
        await session.commit()
        print(f"Society {s.name} created: {s.id}\nFirst committee member: {u.full_name} ({u.phone or u.email})")
    await dispose_engine()


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-society")
    c.add_argument("--name", required=True)
    c.add_argument("--address")
    c.add_argument("--admin-name", required=True)
    c.add_argument("--admin-phone")
    c.add_argument("--admin-email")
    c.add_argument("--admin-title", default="Secretary")
    c.add_argument("--monthly-charge", default="0")
    c.add_argument("--due-day", type=int, default=10)
    c.add_argument("--timezone", default="Asia/Kolkata")
    c.add_argument("--currency", default="INR")
    a = p.parse_args()
    if not (a.admin_phone or a.admin_email):
        p.error("give --admin-phone or --admin-email")
    asyncio.run(create_society(a))


if __name__ == "__main__":
    main()
