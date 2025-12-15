# -*- mode: python ; coding: utf-8 -*-
"""Kompatibilitäts-Wrapper für ältere CI-Konfigurationen.

In diesem Repo ist `Aufsichtsplan.spec` die gepflegte Spec-Datei (inkl. OR-Tools
Native-Binaries wie `cp_model_helper`). Einige Workflows/Anleitungen referenzierten
früher `Aufsichtsplan-fixed.spec`. Damit diese Referenzen nicht brechen, lädt
dieser Wrapper die aktuelle Spec-Datei.
"""

from pathlib import Path

exec(Path("Aufsichtsplan.spec").read_text(encoding="utf-8"), globals())
