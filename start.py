import os
import sys
import time
import threading
import webbrowser

import uvicorn


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


if __name__ == "__main__":
	base_dir = get_base_dir()
	# Templates/Static sollen im EXE-Paket gesucht werden
	os.environ["APP_RESOURCES_DIR"] = base_dir
	# DB im Arbeitsordner (neben EXE) – sicherstellen, dass wir dort arbeiten
	exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
	os.chdir(exe_dir)

	open_browser_later("http://127.0.0.1:8000/plan/generate")
	uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
