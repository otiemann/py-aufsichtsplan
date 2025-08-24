"""
Windows Auto-Update System für py-aufsichtsplan
Separates Tool das GitHub Releases überwacht und Updates installiert
"""

import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Optional, Tuple
from pathlib import Path


class AutoUpdater:
    def __init__(self, current_version: str = "1.0.0"):
        self.current_version = current_version
        self.github_repo = "otiemann/py-aufsichtsplan"  # Anpassen an Ihr Repo
        self.api_base = f"https://api.github.com/repos/{self.github_repo}"
        self.exe_name = "Aufsichtsplan.exe"
        
    def check_for_updates(self) -> Optional[Dict]:
        """Prüft GitHub Releases API nach neuen Versionen"""
        try:
            # Prüfe alle Releases (auch Prereleases) da wir Beta-Versionen verwenden
            url = f"{self.api_base}/releases"
            with urllib.request.urlopen(url, timeout=10) as response:
                releases_data = json.loads(response.read().decode())
            
            # Finde das neueste Release mit Assets
            latest_release = None
            for release in releases_data:
                if release.get("assets") and any(asset["name"] == self.exe_name for asset in release["assets"]):
                    latest_release = release
                    break
            
            if not latest_release:
                return None
                
            latest_version = latest_release["tag_name"].lstrip("v")
            
            if self._is_newer_version(latest_version, self.current_version):
                # Finde download URL für Windows EXE
                download_url = None
                for asset in latest_release.get("assets", []):
                    if asset["name"] == self.exe_name:
                        download_url = asset["browser_download_url"]
                        break
                        
                if download_url:
                    return {
                        "version": latest_version,
                        "download_url": download_url,
                        "release_notes": latest_release.get("body", ""),
                        "published_at": latest_release.get("published_at", ""),
                        "size": next((a["size"] for a in latest_release["assets"] if a["name"] == self.exe_name), 0)
                    }
                    
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            print(f"Fehler beim Prüfen auf Updates: {e}")
            return None
            
        return None
        
    def _is_newer_version(self, latest: str, current: str) -> bool:
        """Vergleicht Versionsnummern (basic semantic versioning)"""
        def version_tuple(v):
            # Entferne Beta/Alpha-Suffixe für Vergleich
            v_clean = v.replace('-beta', '').replace('-alpha', '').replace('-rc', '')
            return tuple(map(int, v_clean.split(".")))
            
        try:
            return version_tuple(latest) > version_tuple(current)
        except ValueError:
            return False
            
    def download_update(self, download_url: str, progress_callback=None) -> Optional[str]:
        """Lädt Update herunter und gibt Pfad zur neuen EXE zurück"""
        try:
            temp_dir = tempfile.mkdtemp(prefix="aufsichtsplan_update_")
            temp_file = os.path.join(temp_dir, self.exe_name)
            
            print(f"Lade Update herunter: {download_url}")
            
            def reporthook(blocknum, blocksize, totalsize):
                if progress_callback and totalsize > 0:
                    progress = min(100, (blocknum * blocksize * 100) // totalsize)
                    progress_callback(progress)
                    
            urllib.request.urlretrieve(download_url, temp_file, reporthook)
            
            if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                print(f"Download abgeschlossen: {temp_file}")
                return temp_file
                
        except Exception as e:
            print(f"Fehler beim Download: {e}")
            
        return None
        
    def install_update(self, new_exe_path: str) -> bool:
        """Installiert das Update (ersetzt aktuelle EXE)"""
        if not os.path.exists(new_exe_path):
            return False
            
        try:
            current_exe = sys.executable if getattr(sys, 'frozen', False) else None
            if not current_exe:
                print("Kann aktuelle EXE nicht finden (nicht als gefrorene App ausgeführt)")
                return False
                
            # Backup der aktuellen Version
            backup_path = current_exe + ".backup"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            shutil.copy2(current_exe, backup_path)
            
            # PowerShell Script für verzögerte Installation (umgeht Datei-Locks)
            ps_script = f'''
            Start-Sleep -Seconds 2
            try {{
                if (Test-Path "{current_exe}") {{
                    Remove-Item "{current_exe}" -Force
                }}
                Copy-Item "{new_exe_path}" "{current_exe}" -Force
                Start-Process "{current_exe}"
                if (Test-Path "{backup_path}") {{
                    Remove-Item "{backup_path}" -Force
                }}
                Remove-Item "{new_exe_path}" -Force
            }} catch {{
                if (Test-Path "{backup_path}") {{
                    Copy-Item "{backup_path}" "{current_exe}" -Force
                }}
                throw
            }}
            '''
            
            # Starte PowerShell Script im Hintergrund
            subprocess.Popen([
                "powershell", "-WindowStyle", "Hidden", "-Command", ps_script
            ], creationflags=subprocess.CREATE_NO_WINDOW)
            
            print("Update wird installiert. Anwendung wird neu gestartet...")
            return True
            
        except Exception as e:
            print(f"Fehler bei der Installation: {e}")
            return False


def update_check_cli():
    """CLI Version für manuellen Update-Check"""
    updater = AutoUpdater()
    
    print("Prüfe auf Updates...")
    update_info = updater.check_for_updates()
    
    if not update_info:
        print("Keine Updates verfügbar.")
        return
        
    print(f"Update verfügbar: v{update_info['version']}")
    print(f"Größe: {update_info['size'] / 1024 / 1024:.1f} MB")
    print(f"Veröffentlicht: {update_info['published_at']}")
    
    if input("Update installieren? (j/N): ").lower().startswith('j'):
        def progress(percent):
            print(f"\rDownload: {percent}%", end="", flush=True)
            
        new_exe = updater.download_update(update_info['download_url'], progress)
        print()  # Neue Zeile nach Progress
        
        if new_exe and updater.install_update(new_exe):
            print("Update wird installiert...")
            sys.exit(0)
        else:
            print("Update fehlgeschlagen.")


if __name__ == "__main__":
    update_check_cli()
