"""Society settings, members (who can log in and with what role), flats and residents."""
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Flat, Membership, Resident, User
from app.schemas import FlatIn, FlatOut, MemberIn, MemberOut, ResidentIn, ResidentOut, SocietyOut, SocietyUpdate
from app.security import otp
from app.security.deps import AnyMember, CommitteeMember, Session, StepUp
from app.services import audit

router = APIRouter(prefix="/societies/{society_id}", tags=["society"])


@router.get("", response_model=SocietyOut)
async def get_society(m: AnyMember):
    return m.society


@router.patch("", response_model=SocietyOut)
async def update_society(body: SocietyUpdate, m: CommitteeMember, session: Session):
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(m.society, k, v)
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="society.update",
                       entity="society", entity_id=m.society.id, detail=changes)
    return m.society


# ---------------------------------------------------------------- members
async def get_or_create_user(session, full_name: str, phone: str | None, email: str | None) -> User:
    phone = otp.normalise(phone)[1] if phone else None
    email = email.strip().lower() if email else None
    user = None
    if phone:
        user = await otp.find_user(session, "sms", phone)
    if user is None and email:
        user = await otp.find_user(session, "email", email)
    if user is None:
        user = User(full_name=full_name, phone=phone, email=email)
        session.add(user)
        await session.flush()
    else:
        user.phone = user.phone or phone
        user.email = user.email or email
    return user


@router.get("/members", response_model=list[MemberOut])
async def list_members(m: CommitteeMember, session: Session):
    rows = (await session.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                                  .where(Membership.society_id == m.society.id).order_by(User.full_name))).all()
    return [MemberOut(user_id=u.id, full_name=u.full_name, phone=u.phone, email=u.email, role=ms.role,
                      title=ms.title, is_signatory=ms.is_signatory, active=ms.active) for ms, u in rows]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(body: MemberIn, m: CommitteeMember, session: Session, _: StepUp):
    """Give someone access. Adding committee members or signatories is sensitive, so it needs step-up."""
    user = await get_or_create_user(session, body.full_name, body.phone, body.email)
    ms = (await session.execute(select(Membership).where(Membership.society_id == m.society.id,
                                                         Membership.user_id == user.id))).scalar_one_or_none()
    if ms is None:
        ms = Membership(society_id=m.society.id, user_id=user.id, role=body.role)
        session.add(ms)
    ms.role, ms.title, ms.is_signatory, ms.active = body.role, body.title, body.is_signatory, True
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="member.upsert", entity="membership",
                       entity_id=ms.id, detail={"user": user.id, "role": body.role, "signatory": body.is_signatory})
    return MemberOut(user_id=user.id, full_name=user.full_name, phone=user.phone, email=user.email,
                     role=ms.role, title=ms.title, is_signatory=ms.is_signatory, active=ms.active)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, m: CommitteeMember, session: Session, _: StepUp):
    ms = (await session.execute(select(Membership).where(Membership.society_id == m.society.id,
                                                         Membership.user_id == user_id))).scalar_one_or_none()
    if ms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found.")
    if ms.role == "committee":
        others = await session.scalar(select(func.count()).select_from(Membership).where(
            Membership.society_id == m.society.id, Membership.role == "committee", Membership.active,
            Membership.user_id != user_id))
        if not others:
            raise HTTPException(status.HTTP_409_CONFLICT, "A society needs at least one committee member.")
    ms.active, ms.is_signatory = False, False
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="member.remove",
                       entity="membership", entity_id=ms.id)


# ---------------------------------------------------------------- flats & residents
def flat_out(f: Flat) -> FlatOut:
    return FlatOut.model_validate(f)


@router.get("/flats", response_model=list[FlatOut])
async def list_flats(m: AnyMember, session: Session):
    flats = (await session.execute(select(Flat).where(Flat.society_id == m.society.id))).scalars().all()
    return sorted((flat_out(f) for f in flats), key=lambda f: (f.wing, len(f.number), f.number))


@router.post("/flats", response_model=FlatOut, status_code=status.HTTP_201_CREATED)
async def add_flat(body: FlatIn, m: CommitteeMember, session: Session):
    f = Flat(society_id=m.society.id, **body.model_dump())
    session.add(f)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Flat {body.wing}-{body.number} already exists.")
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="flat.create", entity="flat",
                       entity_id=f.id, detail=body.model_dump())
    return flat_out(f)


@router.put("/flats/{flat_id}", response_model=FlatOut)
async def update_flat(flat_id: uuid.UUID, body: FlatIn, m: CommitteeMember, session: Session):
    f = await session.get(Flat, flat_id)
    if f is None or f.society_id != m.society.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flat not found.")
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="flat.update", entity="flat",
                       entity_id=f.id, detail=body.model_dump())
    return flat_out(f)


@router.get("/flats/{flat_id}/residents", response_model=list[ResidentOut])
async def list_residents(flat_id: uuid.UUID, m: CommitteeMember, session: Session):
    return (await session.execute(select(Resident).where(Resident.society_id == m.society.id,
                                                         Resident.flat_id == flat_id))).scalars().all()


@router.post("/flats/{flat_id}/residents", response_model=ResidentOut, status_code=status.HTTP_201_CREATED)
async def add_resident(flat_id: uuid.UUID, body: ResidentIn, m: CommitteeMember, session: Session):
    f = await session.get(Flat, flat_id)
    if f is None or f.society_id != m.society.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flat not found.")
    phone = otp.normalise(body.phone)[1] if body.phone else None
    user_id = None
    if body.link_login and (phone or body.email):
        user = await get_or_create_user(session, body.full_name, phone, body.email)
        user_id = user.id
        exists = await session.scalar(select(Membership.id).where(Membership.society_id == m.society.id,
                                                                  Membership.user_id == user.id))
        if not exists:
            session.add(Membership(society_id=m.society.id, user_id=user.id, role=body.kind))
    r = Resident(society_id=m.society.id, flat_id=flat_id, user_id=user_id, kind=body.kind,
                 full_name=body.full_name, phone=phone, email=body.email, moved_in=body.moved_in)
    session.add(r)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="resident.add", entity="resident",
                       entity_id=r.id, detail={"flat": f.label, "kind": body.kind})
    return r
