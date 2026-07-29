"""
SQLAlchemy setup and ORM model for persisted classification results.
Uses SQLite by default (DATABASE_URL in .env.local to override).
"""
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Float, Boolean, DateTime, Text, Integer, ForeignKey, LargeBinary
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
    theme = Column(Text)  # LLM-synthesized cross-doc theme
    # Cached contradiction-analysis result — JSON array, computed on demand
    contradictions_json = Column(Text)
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

    # Extracted document text — enables cross-doc features (Q&A, contradictions)
    doc_text = Column(Text)

    # Human corrections (feedback loop) — set when a consultant overrides a label.
    # The original classifier prediction stays in doc_type_label / industry.
    user_doc_type = Column(String)
    user_industry = Column(String)

    # Portfolio association
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True)
    portfolio = relationship("Portfolio", back_populates="records")

    @property
    def pain_points(self) -> list[dict]:
        return json.loads(self.pain_points_json or "[]")

    @pain_points.setter
    def pain_points(self, value: list[dict]):
        self.pain_points_json = json.dumps(value)


class LLMCache(Base):
    """
    Content-addressed cache of LLM pipeline results.

    Key = sha256 of (pipeline name + prompt version + inputs). Guarantees that
    an unchanged document re-classified later produces byte-identical analysis —
    temp-0 sampling alone cannot promise that — and makes repeat runs free.
    """
    __tablename__ = "llm_cache"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)  # JSON-encoded pipeline result
    created_at = Column(DateTime(timezone=True), nullable=False)


class DocumentChunk(Base):
    """
    Embedded text chunk of a classified document. Written at classify time;
    read by retrieval features (portfolio Q&A). Embedding is a float32 vector
    stored as raw bytes (see app/pipelines/chunks.py for encode/decode).
    """
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(Integer, ForeignKey("classifications.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)


# Columns added after the initial schema — naive additive migration for SQLite.
# (table_name, column_name, SQL type)
_MIGRATIONS = [
    ("classifications", "doc_text", "TEXT"),
    ("classifications", "user_doc_type", "VARCHAR"),
    ("classifications", "user_industry", "VARCHAR"),
    ("portfolios", "contradictions_json", "TEXT"),
]


def init_db():
    Base.metadata.create_all(bind=engine)
    # Add any missing columns to pre-existing tables (SQLite has no built-in
    # migrations; ADD COLUMN is safe and idempotent here).
    with engine.connect() as conn:
        for table, column, sql_type in _MIGRATIONS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
        conn.commit()


def get_session() -> Session:
    return Session(engine)
