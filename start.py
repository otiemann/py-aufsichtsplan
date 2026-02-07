import os
import sys
import time
import threading
import socket
import webbrowser
import logging
import logging.config
import subprocess
import urllib.request
import faulthandler
import multiprocessing as mp

import uvicorn

# Hinweis: Import der App erfolgt erst im __main__-Block nach Umgebungs-Setup


def get_base_dir() -> str:
	base = getattr(sys, "_MEIPASS", None)
	if base:
		return base
	return os.path.dirname(os.path.abspath(__file__))


def get_data_dir() -> str:
	# Wichtig: Auf gemanagten Windows-Rechnern ist das EXE-Verzeichnis oft nicht beschreibbar (keine Admin-Rechte).
	# Deshalb bevorzugen wir einen Benutzer-spezifischen Datenordner.
	if os.name == "nt":
		base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
		if base:
			path = os.path.join(base, "Aufsichtsplan")
		else:
			path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Aufsichtsplan")
	else:
		if getattr(sys, "frozen", False):
			path = os.path.dirname(os.path.abspath(sys.executable))
		else:
			path = os.path.dirname(os.path.abspath(__file__))
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
			ctypes.windll.user32.MessageBoxW(None, f"Die Anwendung laeuft. Oeffne im Browser: {url}", "Pausenaufsichtsplan", MB_ICONINFORMATION | MB_TOPMOST)
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
			"uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
			"uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
			"uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
			"app": {"handlers": ["default"], "level": "INFO", "propagate": False},
		},
	}


def write_startup_stamp(log_file: str, base_dir: str, data_dir: str) -> None:
	try:
		stamp = time.strftime("%Y-%m-%d %H:%M:%S")
		exe = sys.executable
		pid = os.getpid()
		subproc = os.environ.get("SCHEDULER_SOLVER_SUBPROCESS", "")
		cwd = os.getcwd()
		with open(log_file, "a", encoding="utf-8") as handle:
			handle.write(
				f"[startup] {stamp} pid={pid} exe={exe} cwd={cwd} base_dir={base_dir} data_dir={data_dir} solver_subprocess={subproc}\n"
			)
	except Exception:
		pass


def try_build_logging_config(log_file: str) -> dict | None:
	try:
		# Test ob Log-Datei angelegt/beschreibbar ist
		os.makedirs(os.path.dirname(log_file), exist_ok=True)
		with open(log_file, "a", encoding="utf-8"):
			pass
		return build_logging_config(log_file)
	except OSError as exc:
		print(f"[WARN] Log-Datei {log_file} nicht beschreibbar ({exc}). Fallback auf Standard-Logging.")
		return None


def pick_free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
		s.bind(("127.0.0.1", 0))
		return int(s.getsockname()[1])


def is_port_available(port: int) -> bool:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
		try:
			s.bind(("127.0.0.1", port))
		except OSError:
			return False
		return True


def pick_preferred_port(preferred_ports: list[int]) -> int:
	for port in preferred_ports:
		if is_port_available(port):
			return port
	return pick_free_port()


def run_browser(fastapi_app, log_config: dict | None) -> None:
	port = pick_preferred_port([8000, 8001])
	url = f"http://127.0.0.1:{port}"
	open_browser_when_ready(url)
	uvicorn.run(
		fastapi_app,
		host="127.0.0.1",
		port=port,
		reload=False,
		log_config=log_config,
	)


def wait_until_ready(url: str, timeout_seconds: float = 15.0) -> None:
	deadline = time.time() + timeout_seconds
	last_exc: Exception | None = None
	while time.time() < deadline:
		try:
			with urllib.request.urlopen(url, timeout=1.5) as resp:  # nosec B310
				if 200 <= getattr(resp, "status", 200) < 500:
					return
		except Exception as exc:
			last_exc = exc
			time.sleep(0.5)
	if last_exc:
		raise last_exc


