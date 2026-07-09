"""
SQLAlchemy setup and ORM model for persisted classification results.
Uses SQLite by default (DATABASE_URL in .env.local to override).
"""
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Float, Boolean, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Session, relationship

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # needed for SQLite
)


class Base(DeclarativeBase):
    pass


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    theme = Column(Text)  # Haiku-synthesized cross-doc theme
    records = relationship("ClassificationRecord", back_populates="portfolio")


class ClassificationRecord(Base):
    __tablename__ = "classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    classified_at = Column(DateTime(timezone=True), nullable=False)

    # Document type
    doc_type_label = Column(String)
    doc_type_probability = Column(Float)
    doc_type_needs_review = Column(Boolean, default=False)

    # Industry (classifier-detected or user-provided)
    industry = Column(String)
    industry_probability = Column(Float)
    industry_needs_review = Column(Boolean, default=False)
    industry_user_provided = Column(Boolean, default=False)

    # Pain points — stored as JSON array of {label, similarity_score}
    pain_points_json = Column(Text, default="[]")

    # Confidentiality
    confidentiality_label = Column(String)
    confidentiality_rationale = Column(Text)
    confidentiality_confidence = Column(Float)
    confidentiality_needs_review = Column(Boolean)

    # Importance
    importance_label = Column(String)
    importance_rationale = Column(Text)
    importance_confidence = Column(Float)
    importance_needs_review = Column(Boolean)

    # AI-generated summary
    summary = Column(Text)

    error = Column(Text)

    # Portfolio association
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True)
    portfolio = relationship("Portfolio", back_populates="records")

    @property
    def pain_points(self) -> list[dict]:
        return json.loads(self.pain_points_json or "[]")

    @pain_points.setter
    def pain_points(self, value: list[dict]):
        self.pain_points_json = json.dumps(value)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    return Session(engine)
