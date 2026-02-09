import os
import sys
import shutil
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .db_config import get_app_data_dir, read_database_path_config

# Bestimme den Datenpfad
_db_path = os.environ.get("DATABASE_PATH")
if _db_path:
	_db_path = os.path.abspath(os.path.expanduser(os.path.expandvars(_db_path)))
if not _db_path:
	_db_path = read_database_path_config()
if not _db_path:
	data_dir = get_app_data_dir()

	target_filename = "aufsichtsplan.db"
	candidate_path = os.path.join(data_dir, target_filename)

	if not os.path.exists(candidate_path):
		legacy_paths = [os.path.join(data_dir, "vertretungsplan.db")]
		# Migration: ältere Versionen haben die DB ggf. direkt neben der EXE abgelegt.
		# Auf gemanagten Rechnern ist das Verzeichnis oft nicht beschreibbar – daher versuchen wir zu kopieren.
		if getattr(sys, "frozen", False):
			exe_dir = os.path.dirname(os.path.abspath(sys.executable))
			if os.path.abspath(exe_dir) != os.path.abspath(data_dir):
				legacy_paths.extend(
					[
						os.path.join(exe_dir, target_filename),
						os.path.join(exe_dir, "vertretungsplan.db"),
					]
				)

		for legacy_path in legacy_paths:
			if os.path.exists(legacy_path):
				try:
					shutil.copy2(legacy_path, candidate_path)
				except OSError:
					candidate_path = legacy_path
				break

	_db_path = candidate_path

db_dir = os.path.dirname(os.path.abspath(_db_path))
if db_dir:
	os.makedirs(db_dir, exist_ok=True)

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
