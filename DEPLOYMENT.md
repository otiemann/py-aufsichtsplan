# 🚀 Deployment Guide

Anleitung zur Veröffentlichung von Releases und Updates.

## 📋 Schnell-Übersicht

**Für neue Releases:**
```bash
git tag v1.0.1
git push origin v1.0.1
```

Das war's! GitHub Actions übernimmt den Rest automatisch.

## 🔄 Automatisierte Systeme

### 1. GitHub Actions (Release Pipeline)
- **Datei**: `.github/workflows/release.yml`
- **Trigger**: Git Tags mit Pattern `v*` (z.B. `v1.0.0`, `v1.2.3`)
- **Funktionen**:
  - Automatischer Windows EXE Build
  - Release-Erstellung auf GitHub
  - Upload der EXE-Datei
  - Generierung von Checksums
  - Version-JSON für Update-System

### 2. GitHub Pages (Website)
- **Dateien**: 
  - `.github/workflows/pages.yml`
  - `docs/index.html`
  - `docs/_config.yml`
- **Trigger**: Push zu `main` Branch
- **URL**: `https://olivertiemann.github.io/py-aufsichtsplan/`
- **Funktionen**:
  - Automatische Website-Updates
  - Download-Links zu neuesten Releases
  - Dokumentation und Installation

### 3. Auto-Update System
- **Dateien**: 
  - `updater.py` (separates Update-Tool)
  - `version.py` (Version-Management)
  - `/api/version` Endpoint in FastAPI
- **Funktionen**:
  - Windows Update-Benachrichtigungen
  - Automatischer Download neuer Versionen
  - Seamless Installation und Neustart

## 📝 Release Workflow

### Schritt 1: Version vorbereiten
```bash
# Version in version.py aktualisieren
# Datum in version.py setzen
vim version.py
```

### Schritt 2: Release erstellen
```bash
# Änderungen committen
git add .
git commit -m "Release v1.0.1: Bug fixes and improvements"

# Tag erstellen und pushen
git tag v1.0.1
git push origin main
git push origin v1.0.1
```

### Schritt 3: Automatischer Build
- GitHub Actions startet automatisch
- Windows EXE wird gebaut
- Release wird auf GitHub erstellt
- GitHub Pages wird aktualisiert

### Schritt 4: Verifizierung
1. **GitHub Release**: https://github.com/olivertiemann/py-aufsichtsplan/releases
2. **Website**: https://olivertiemann.github.io/py-aufsichtsplan/
3. **Download-Test**: EXE herunterladen und testen

## 🔧 Konfiguration

### Repository Settings
Für GitHub Pages müssen folgende Einstellungen aktiviert werden:
1. Repository → Settings → Pages
2. Source: "GitHub Actions"
3. Sicherstellen dass Actions aktiviert sind

### GitHub Secrets
Aktuell sind keine zusätzlichen Secrets erforderlich.
`GITHUB_TOKEN` wird automatisch bereitgestellt.

## 🐛 Problembehandlung

### Release schlägt fehl
- Prüfen Sie die GitHub Actions Logs
- Häufige Probleme:
  - Build-Abhängigkeiten fehlen
  - Falsche Tag-Namensgebung
  - PowerShell Syntax-Fehler

### GitHub Pages lädt nicht
- Prüfen Sie den Pages Workflow
- Jekyll Build-Fehler in den Logs
- DNS-Propagation kann bis zu 24h dauern

### Update-System funktioniert nicht
- Prüfen Sie `/api/version` Endpoint
- GitHub Releases API Limits beachten
- Windows Berechtigung für Datei-Ersetzung

## 📊 Monitoring

### Wichtige URLs zum Überwachen:
- **Releases**: https://github.com/olivertiemann/py-aufsichtsplan/releases
- **Actions**: https://github.com/olivertiemann/py-aufsichtsplan/actions
- **Website**: https://olivertiemann.github.io/py-aufsichtsplan/
- **API**: https://api.github.com/repos/olivertiemann/py-aufsichtsplan/releases/latest

### Log-Dateien:
- GitHub Actions Logs (online)
- Lokale Update-Logs in `%LOCALAPPDATA%\py-vertretungsplan\`

## 🚨 Wichtige Hinweise

- **Versionierung**: Nutzen Sie Semantic Versioning (MAJOR.MINOR.PATCH)
- **Tags**: Immer `v` Prefix verwenden (v1.0.0, nicht 1.0.0)
- **Testing**: Testen Sie EXE lokal vor Release
- **Rollback**: Bei Problemen können Sie Releases als "Pre-release" markieren

## 📞 Support

Bei Problemen mit dem Deployment-System:
1. Prüfen Sie die GitHub Actions Logs
2. Konsultieren Sie diese Dokumentation
3. Erstellen Sie ein Issue mit detaillierter Fehlerbeschreibung
