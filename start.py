import os
import sys
import time
import threading
import webbrowser
import logging
import logging.config

import uvicorn

# Direktimport der App, damit PyInstaller alle Module erkennt
try:
	from app.main import app as fastapi_app  # type: ignore
except Exception:
	fastapi_app = None


def get_base_dir() -> str:
	base = getattr(sys, "_MEIPASS", None)
	if base:
		return base
	return os.path.dirname(os.path.abspath(__file__))


def open_browser_later(url: str, delay_seconds: float = 1.5) -> None:
	def _op():
		time.sleep(delay_seconds)
		try:
			webbrowser.open(url)
		except Exception:
			pass
	threading.Thread(target=_op, daemon=True).start()


def build_logging_config(log_file: str) -> dict:
	return {
		"version": 1,
		"disable_existing_loggers": False,
		"formatters": {
			"default": {
				"()": "uvicorn.logging.DefaultFormatter",
				"fmt": "%(levelprefix)s %(message)s",
				"use_colors": False,
			},
			"access": {
				"()": "uvicorn.logging.AccessFormatter",
				"fmt": "%(client_addr)s - \"%(request_line)s\" %(status_code)s",
				"use_colors": False,
			},
		},
		"handlers": {
			"default": {
				"class": "logging.FileHandler",
				"filename": log_file,
				"mode": "a",
				"encoding": "utf-8",
				"formatter": "default",
			},
			"access": {
				"class": "logging.FileHandler",
				"filename": log_file,
				"mode": "a",
				"encoding": "utf-8",
				"formatter": "access",
			},
		},
		"loggers": {
			"uvicorn": {"handlers": ["default"], "level": "INFO"},
			"uvicorn.error": {"handlers": ["default"], "level": "INFO"},
			"uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
		},
	}


if __name__ == "__main__":
	base_dir = get_base_dir()
	os.environ["APP_RESOURCES_DIR"] = base_dir
	exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
	os.chdir(exe_dir)

	log_file = os.path.join(exe_dir, "app.log")
	log_config = build_logging_config(log_file)

	open_browser_later("http://127.0.0.1:8000/plan/generate")

	if fastapi_app is not None:
		uvicorn.run(
			fastapi_app,
			host="127.0.0.1",
			port=8000,
			reload=False,
			log_config=log_config,
		)
	else:
		# Fallback: Import zur Laufzeit (sollte dank Direktimport oben selten nötig sein)
		uvicorn.run(
			"app.main:app",
			host="127.0.0.1",
			port=8000,
			reload=False,
			log_config=log_config,
		)
