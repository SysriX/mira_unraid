# MIRA-Plugins

Ein Plugin bindet ein anderes Programm in MIRA ein, sodass man es in MIRA bedient, als wäre man direkt darin. Beispiel: [VERA](https://github.com/SysriX/vera_unraid) (Ordner `mira-plugin/vera/`).

## Ablauf für den Benutzer

1. Plugin-Ordner nach `/config/plugins/<id>/` kopieren.
2. MIRA › Plugins › „Ordner neu einlesen“ → Status **neu** → ein Admin **gibt es frei**.
3. Knopf des Plugins → **Einrichtungs-Assistent**: Suche im Netz → oder Adresse eingeben → einmal mit einem **Admin des Programms** anmelden.
4. Das Programm stellt MIRA einen **eigenen API-Schlüssel** aus. MIRA speichert nur diesen (verschlüsselt), nie Benutzer oder Passwort.

## Aufbau eines Plugins

```
vera/
  plugin.json        Beschreibung (Pflicht)
  icon.svg           Symbol (wird auch vor der Freigabe gezeigt)
  vera-plugin.js     meldet das Plugin bei MIRA an
  …                  weitere Skripte/Stile der Oberfläche
```

### plugin.json

```json
{
  "id": "vera",                       // a–z, 0–9, _ und -, 2–32 Zeichen; = Ordnername empfohlen
  "name": "VERA",
  "title": "VPN Endpoint & Remote Access",
  "version": "1.1.0",
  "author": "SysriX",
  "description": "…",
  "icon": "icon.svg",
  "connect": {
    "type": "sysrix",                 // Programm spricht den SysriX-Verbindungsstandard (unten)
    "app": "vera",                    // erwarteter Wert von "app" in /.well-known/sysrix-app
    "ports": [8080, 443, 80],         // Ports für die Suche (443 = https, sonst http; 8443/9443 = https)
    "hostnames": ["vera"],            // zusätzlich probierte Namen (Docker-Netz, DNS)
    "proxy": ["/api/wg/", "/api/firewalls"]   // nur diese Pfade darf die Oberfläche über MIRA aufrufen
  },
  "ui": {
    "styles": ["wireguard.css"],
    "scripts": ["wireguard.js", "vera-views.js", "vera-plugin.js"],   // in dieser Reihenfolge geladen
    "views": [                        // Seiten in der Navigation (Gruppe = Plugin-Name)
      { "id": "wireguard", "title": "WireGuard" },
      { "id": "users", "title": "Benutzer", "level": "admin" }    // level: read | write | admin
    ]
  }
}
```

### Oberfläche

Die Skripte laufen in MIRA und können die MIRA-Hilfsfunktionen benutzen (`h`, `leeren`, `dialog`, `feld`, `eingabe`, `tabelle`, `marke`, `punkt`, `datum`, `versuch`, `hinweis` …; dieselben wie in den SysriX-Programmen). Das letzte Skript meldet das Plugin an:

```js
miraPlugin("vera", ctx => ({
  views: {
    wireguard: { titel: "WireGuard", zeigen: async (main, q) => { … } },
  },
}));
```

`ctx` gibt es je Verbindung:

| Feld | Bedeutung |
| --- | --- |
| `ctx.api(methode, pfad, daten)` | Aufruf an das Programm, `pfad` relativ zu dessen Wurzel (`"/api/firewalls"`). MIRA leitet ihn mit dem Schlüssel weiter |
| `ctx.link(pfad)` | Adresse für direkte Browser-Aufrufe (Downloads) |
| `ctx.can("read"\|"write"\|"admin")` | darf der MIRA-Benutzer das (MIRA-Rolle **und** Rolle des Schlüssels) |
| `ctx.rerender()` | aktuelle Seite neu zeichnen |
| `ctx.conn` | Verbindung: `id`, `name`, `base_url`, `role` |

Tipp: Programm und Plugin teilen sich dieselben Ansichts-Dateien. VERA nutzt `vera-views.js` selbst mit `api = fetch auf /api/...` und das Plugin mit `ctx.api` – eine Oberfläche, zwei Orte.

### Freigabe und Sicherheit

- MIRA bildet einen **Fingerabdruck** (SHA-256 über alle Dateien). Ändert sich eine Datei, steht das Plugin auf **geändert** und ist gesperrt, bis ein Admin es erneut freigibt.
- Vor der Freigabe liefert MIRA nur das Symbol aus.
- Aufrufe gehen ausschließlich über `/api/p/<Verbindung>/<Pfad>`, nur auf die Pfade aus `connect.proxy`. Lesen (`GET`) ab MIRA-Rolle *Leser*, alles andere ab *Operator*.
- Eine Ablehnung des Programms (401) zeigt MIRA als Hinweis „Schlüssel widerrufen? Neu einrichten“ – nicht als Abmeldung von MIRA.

## SysriX-Verbindungsstandard (für Programme)

Was ein Programm können muss, damit MIRA es findet und bedienen kann (Referenz: VERA `app/main.py`):

**1. Erkennung** – ohne Anmeldung:

```
GET /.well-known/sysrix-app
→ {"app": "vera", "name": "VERA", "title": "…", "version": "1.1.0", "connect": 1,
   "pair": "/api/connect/pair", "instance": "<feste Kennung>", "setup_needed": false}
```

`instance` ist eine feste Kennung der Installation; damit zeigt die Suche ein Programm, das unter mehreren Adressen erreichbar ist, nur einmal.

**2. Kopplung** – einmalig mit einem Admin des Programms:

```
POST /api/connect/pair   {"username", "password", "client": "MIRA (host)", "role": "admin|write|read"}
→ {"token": "vera_…", "token_id": 3, "role": "admin"}
```

- nur Admins dürfen koppeln, Fehlversuche zählen wie bei der Anmeldung;
- gespeichert wird nur der Hash des Schlüssels; Liste und Widerruf im Programm unter „Verbundene Apps“.

**3. Aufrufe** mit `Authorization: Bearer <token>` (kein Cookie, daher ohne CSRF-Kopfzeile) und

| Kopfzeile | Bedeutung |
| --- | --- |
| `X-Acting-User` | MIRA-Benutzer – fürs Protokoll („MIRA (host) · marco“) |
| `X-Acting-Role` | `read`/`write`/`admin` aus der MIRA-Rolle – kann die Rolle des Schlüssels nur **senken** |
| `X-Acting-IP` | IP des MIRA-Benutzers – fürs Protokoll |

**4. Widerruf** – `DELETE /api/connect/tokens/<token_id>`: Admins jeden Schlüssel, ein Schlüssel immer sich selbst (MIRA macht das beim Entfernen der Verbindung).

**5. Prüfen** – `GET /api/me` mit dem Schlüssel → 200 (MIRA „Testen“).

## Geplant

- Plugin-Typ mit eigenem Python-Teil für fremde Programme ohne diesen Standard (z. B. OPNsense, Unraid, Proxmox direkt).
- Die eingebauten Module als mitgelieferte Plugins (`app/plugins/`, automatisch freigegeben).
- Signierte Plugins (Ed25519, wie die ALMA-Lizenzen): „von SysriX signiert“ statt nur Fingerabdruck.
