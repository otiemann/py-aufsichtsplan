import os
import sys
import time
import threading
import webbrowser

import uvicorn


def get_base_dir() -> str:
	# Unter PyInstaller onefile liegt add-data unter sys._MEIPASS
	base = getattr(sys, "_MEIPASS", None)
	if base:
		return base
	# Fallback: Projektverzeichnis (neben start.py)
	return os.path.dirname(os.path.abspath(__file__))


def open_browser_later(url: str, delay_seconds: float = 1.5) -> None:
	def _op():
		time.sleep(delay_seconds)
		try:
			webbrowser.open(url)
		except Exception:
			pass
	threading.Thread(target=_op, daemon=True).start()


if __name__ == "__main__":
	base_dir = get_base_dir()
	# Stelle sicher, dass relative Pfade (Templates, SQLite-DB) gefunden werden
	os.chdir(base_dir)

	open_browser_later("http://127.0.0.1:8000/plan/generate")
	uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
