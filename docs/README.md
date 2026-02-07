# 📅 Aufsichtsplan

Professionelle Software für die Pausenaufsichtsplanung an Schulen.

## 🚀 Features

- **Intelligente Planung**: Automatische Erstellung von Aufsichtsplänen
- **CSV Import/Export**: Einfacher Datenaustausch
- **Web-Interface**: Benutzerfreundliche Oberfläche
- **GPU Integration**: Stundenplan-Import über GPU001.TXT
- **Flexible Bereiche**: Verwaltung verschiedener Aufsichtsbereiche
- **Offline-Betrieb**: Keine Internetverbindung erforderlich

## 💾 Download

[**→ Neueste Version herunterladen**](./downloads/Aufsichtsplan.exe)

Die Datei wird bei jedem **veröffentlichten GitHub Release** automatisch per GitHub Actions gebaut (PyInstaller), als Release-Asset hochgeladen und hier über GitHub Pages bereitgestellt. Eine SHA256-Prüfsumme liegt in `./downloads/checksums.txt` auf derselben Seite.

## 📦 Installation

1. Laden Sie die `Aufsichtsplan.exe` herunter
2. Führen Sie die Datei aus
3. Die Anwendung öffnet ein eigenes Desktop-Fenster (kein Browser erforderlich)
4. Beginnen Sie mit dem Import Ihrer Daten

## ⚙️ Systemanforderungen

- Windows 10 oder neuer
- 512 MB RAM
- 50 MB freier Speicherplatz
- Microsoft Edge WebView2 Runtime (unter Windows 10/11 meist bereits vorhanden)

## 🔧 Verwendung

### Lehrkräfte importieren
1. Gehen Sie zur Admin-Seite
2. Laden Sie eine CSV-Datei mit Lehrkräften hoch
3. Spalten: Nachname, Vorname, Kürzel (optional), E-Mail (optional)

### Stundenplan importieren
1. Exportieren Sie GPU001.TXT aus Ihrer Stundenplan-Software
2. Laden Sie die Datei in der Admin-Seite hoch
3. Anwesenheitstage werden automatisch gesetzt

### Aufsichtsplan erstellen
1. Definieren Sie Aufsichtsbereiche und Kontingente
2. Wählen Sie den Zeitraum (Start-Montag + Anzahl Wochen)
3. Generieren Sie den Wochenplan (wird im Zeitraum wöchentlich wiederholt)
4. Exportieren Sie als PDF oder GPU009

## 🐛 Problem melden

Haben Sie einen Fehler gefunden oder einen Verbesserungsvorschlag?
[Erstellen Sie ein Issue auf GitHub](https://github.com/otiemann/py-aufsichtsplan/issues)

## 📄 Lizenz

Dieses Projekt steht unter der MIT-Lizenz.

---

**Entwickelt mit ❤️ für Schulen**