def run_server_in_thread(fastapi_app, host: str, port: int, log_config: dict | None):
	config = uvicorn.Config(
		fastapi_app,
		host=host,
		port=port,
		reload=False,
		log_config=log_config,
	)
	server = uvicorn.Server(config)
	thread = threading.Thread(target=server.run, daemon=True)
	thread.start()
	return server, thread


def run_desktop(fastapi_app, log_config: dict | None) -> None:
	# Desktop-Mode: In-App WebView, kein externer Browser.
	try:
		import webview  # type: ignore
	except Exception as exc:
		# In der gepackten Windows-App ist WebView verpflichtend.
		if os.name == "nt" and getattr(sys, "frozen", False):
			try:
				import ctypes  # type: ignore

				MB_ICONERROR = 0x10
				MB_TOPMOST = 0x00040000
				ctypes.windll.user32.MessageBoxW(
					None,
					"Desktop-UI konnte nicht gestartet werden (WebView fehlt).\n\n"
					"Bitte pywebview/WebView2 Runtime prüfen.\n\n"
					f"Details: {exc}",
					"Aufsichtsplan",
					MB_ICONERROR | MB_TOPMOST,
				)
			except Exception:
				pass
			raise
		# Dev-Fallback: öffne im Browser.
		run_browser(fastapi_app, log_config)
		return

	port = pick_free_port()
	url = f"http://127.0.0.1:{port}"
	server, thread = run_server_in_thread(fastapi_app, host="127.0.0.1", port=port, log_config=log_config)

	# Warten bis der Server antwortet, damit die WebView nicht leer startet.
	wait_until_ready(url, timeout_seconds=20.0)

	window = webview.create_window(
		"Aufsichtsplan",
		url,
		width=1200,
		height=850,
		min_size=(1000, 700),
	)

	try:
		# Auf Windows explizit Edge/WebView2 nutzen, falls vorhanden.
		gui = "edgechromium" if os.name == "nt" else None
		if gui:
			webview.start(gui=gui)
		else:
			webview.start()
	finally:
		# App-Fenster geschlossen → Server beenden.
		server.should_exit = True
		thread.join(timeout=5.0)


if __name__ == "__main__":
	# Notwendig für multiprocessing in PyInstaller/Windows.
	if os.name == "nt":
		mp.freeze_support()

	base_dir = get_base_dir()
	os.environ["APP_RESOURCES_DIR"] = base_dir

	data_dir = get_data_dir()
	os.environ["APP_DATA_DIR"] = data_dir

	exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
	os.chdir(exe_dir)

	# Stabilere OR-Tools Defaults auf Windows (verhindert native Crashes bei CP-SAT).
	if os.name == "nt":
		os.environ.setdefault("OMP_NUM_THREADS", "1")
		os.environ.setdefault("MKL_NUM_THREADS", "1")
		os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
		os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
		os.environ.setdefault("ORTOOLS_NUM_THREADS", "1")
		os.environ.setdefault("SCHEDULER_NUM_WORKERS", "1")
		# Solver im Subprozess ausführen, um native Crashes abzufangen.
		os.environ.setdefault("SCHEDULER_SOLVER_SUBPROCESS", "1")

	log_file = os.path.join(data_dir, "app.log")
	log_config = try_build_logging_config(log_file)
	write_startup_stamp(log_file, base_dir, data_dir)
	# Schreibe native Crash-Backtraces in die Logdatei (hilft bei EXE-Abstürzen).
	try:
		_fault_log = open(log_file, "a", encoding="utf-8")
		faulthandler.enable(file=_fault_log)
	except Exception:
		pass

	# Import jetzt, nachdem ENV & CWD gesetzt sind
	from app.main import app as fastapi_app  # type: ignore

	desktop_mode = os.environ.get("APP_DESKTOP", "1").lower() not in {"0", "false", "off", "no"}
	if desktop_mode:
		run_desktop(fastapi_app, log_config)
	else:
		run_browser(fastapi_app, log_config)
