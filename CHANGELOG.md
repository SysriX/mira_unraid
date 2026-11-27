# Änderungen

## 0.7.0 – 2026-11-27

### Neu: DANA – MySQL- und MariaDB-Server in MIRA verwalten (Plugin)
- Eingebauter Anschluss **`mysql`** (`app/connector_mysql.py`): MIRA spricht selbst mit MySQL/MariaDB (PyMySQL), es braucht kein weiteres Programm. Jeder Datenbank-Server ist eine Plugin-Verbindung – **beliebig viele** nebeneinander, oben umschaltbar.
- Plugin **DANA** (Ordner `plugins/dana/`, signiert): Server-Übersicht (Version, Laufzeit, Verbindungen, Größe), **Datenbanken** anlegen/Sortierung ändern/löschen, **Tabellen** (Liste mit Größe/Zeilen, Mehrfachauswahl: Export, Optimieren, Leeren, Löschen), **Daten** blättern/sortieren/suchen/WHERE-Filter, Zeilen einfügen/bearbeiten/kopieren/löschen (auch NULL, ENUM, Binärdaten als Hex), **Struktur** (Spalten, Indizes, Fremdschlüssel, Trigger, CREATE-Anweisung) mit Spalten/Index anlegen, ändern, löschen, **neue Tabelle** mit Spalten-Editor und SQL-Vorschau, **Vorgänge** (umbenennen, Kommentar, Engine, Sortierung, AUTO_INCREMENT, Optimieren/Analysieren/Prüfen/Reparieren, Leeren, Löschen), **SQL-Konsole** (mehrere Anweisungen, DELIMITER, mehrere Ergebnismengen, Verlauf, Strg+Enter), **Export** als SQL/SQL.gz (Struktur, Daten, Ansichten, Trigger, Prozeduren) oder CSV, **Import** von .sql/.sql.gz, **Benutzer & Rechte** (anlegen mit eigener Datenbank, Passwort, sperren, umbenennen, Rechte vergeben/entziehen), **Status** (Prozesse mit Beenden, Variablen, Statuswerte).
- Rollen: *Leser* sehen alles und dürfen genau eine SELECT/SHOW/DESCRIBE/EXPLAIN-Abfrage in einer READ-ONLY-Transaktion ausführen; *Operator* ändert Daten, Struktur und nutzt die SQL-Konsole; *Admin* darf zusätzlich löschen (Datenbanken, Tabellen, Spalten, Indizes, Benutzer), leeren und Prozesse beenden. Darüber hinaus gelten die Rechte des Datenbank-Benutzers selbst.
- Assistent: Suche im Netz über die Server-Begrüßung auf Port 3306/3307 (weitere Ports mit `netz:port`), auch Server, die MIRA (noch) nicht zulassen, werden mit Hinweis gezeigt. Zugangsdaten verschlüsselt in MIRA; TLS wird benutzt, wenn der Server es anbietet (Zertifikat wahlweise geprüft).

### Plugin-System
- Anschlüsse können eigene Adressen haben (`norm_url`, `scheme`) – z. B. `mysql://host:3306` statt http.
- `plugin.json` › `connect` kennt neue Texte für den Assistenten: `address_hint`, `login_label`, `login_note`, `tls_label`, `tls_hint`, `search_note`.
- Der Assistent schlägt bei eingebauten Anschlüssen den Namen der Verbindung aus Programm und Adresse vor.

## 0.5.0 – 2026-11-23

### Neu: HAProxy-Migrationsassistent (Routing › HAProxy-Migration, nur Admins)
- Konfiguration von einem HAProxy-Server in einen anderen übernehmen – in alle Richtungen: **haproxy-ui (Docker) ↔ OPNsense (os-haproxy)**, auch Docker → Docker und OPNsense → OPNsense.
- Liest aus der Quelle **Backends** (mit Servern), **Adressen** (Name → Backend, aus `listen/dienste.map` bzw. aus Host-/SNI-Regeln der OPNsense, inkl. Zugang intern/extern) und **Frontends** mit **Bedingungen (ACLs)** und **Regeln**.
- **Auswählen**, was übernommen wird (eingebaute Teile von haproxy-unraid sind abgewählt); beim Anhaken eines Frontends/einer Adresse werden die nötigen Backends mitgewählt.
- **Umbenennen** von Backends, Servern, Frontends, Bedingungen und Regeln; alle Verweise (Standard-Backend, use_backend, Adressen, Bedingungen in Regeln, verknüpfte ACLs) ziehen mit, damit es nach dem Import genauso funktioniert.
- **Namenskonflikte** mit dem Ziel werden angezeigt: umbenennen (Vorschlag `…_import`) oder „vorhandenes benutzen“; bei Adressen „ersetzen“ oder „überspringen“.
- Ziel OPNsense: Server → Backends → ACLs → Aktionen → Frontends über die API; Adressen werden je eine Host-/SNI-ACL + use_backend-Aktion in einem gewählten (oder neuen) Frontend, intern/extern über eine ACL „interne Netze“; Zertifikat für TLS-Frontends wählbar; Regeln, die die OPNsense nicht abbilden kann, werden eigene Regeln (custom) mit eingebetteten Bedingungen.
- Ziel haproxy-ui: Backends/Frontends als Block „MIRA-Import“ in `dienste.cfg`, Adressen in `listen/dienste.map`.
- **Vorschau** vor dem Import; der Import speichert nur (Entwurf bzw. gespeichert) – danach wie gewohnt prüfen und übernehmen. Ergebnis im Protokoll.
- Grenzen: Zertifikate, Listen (`listen/*.lst`), global/defaults und `listen`-Abschnitte werden nicht kopiert (Hinweise im Assistenten); mehrzeilige ACLs mit verschiedenen Prüfungen kann die OPNsense nicht als eine ACL abbilden (klare Meldung).

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
