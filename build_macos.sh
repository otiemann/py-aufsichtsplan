#!/bin/bash

# Explizit Python 3.13 venv verwenden (neueste verfügbare Version)
VENV_DIR="venv_py313"

# Prüfen ob venv existiert, ansonsten erstellen
if [ ! -d "$VENV_DIR" ]; then
    echo "Erstelle Python 3.13 venv..."
    /opt/homebrew/bin/python3.13 -m venv "$VENV_DIR"
fi

# Venv aktivieren
source "$VENV_DIR/bin/activate"

echo "Verwende Python: $(python --version)"

# Pip aktualisieren und Abhängigkeiten installieren
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# Clean dist
rm -rf dist build

# Bundle mit Templates und Static Files
pyinstaller --noconsole --onefile \
  --name Aufsichtsplan \
  --add-data "app/templates:app/templates" \
  --add-data "app/static:app/static" \
  start.py

echo "Build abgeschlossen. App unter dist/Aufsichtsplan"
