"""SQLAlchemy models."""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    default_address = Column(String, nullable=False, default="")
    credits = Column(Integer, nullable=False, default=0)
    code_sites_table = Column(String, nullable=False, default="code_sites")
    is_admin = Column(Boolean, nullable=False, default=False)
    batch_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Distance(Base):
    __tablename__ = "distances"

    id = Column(Integer, primary_key=True, index=True)
    origin = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    distance_km = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("origin", "destination", name="uq_origin_destination"),
    )


class CodeSite(Base):
    __tablename__ = "code_sites"

    id = Column(Integer, primary_key=True, index=True)
    table_name = Column(String, nullable=False, index=True)
    code = Column(String, nullable=False)
    address = Column(String, nullable=False)


class GenerationLog(Base):
    __tablename__ = "generation_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    consultant_name = Column(String, nullable=False)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    target_km = Column(Float, nullable=False)
    num_days = Column(Integer, nullable=False)
    action = Column(String, nullable=False)  # "preview" or "pdf"
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CreditTransaction(Base):
    __tablename__ = "credit_txns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)  # positive=purchase, negative=spend
    stripe_session_id = Column(String, nullable=True)
    description = Column(String, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
