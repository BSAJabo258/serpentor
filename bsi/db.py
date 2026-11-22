"""Data model.

Design rules baked into the schema:
  * Originals are never edited; a Source row points at an immutable file by hash.
  * A person's self-identification, a community's term, a historical label and a
    government category are stored as DIFFERENT kinds of IdentityTerm and are never
    converted into one another.
  * A Claim has no verdict field anyone can type into. Its status is computed from
    the evidence linked to it (see claims.py).
"""
from datetime import datetime, timezone
from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime,
                        ForeignKey, Float, Boolean, JSON, UniqueConstraint)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from . import config

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    sha256 = Column(String(64), unique=True, nullable=False)
    original_name = Column(String)
    mime = Column(String)
    size_bytes = Column(Integer)
    title = Column(String)
    author = Column(String)
    created_date = Column(String)      # date the source itself was made (as written, e.g. "c. 1790")
    created_year = Column(Integer)     # normalized year for sorting, nullable
    origin_url = Column(String)
    holder = Column(String)            # archive / museum / institution holding the original
    # primary: made at the time by participants or witnesses
    # secondary: later scholarship working from primaries
    # tertiary: encyclopedias, popular retellings, later artistic depictions
    # unverified: provenance unknown
    tier = Column(String, default="unverified")
    sensitive = Column(Boolean, default=False)   # never leaves local hosts
    injection_flags = Column(JSON, default=list)
    registered_at = Column(DateTime(timezone=True), default=now)
    pages = relationship("Page", back_populates="source", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    page_no = Column(Integer, nullable=False)    # 1-based, as a human would cite it
    text = Column(Text)
    source = relationship("Source", back_populates="pages")


class AuditEvent(Base):
    """Append-only, hash-chained. Each row's hash covers the previous row's hash,
    so editing or deleting any past event breaks verification."""
    __tablename__ = "audit"
    id = Column(Integer, primary_key=True)
    at = Column(String, nullable=False)
    action = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False)


class Person(Base):
    __tablename__ = "persons"
    id = Column(Integer, primary_key=True)
    display_name = Column(String, nullable=False)
    birth_year = Column(Integer)
    death_year = Column(Integer)
    birth_place = Column(String)
    fields = Column(JSON, default=list)          # mathematics, medicine, music ...
    ftm_id = Column(String)                       # FollowTheMoney / Aleph entity id when synced
    external_ids = Column(JSON, default=dict)     # {"wikidata": "Q...", "viaf": "...", ...}
    tags = Column(JSON, default=list)             # discovery provenance, e.g. "wikidata:ethnic_group:Q49085"
    notes = Column(Text)
    names = relationship("PersonName", cascade="all, delete-orphan")
    identity_terms = relationship("IdentityTerm", cascade="all, delete-orphan")
    claims = relationship("Claim", back_populates="person", cascade="all, delete-orphan")


class PersonName(Base):
    __tablename__ = "person_names"
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    name = Column(String, nullable=False)
    kind = Column(String, default="alias")       # birth, chosen, alias, transliteration, title
    source_id = Column(Integer, ForeignKey("sources.id"))


IDENTITY_KINDS = {
    "self_identified",     # what the person called themselves, in their words
    "community_term",      # what their own community called them
    "historical_label",    # what a document of the period labeled them (kept verbatim)
    "government_category"  # an official classification (census, registry) — never treated as truth about the person
}


class IdentityTerm(Base):
    __tablename__ = "identity_terms"
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    term = Column(String, nullable=False)        # stored verbatim, never normalized
    kind = Column(String, nullable=False)
    period = Column(String)
    source_id = Column(Integer, ForeignKey("sources.id"))
    page_no = Column(Integer)


CLAIM_TYPES = {
    "contribution",   # invented / discovered / authored X
    "ancestry",       # descent or origin
    "depiction",      # how the person was portrayed, and whether that changed over time
    "attribution",    # credit for X was given to someone else
    "biographical",   # any other factual claim
    "succession",     # institution / asset / record passed from A to B
}


class Claim(Base):
    __tablename__ = "claims"
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id"))
    claim_type = Column(String, nullable=False)
    statement = Column(Text, nullable=False)
    alternatives = Column(JSON, default=list)    # competing hypotheses, stated up front
    negative_result = Column(Text)               # what finding would count against it
    needs_review = Column(Boolean, default=True)
    reviewer_note = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now)
    person = relationship("Person", back_populates="claims")
    evidence = relationship("EvidenceLink", back_populates="claim", cascade="all, delete-orphan")


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"), nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    page_no = Column(Integer)
    quote = Column(Text)                 # exact passage relied on
    stance = Column(String, nullable=False)   # supports | contradicts | context
    # for depiction claims: how the source portrays the person, verbatim or as described
    depiction_descriptor = Column(String)
    added_by = Column(String, default="human")   # human | model:<name> (model links always need review)
    claim = relationship("Claim", back_populates="evidence")
    source = relationship("Source")


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"))
    parent_question_id = Column(Integer, ForeignKey("questions.id"))
    text = Column(Text, nullable=False)
    record_types = Column(JSON, default=list)   # where the answer would live
    status = Column(String, default="open")      # open | answered | negative | blocked
    answer_note = Column(Text)
    __table_args__ = (UniqueConstraint("claim_id", "text"),)


class ResearchManifest(Base):
    """A saved, hash-identified research plan + the exact results it produced,
    so anyone can re-run it and compare."""
    __tablename__ = "manifests"
    id = Column(Integer, primary_key=True)
    spec_hash = Column(String(64), nullable=False)
    spec = Column(JSON, nullable=False)
    results = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=now)


_engine = None
_Session = None


def init(url: str | None = None):
    global _engine, _Session
    url = url or config.DATABASE_URL
    if url.startswith("sqlite:///"):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, future=True)
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def session():
    if _Session is None:
        init()
    return _Session()
