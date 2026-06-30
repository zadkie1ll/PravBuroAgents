from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from app.config import settings
from app.security import hash_password, verify_password

Base = declarative_base()
engine_kwargs = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True)
    email = Column(String(254), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    password_hash = Column(String(256), nullable=False)
    referral_code = Column(String(32), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    leads = relationship("Lead", back_populates="agent")


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"

    token = Column(String(64), primary_key=True)
    email = Column(String(254), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    password_hash = Column(String(256), nullable=False)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)
    parent_lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    name = Column(String(160), nullable=False)
    phone = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    bitrix_lead_id = Column(BigInteger, nullable=True, index=True)
    referral_code = Column(String(32), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    moderated_at = Column(DateTime, nullable=True)
    work_started_at = Column(DateTime, nullable=True)
    reject_reason = Column(Text, nullable=True)

    agent = relationship("Agent", back_populates="leads")
    parent_lead = relationship("Lead", remote_side=[id])
    payouts = relationship("Payout", back_populates="lead")


class Payout(Base):
    __tablename__ = "payouts"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    level = Column(Integer, nullable=False)
    amount_rub = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="accrued")
    created_at = Column(DateTime, nullable=False, default=utcnow)

    lead = relationship("Lead", back_populates="payouts")

    __table_args__ = (
        UniqueConstraint("agent_id", "lead_id", "level", name="uix_payout_once"),
    )


@dataclass(frozen=True)
class ReferralTarget:
    agent_id: int
    parent_lead_id: int | None


EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


def initialize_storage() -> None:
    Base.metadata.create_all(engine)


def session_scope() -> Session:
    return SessionLocal()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def email_is_valid(value: str) -> bool:
    value = normalize_email(value)
    return len(value) <= 254 and EMAIL_RE.fullmatch(value) is not None


def normalize_phone(value: str) -> str:
    value = re.sub(r"[^\d+]", "", value.strip())
    if value.startswith("8") and len(value) == 11:
        value = "+7" + value[1:]
    if value.startswith("7") and len(value) == 11:
        value = "+" + value
    return value


def phone_is_valid(value: str) -> bool:
    return re.fullmatch(r"\+?\d{10,15}", value) is not None


def _code_hash(token: str, code: str) -> str:
    return hashlib.sha256(f"{token}:{code}".encode("utf-8")).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _new_referral_code() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:10]


def unique_referral_code(session: Session) -> str:
    while True:
        code = _new_referral_code()
        agent_exists = session.scalar(select(Agent.id).where(Agent.referral_code == code))
        lead_exists = session.scalar(select(Lead.id).where(Lead.referral_code == code))
        if not agent_exists and not lead_exists:
            return code


def create_pending_registration(name: str, email: str, password: str) -> tuple[str, str]:
    email = normalize_email(email)
    token = secrets.token_hex(24)
    code = _new_code()
    with session_scope() as session:
        if session.scalar(select(Agent.id).where(Agent.email == email)):
            raise ValueError("Пользователь с такой почтой уже есть")
        session.query(PendingRegistration).filter(PendingRegistration.email == email).delete()
        pending = PendingRegistration(
            token=token,
            email=email,
            name=name.strip(),
            password_hash=hash_password(password),
            code_hash=_code_hash(token, code),
            expires_at=utcnow() + timedelta(seconds=settings.registration_code_ttl_seconds),
        )
        session.add(pending)
        session.commit()
    return token, code


def get_pending_registration(token: str | None) -> PendingRegistration | None:
    if not token:
        return None
    with session_scope() as session:
        pending = session.get(PendingRegistration, token)
        if pending is None or pending.expires_at < utcnow():
            return None
        return pending


def confirm_registration(token: str | None, code: str) -> Agent | None:
    if not token:
        return None
    with session_scope() as session:
        pending = session.get(PendingRegistration, token)
        if pending is None or pending.expires_at < utcnow():
            return None
        if pending.attempts >= 5:
            return None
        if pending.code_hash != _code_hash(token, code.strip()):
            pending.attempts += 1
            session.commit()
            return None
        agent = Agent(
            email=pending.email,
            name=pending.name,
            password_hash=pending.password_hash,
            referral_code=unique_referral_code(session),
        )
        session.add(agent)
        session.delete(pending)
        session.commit()
        return agent


def authenticate_agent(email: str, password: str) -> Agent | None:
    with session_scope() as session:
        agent = session.scalar(select(Agent).where(Agent.email == normalize_email(email)))
        if agent and verify_password(password, agent.password_hash):
            return agent
        return None


def get_or_create_debug_agent() -> Agent:
    email = normalize_email(settings.debug_agent_email)
    with session_scope() as session:
        agent = session.scalar(select(Agent).where(Agent.email == email))
        if agent:
            return agent
        agent = Agent(
            email=email,
            name=settings.debug_agent_name.strip() or "Тестовый агент",
            password_hash=hash_password(settings.debug_agent_password),
            referral_code=unique_referral_code(session),
        )
        session.add(agent)
        session.commit()
        return agent


def get_agent(agent_id: int | None) -> Agent | None:
    if agent_id is None:
        return None
    with session_scope() as session:
        return session.get(Agent, agent_id)


def resolve_referral(code: str) -> ReferralTarget | None:
    with session_scope() as session:
        agent = session.scalar(select(Agent).where(Agent.referral_code == code))
        if agent:
            return ReferralTarget(agent_id=agent.id, parent_lead_id=None)
        lead = session.scalar(select(Lead).where(Lead.referral_code == code))
        if lead and lead.status in {"approved", "work_started"}:
            return ReferralTarget(agent_id=lead.agent_id, parent_lead_id=lead.id)
    return None


def create_lead(target: ReferralTarget, name: str, phone: str) -> Lead:
    with session_scope() as session:
        lead = Lead(
            agent_id=target.agent_id,
            parent_lead_id=target.parent_lead_id,
            name=name.strip(),
            phone=normalize_phone(phone),
            referral_code=unique_referral_code(session),
        )
        session.add(lead)
        session.commit()
        return lead


def get_lead(lead_id: int) -> Lead | None:
    with session_scope() as session:
        return session.get(Lead, lead_id)


def set_lead_approved(lead_id: int, bitrix_lead_id: int) -> Lead | None:
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if not lead or lead.status != "pending":
            return lead
        lead.status = "approved"
        lead.bitrix_lead_id = bitrix_lead_id
        lead.moderated_at = utcnow()
        session.commit()
        return lead


def set_lead_rejected(lead_id: int, reason: str = "") -> Lead | None:
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if not lead or lead.status != "pending":
            return lead
        lead.status = "rejected"
        lead.reject_reason = reason
        lead.moderated_at = utcnow()
        session.commit()
        return lead


def mark_work_started(lead_id: int) -> list[Payout]:
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if not lead or lead.status not in {"approved", "work_started"}:
            return []
        if lead.status != "work_started":
            lead.status = "work_started"
            lead.work_started_at = utcnow()
        level = 2 if lead.parent_lead_id else 1
        amount = (
            settings.second_level_reward_rub
            if lead.parent_lead_id
            else settings.direct_reward_rub
        )
        exists = session.scalar(
            select(Payout.id).where(
                Payout.agent_id == lead.agent_id,
                Payout.lead_id == lead.id,
                Payout.level == level,
            )
        )
        payouts: list[Payout] = []
        if not exists:
            payouts.append(
                Payout(
                    agent_id=lead.agent_id,
                    lead_id=lead.id,
                    level=level,
                    amount_rub=amount,
                )
            )
        session.add_all(payouts)
        session.commit()
        return payouts


def dashboard_data(agent_id: int) -> dict:
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        leads = list(
            session.scalars(
                select(Lead).where(Lead.agent_id == agent_id).order_by(Lead.created_at.desc())
            )
        )
        total_earned = session.scalar(
            select(func.coalesce(func.sum(Payout.amount_rub), 0)).where(Payout.agent_id == agent_id)
        )
        return {"agent": agent, "leads": leads, "total_earned": int(total_earned or 0)}


def leaderboard(limit: int = 10) -> list[tuple[str, int, int]]:
    with session_scope() as session:
        rows = session.execute(
            select(
                Agent.name,
                func.count(Lead.id),
                func.coalesce(func.sum(Payout.amount_rub), 0),
            )
            .outerjoin(Lead, Lead.agent_id == Agent.id)
            .outerjoin(Payout, Payout.agent_id == Agent.id)
            .group_by(Agent.id)
            .order_by(func.coalesce(func.sum(Payout.amount_rub), 0).desc(), func.count(Lead.id).desc())
            .limit(limit)
        ).all()
        return [(str(name), int(count or 0), int(amount or 0)) for name, count, amount in rows]


def pending_leads(limit: int = 10) -> list[Lead]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Lead).where(Lead.status == "pending").order_by(Lead.created_at.asc()).limit(limit)
            )
        )
