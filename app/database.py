import os
import sys
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Bestimme den Datenpfad
_db_path = os.environ.get("DATABASE_PATH")
if not _db_path:
	data_dir = os.environ.get("APP_DATA_DIR")
	if not data_dir:
		if getattr(sys, "frozen", False):
			data_dir = os.path.dirname(os.path.abspath(sys.executable))
		else:
			data_dir = os.path.dirname(os.path.abspath(__file__))
	os.makedirs(data_dir, exist_ok=True)

	target_filename = "aufsichtsplan.db"
	candidate_path = os.path.join(data_dir, target_filename)

	if not os.path.exists(candidate_path):
		legacy_names = [
			os.path.join(data_dir, "vertretungsplan.db"),
		]
		for legacy_path in legacy_names:
			if os.path.exists(legacy_path):
				try:
					os.replace(legacy_path, candidate_path)
				except OSError:
					candidate_path = legacy_path
				break

	_db_path = candidate_path

# SQLite-URL mit absolutem Pfad
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.abspath(_db_path)}"

engine = create_engine(
	SQLALCHEMY_DATABASE_URL,
	connect_args={"check_same_thread": False, "timeout": 30},
	future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
	cursor = dbapi_connection.cursor()
	try:
		cursor.execute("PRAGMA busy_timeout=5000")
		cursor.execute("PRAGMA journal_mode=WAL")
	finally:
		cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()
