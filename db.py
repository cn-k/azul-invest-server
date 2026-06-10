import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Session

load_dotenv()

SCHEMA = "sahibinden"

# District statuses
STATUS_PENDING  = "pending"   # added to DB, initial load not started yet
STATUS_LOADING  = "loading"   # initial load in progress
STATUS_ACTIVE   = "active"    # initial load done, daily sync runs


class Base(DeclarativeBase):
    pass


class Apartment(Base):
    __tablename__ = "apartments"
    __table_args__ = {"schema": SCHEMA}

    sahibinden_id = Column(String, primary_key=True)
    title         = Column(String, nullable=True)
    price         = Column(String, nullable=True)
    location      = Column(String, nullable=True)   # ham konum metni (eski uyumluluk)
    neighbourhood = Column(String, nullable=True)   # mahalle (ör: "Atatürk Mah.")
    room_count    = Column(String, nullable=True)
    size_m2       = Column(String, nullable=True)
    floor         = Column(String, nullable=True)
    building_age  = Column(String, nullable=True)
    listing_type  = Column(String, nullable=True)  # "satilik-daire" | "kiralik-daire"
    district      = Column(String, nullable=True)
    url           = Column(String, nullable=True)
    ilan_tarihi   = Column(String, nullable=True)
    scraped_at    = Column(DateTime, default=datetime.utcnow)


class District(Base):
    """
    One row per district. Single source of truth.
    Add a new row here → system picks it up automatically.

    status flow:  pending → loading → active
    """
    __tablename__ = "districts"
    __table_args__ = {"schema": SCHEMA}

    id            = Column(Integer, primary_key=True, autoincrement=True)
    slug          = Column(String, unique=True, nullable=False)  # e.g. "ankara-etimesgut"
    display_name  = Column(String, nullable=True)                # e.g. "Ankara / Etimesgut"
    listing_type  = Column(String, default="satilik-daire")            # "satilik-daire" | "kiralik-daire"
    status        = Column(String, default=STATUS_PENDING)       # pending | loading | active
    # --- initial load progress ---
    current_page   = Column(Integer, default=0)
    current_offset = Column(Integer, default=0)
    last_sahibinden_id = Column(String, nullable=True)
    last_ilan_tarihi   = Column(String, nullable=True)
    total_scraped      = Column(Integer, default=0)
    # --- timestamps ---
    added_at      = Column(DateTime, default=datetime.utcnow)
    completed_at  = Column(DateTime, nullable=True)   # when initial load finished
    last_synced_at = Column(DateTime, nullable=True)  # last daily sync
    updated_at    = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine (singleton)
# ---------------------------------------------------------------------------

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)
    return _engine


def setup_tables():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.commit()
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# District helpers
# ---------------------------------------------------------------------------

def get_all_districts() -> list[District]:
    """Tüm district'ler."""
    with Session(get_engine()) as s:
        return s.query(District).all()


def get_pending_districts() -> list[District]:
    """Districts waiting for initial load."""
    with Session(get_engine()) as s:
        return s.query(District).filter_by(status=STATUS_PENDING).all()


def get_loading_districts() -> list[District]:
    """Districts whose initial load is in progress."""
    with Session(get_engine()) as s:
        return s.query(District).filter_by(status=STATUS_LOADING).all()


def get_active_districts() -> list[District]:
    """Districts with completed initial load — eligible for daily sync."""
    with Session(get_engine()) as s:
        return s.query(District).filter_by(status=STATUS_ACTIVE).all()


def get_all_work_districts() -> list[District]:
    """Districts that need any work: pending + loading."""
    with Session(get_engine()) as s:
        return s.query(District).filter(
            District.status.in_([STATUS_PENDING, STATUS_LOADING])
        ).all()


def start_loading(slug: str):
    with Session(get_engine()) as s:
        d = s.query(District).filter_by(slug=slug).first()
        if d and d.status == STATUS_PENDING:
            d.status = STATUS_LOADING
            d.updated_at = datetime.utcnow()
            s.commit()


def mark_initial_done(slug: str):
    with Session(get_engine()) as s:
        d = s.query(District).filter_by(slug=slug).first()
        if d:
            d.status = STATUS_ACTIVE
            d.completed_at = datetime.utcnow()
            d.updated_at = datetime.utcnow()
            s.commit()


def advance_offset(slug: str, step: int = 20, last_id: str = None,
                   last_tarih: str = None, batch_count: int = 0):
    with Session(get_engine()) as s:
        d = s.query(District).filter_by(slug=slug).first()
        if d:
            d.current_offset += step
            d.current_page = d.current_offset // step
            if last_id:
                d.last_sahibinden_id = last_id
            if last_tarih:
                d.last_ilan_tarihi = last_tarih
            d.total_scraped += batch_count
            d.updated_at = datetime.utcnow()
            s.commit()


def mark_synced(slug: str):
    with Session(get_engine()) as s:
        d = s.query(District).filter_by(slug=slug).first()
        if d:
            d.last_synced_at = datetime.utcnow()
            d.updated_at = datetime.utcnow()
            s.commit()


def get_offset(slug: str) -> int:
    with Session(get_engine()) as s:
        d = s.query(District).filter_by(slug=slug).first()
        return d.current_offset if d else 0


def seed_districts_from_json(
    json_path: str | Path = None,
    listing_type: str = "satilik-daire",
) -> int:
    """
    Read cities.json and insert any missing districts into the DB.
    Returns number of newly added districts.
    """
    if json_path is None:
        json_path = Path(__file__).parent / "cities.json"

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    added = 0
    for city in data.get("cities", []):
        city_name = city["name"].lower().strip()
        for district in city.get("districts", []):
            # Support both string ("etimesgut") and object ({"name": ..., "is_active": ...})
            if isinstance(district, str):
                district_name = district.lower().strip()
                is_active = True
            else:
                district_name = district["name"].lower().strip()
                is_active = district.get("is_active", True)

            if not is_active:
                continue  # skip inactive districts

            slug = f"{city_name}-{district_name}"
            display = f"{city_name.capitalize()} / {district_name.capitalize()}"
            if add_district(slug, display, listing_type):
                added += 1
    return added


def add_district(slug: str, display_name: str = None, listing_type: str = "satilik-daire"):
    """Add a new district. If already exists, does nothing."""
    with Session(get_engine()) as s:
        exists = s.query(District).filter_by(slug=slug).first()
        if not exists:
            s.add(District(slug=slug, display_name=display_name or slug, listing_type=listing_type))
            s.commit()
            return True
        return False


# ---------------------------------------------------------------------------
# Apartment helpers
# ---------------------------------------------------------------------------

def insert_apartments_batch(rows: list[dict]) -> int:
    """
    True bulk insert — one SQL statement for the whole page.
    Skips rows whose sahibinden_id already exists (ON CONFLICT DO NOTHING).
    Returns number of rows actually inserted.
    """
    if not rows:
        return 0
    # Filter out rows missing the primary key
    valid = [r for r in rows if r.get("sahibinden_id")]
    if not valid:
        return 0

    stmt = (
        pg_insert(Apartment)
        .values(valid)
        .on_conflict_do_nothing(index_elements=["sahibinden_id"])
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
        return result.rowcount
