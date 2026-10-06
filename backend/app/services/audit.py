import uuid
from decimal import Decimal
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def _jsonable(v):
    if isinstance(v, (Decimal,)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


async def record(session: AsyncSession, *, society_id: uuid.UUID | None, actor: uuid.UUID | None,
                 action: str, entity: str, entity_id=None, detail: dict | None = None,
                 ip: str | None = None) -> None:
    """Append an audit entry in the same transaction as the change it describes."""
    session.add(AuditLog(society_id=society_id, actor_user_id=actor, action=action, entity=entity,
                         entity_id=str(entity_id) if entity_id is not None else None,
                         detail=_jsonable(detail) if detail else None, ip=ip))
