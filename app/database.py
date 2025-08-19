import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Bestimme den Datenpfad
_db_path = os.environ.get("DATABASE_PATH")
if not _db_path:
	data_dir = os.environ.get("APP_DATA_DIR") or os.getcwd()
	os.makedirs(data_dir, exist_ok=True)
	_db_path = os.path.join(data_dir, "vertretungsplan.db")

# SQLite-URL mit absolutem Pfad
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.abspath(_db_path)}"

engine = create_engine(
	SQLALCHEMY_DATABASE_URL,
	connect_args={"check_same_thread": False},
	future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()
