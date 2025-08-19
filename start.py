import os
import sys
import time
import threading
import webbrowser
import logging
import logging.config
import subprocess
import urllib.request

import uvicorn

# Hinweis: Import der App erfolgt erst im __main__-Block nach Umgebungs-Setup


def get_base_dir() -> str:
	base = getattr(sys, "_MEIPASS", None)
	if base:
		return base
	return os.path.dirname(os.path.abspath(__file__))


def get_data_dir() -> str:
	if os.name == "nt":
		base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
		path = os.path.join(base, "py-vertretungsplan")
	else:
		base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
		path = os.path.join(base, "py-vertretungsplan")
	os.makedirs(path, exist_ok=True)
	return path


def try_open_url(url: str) -> None:
	try:
		if webbrowser.open(url):
			return
	except Exception:
		pass
	if os.name == "nt":
		try:
			os.startfile(url)  # type: ignore[attr-defined]
			return
		except Exception:
			pass
		try:
			subprocess.run(["cmd", "/c", "start", "", url], check=False)
			return
		except Exception:
			pass


def notify_user(url: str) -> None:
	if os.name == "nt":
		try:
			import ctypes  # type: ignore
			MB_ICONINFORMATION = 0x40
			MB_TOPMOST = 0x00040000
			ctypes.windll.user32.MessageBoxW(None, f"Die Anwendung laeuft. Oeffne im Browser: {url}", "Vertretungsplan", MB_ICONINFORMATION | MB_TOPMOST)
		except Exception:
			pass


def open_browser_when_ready(url: str, timeout_seconds: float = 15.0) -> None:
	def _op():
		deadline = time.time() + timeout_seconds
		while time.time() < deadline:
			try:
				with urllib.request.urlopen(url, timeout=1.5) as resp:  # nosec B310
					if 200 <= getattr(resp, "status", 200) < 500:
						break
			except Exception:
				time.sleep(0.5)
		try_open_url(url)
		notify_user(url)
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

	data_dir = get_data_dir()
	os.environ["APP_DATA_DIR"] = data_dir

	exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
	os.chdir(exe_dir)

	log_file = os.path.join(data_dir, "app.log")
	log_config = build_logging_config(log_file)

	url = "http://127.0.0.1:8000"
	open_browser_when_ready(url)

	# Import jetzt, nachdem ENV & CWD gesetzt sind
	from app.main import app as fastapi_app  # type: ignore

	try:
		uvicorn.run(
			fastapi_app,
			host="127.0.0.1",
			port=8000,
			reload=False,
			log_config=log_config,
		)
	except OSError:
		alt_url = "http://127.0.0.1:8001"
		open_browser_when_ready(alt_url)
		uvicorn.run(
			fastapi_app,
			host="127.0.0.1",
			port=8001,
			reload=False,
			log_config=log_config,
		)
