# Tennisplatz-Buchungstool – korrigierte Login-/Session-Version

Korrigiert:
- `auto_logout`-Route ist vorhanden.
- Fehler `Could not build url for endpoint 'auto_logout'` behoben.
- Logo entfernt.
- Aufruf von `/` zeigt immer den Login-Bildschirm.
- Regel heißt: „Maximale Stunden im Voraus buchbar“.
- Auto-Logout beim Verlassen/Schließen der Website ist enthalten.

Start:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Beim Wechsel von alter Version:
```bash
rm -f instance/tennisbuchung.db
rm -f tennisbuchung.db
```

Admin:
- E-Mail: admin@verein.de
- Passwort: admin123
