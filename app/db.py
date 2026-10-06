from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    # hide_parameters keeps report text out of SQLAlchemy error messages,
    # which would otherwise end up in tracebacks and logs.
    engine = create_engine(url, connect_args={"check_same_thread": False}, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def turn_on_foreign_keys(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def create_tables():
    from app import models  # noqa: F401  (registers the tables on Base)

    Base.metadata.create_all(engine)


def get_db():
    with SessionLocal() as db:
        yield db
