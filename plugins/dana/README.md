# DANA – MySQL & MariaDB in MIRA

Plugin für [MIRA](../../README.md) ab 0.7.0: beliebig viele **MySQL**- und **MariaDB**-Server verwalten – ähnlich phpMyAdmin, aber direkt in MIRA, mit MIRA-Anmeldung, Rollen und Protokoll.

MIRA spricht selbst mit dem Datenbank-Server (eingebauter Anschluss `mysql`, `app/connector_mysql.py`); es muss nichts zusätzlich installiert werden.

## Einrichten

1. Plugin installieren: *Plugins › Katalog* (PIA) oder den Ordner `plugins/dana/` nach `/config/plugins/dana/` kopieren und *Ordner neu einlesen*.
2. Auf dem Datenbank-Server einen Benutzer für MIRA anlegen, der sich von der Adresse von MIRA aus anmelden darf, z. B.:

   ```sql
   CREATE USER 'mira'@'<IP_MIRA>' IDENTIFIED BY '<Passwort>';
   GRANT ALL PRIVILEGES ON *.* TO 'mira'@'<IP_MIRA>' WITH GRANT OPTION;   -- alles verwalten
   -- oder nur einzelne Datenbanken: GRANT ALL PRIVILEGES ON `app`.* TO 'mira'@'<IP_MIRA>';
   ```

   Bei Docker/Unraid ist `<IP_MIRA>` die Adresse, unter der MIRA beim Server ankommt (bei br0 die eigene IP des Containers).
3. *DANA › Einrichten*: die Suche findet Server auf Port 3306/3307 im eigenen Netz (weitere Netze/Ports z. B. `192.168.20.0/24:3310`) – oder `host:port` eingeben. Benutzer + Passwort eingeben, Rechte für MIRA wählen, **Verbinden**.
4. Weitere Server: *DANA › Verbindung › + Weitere DANA verbinden*. Mit mehr als einem Server erscheint oben eine Auswahl.

Server, die MIRA (noch) nicht zulassen („Host … is not allowed to connect“), zeigt die Suche mit Hinweis an.

## Funktionen

| Bereich | |
| --- | --- |
| Server | Version, Laufzeit, Verbindungen, Größe, TLS; Datenbanken mit Tabellen/Zeilen/Größe |
| Datenbanken | anlegen (mit Sortierung), Sortierung ändern, löschen (mit Namensbestätigung), Export |
| Tabellen | Liste mit Mehrfachauswahl (Export, Optimieren, Leeren, Löschen), neue Tabelle mit Spalten-Editor und SQL-Vorschau |
| Daten | blättern, sortieren, in allen Spalten suchen, WHERE-Filter, Zeilen einfügen, bearbeiten, als neue Zeile kopieren, löschen; NULL, ENUM, lange Texte, Binärdaten (Hex) |
| Struktur | Spalten, Indizes, Fremdschlüssel, Trigger, CREATE-Anweisung; Spalten/Indizes anlegen, ändern, löschen |
| Vorgänge | umbenennen, Kommentar, Engine, Sortierung (wandelt um), AUTO_INCREMENT, Optimieren/Analysieren/Prüfen/Reparieren/Flush, Leeren, Löschen |
| SQL | mehrere Anweisungen, `DELIMITER`, mehrere Ergebnismengen (CALL), Warnungen, Verlauf, Strg+Enter |
| Export / Import | SQL oder SQL.gz (Struktur, Daten, Ansichten, Trigger, Prozeduren/Funktionen, `DROP … IF EXISTS`), CSV je Tabelle; Import von .sql/.sql.gz (auch mysqldump/mariadb-dump) |
| Benutzer | Konten mit Host, Anmeldeart, gesperrt/abgelaufen/ohne Passwort; anlegen (optional mit gleichnamiger Datenbank), Passwort, sperren, umbenennen, max. Verbindungen, Rechte vergeben (Schnellwahl) und je Zeile entziehen, löschen |
| Status | Prozesse (mit Beenden), globale Variablen, Statuswerte |

## Rechte

| MIRA-Rolle | darf |
| --- | --- |
| Leser | alles ansehen, Daten filtern, **eine** SELECT/SHOW/DESCRIBE/EXPLAIN-Abfrage (läuft in einer READ-ONLY-Transaktion, kein `INTO OUTFILE`) |
| Operator | zusätzlich Daten und Struktur ändern, SQL-Konsole, Import, Benutzer-Rechte vergeben |
| Admin | zusätzlich löschen (Datenbanken, Tabellen, Spalten, Indizes, Benutzer), leeren, Prozesse beenden |

Die beim Verbinden gewählte Stufe begrenzt das zusätzlich, und am Ende entscheidet der Datenbank-Server mit den Rechten des MIRA-Benutzers. Ändernde Aufrufe stehen im MIRA-Protokoll (Bereich *plugin*). Systemdatenbanken (`mysql`, `information_schema`, `performance_schema`, `sys`) lassen sich nicht löschen, und MIRA löscht nicht den Benutzer, mit dem es selbst angemeldet ist.

## Grenzen

- Export und Import laufen im Speicher von MIRA – für Datenbanken mit mehreren GB besser `mysqldump`/`mariadb-dump` am Server.
- Die SQL-Konsole zeigt höchstens 10 000 Zeilen je Ergebnis (Standard 1 000); ohne `LIMIT` liest der Server trotzdem alles.
- Jeder Aufruf öffnet eine eigene Verbindung: `USE`, Variablen und Transaktionen gelten nur innerhalb eines Konsolen-Aufrufs.
- Rollen (MariaDB `CREATE ROLE`) werden angezeigt, aber nur per SQL verwaltet.
