# Änderungen

## 0.4.0 – 2026-11-23

### Neu: Mehrere Server (Routing › Server)
- Beliebig viele **HAProxy**- und **nginx**-Instanzen nebeneinander. Server-Liste mit Name, Art, Zugang, Adresse, Standort/Notiz, Reihenfolge, ein/aus, Status.
- Je Server: **Testen**, **Prüfen** (nginx -t / haproxy -c / haproxy-ui „prüfen“) und **Übernehmen/Neu laden** – in der Liste, auf der Übersicht als Kacheln, und oben in HAProxy/nginx wird zwischen den Servern umgeschaltet.
- Zugangsarten:
  - HAProxy über **haproxy-ui** (wie bisher)
  - HAProxy auf der **OPNsense** (Plugin os-haproxy) über deren API – siehe unten
  - nginx **lokal** (Ordner in MIRA + Docker-Socket, wie bisher)
  - nginx **per SSH** auf anderen Hosts: Dateien über SFTP, `nginx -t`/`reload` per SSH, wahlweise mit `sudo` oder `docker exec <Container>`; Passwort oder privater Schlüssel (verschlüsselt gespeichert); der Host-Schlüssel wird beim ersten Kontakt gemerkt, ein geänderter Schlüssel wird abgelehnt.
- **Datenbank-Migration** (Schema 2) beim ersten Start: bisherige HAProxy- und nginx-Verbindungen werden zu Servern (Einstellungen und verschlüsselte Zugangsdaten bleiben), Sicherung der alten Einträge in der Einstellung `migration_v2_backup`, Eintrag im Protokoll. Die Migration ist wiederholbar und legt nichts doppelt an.
- API: `/api/servers…` (Liste/Anlegen/Ändern/Entfernen/Test/Status/Prüfen/Übernehmen); die Seiten der Arten liegen jetzt unter `/api/srv/<Server>/haproxy|opn|nginx/…` (vorher `/api/m/haproxy|nginx/<Verbindung>/…`).

### Neu: HAProxy auf der OPNsense (os-haproxy)
- Zugang mit API-Key/Secret (Rechte „Services: HAProxy“ genügen).
- Status mit Statistik (Backends/Server, Verbindungen, Version), Dienst starten/neu starten/stoppen.
- **Frontends, Backends, Server, ACLs, Aktionen**: auflisten, ein-/ausschalten, anlegen, ändern (alle Felder der OPNsense, wichtige zuerst), löschen.
- **Prüfen & Übernehmen**: `configtest` (haproxy -c), Unterschied gespeichert ↔ laufend, erzeugte Konfiguration ansehen; Übernehmen (`reconfigure`) nur nach fehlerfreier Prüfung, danach Kontrolle, ob noch etwas abweicht.

### Neu: Cert Warden als mitgeliefertes Plugin
- Cert Warden (ab v0.31) direkt in MIRA bedienen: **Übersicht** mit Ablaufdaten (Ampel), **Zertifikate** (anlegen inkl. neuem Schlüssel, Namen/Post-Processing ändern, „Jetzt erneuern“, Aufträge mit Status/Fehler, erneut versuchen, widerrufen, PEM herunterladen, löschen), **ACME-Konten** (anlegen, registrieren inkl. EAB, löschen, ACME-Server), **Schlüssel** (erzeugen oder PEM importieren, löschen).
- Einrichtung wie bei VERA über den Assistenten (Suche auf den Ports 4055/4050 oder Adresse eingeben). Cert Warden kennt keine API-Schlüssel für die Verwaltung – MIRA speichert Benutzer und Passwort verschlüsselt und meldet sich selbst an (Token wird automatisch erneuert).
- Plugin-System: neue eingebaute **Anschlüsse** (`connect.type`) für Programme ohne SysriX-Standard; schreibende Aufrufe über solche Anschlüsse landen im MIRA-Protokoll, Löschen erst ab MIRA-Admin.

### Sonstiges
- Navigation: neue Gruppe **Routing** (Server, HAProxy, nginx); Protokoll-Filter „server“ und „plugin“.
- Neue Abhängigkeit: `paramiko` (SSH).

## 0.3.1 – 2026-11-20
- `leeren()` flacht verschachtelte Listen vollständig ab; Plugins dürfen Dateien direkt hochladen.

## 0.3.0 – 2026-11-19
- MIRA wird zur Zentrale mit Plugin-System (Freigabe per Fingerabdruck, Einrichtungs-Assistent mit Netzsuche, Weiterleitung `/api/p/…`), VERA als erstes Plugin.

## 0.2.0 – 2026-11-19
- WireGuard-Modul für OPNsense (später nach VERA ausgelagert).

## 0.1.x – 2026-11-12 bis 11-13
- Erste Version: Einrichtung, Anmeldung (lokal/AD/Authentik), AD/Samba, Authentik, HAProxy über haproxy-ui, nginx, grommunio, Exchange, Protokoll, Themes.
