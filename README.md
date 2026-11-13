<p align="center"><img src="icon.svg" alt="" width="120"><br><img src="app/web/logo.svg" alt="MIRA" width="360"></p>

# MIRA

**Mail · Identity · Routing · Access** – eine Weboberfläche für die ganze Infrastruktur: Benutzer im AD, Single Sign-on mit Authentik, Reverse-Proxy mit HAProxy und nginx, Postfächer in grommunio oder Exchange.

Weitere Unraid-Pakete: [haproxy-unraid](https://github.com/SysriX/haproxy_unraid), [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid), [certwarden-unraid](https://github.com/SysriX/certwarden_unraid)

`ghcr.io/sysrix/mira:latest`

> **Stand 0.1.0:** erste Version. Einrichtung, Anmeldung, Verbindungen, HAProxy (über haproxy-ui) und nginx sind lokal getestet. **AD/Samba, Authentik, grommunio und Exchange sind noch nicht gegen echte Server getestet.**

## Was es kann

| Bereich | Inhalt |
| --- | --- |
| **Benutzer** (AD/Samba) | suchen, anlegen, ändern (Name, Mail, Telefon, Abteilung …), Passwort setzen (mit „muss ändern“), aktivieren/deaktivieren, entsperren, in andere OU verschieben, löschen, Gruppen zuordnen |
| **Gruppen** (AD/Samba) | anlegen (Sicherheit/Verteiler, global/universal/lokal), Mitglieder pflegen, Beschreibung, löschen; Organisationseinheiten anlegen |
| **Neuer Mitarbeiter** | in einem Rutsch: AD-Konto mit Vorschlag für Anmeldename und Mail, Start-Passwort, Gruppen, **Postfach** in Exchange oder grommunio, **Zugang zu MIRA** |
| **Mitarbeiter sperren** | AD-Konto deaktivieren, Authentik-Konto sperren, in Exchange aus dem Adressbuch nehmen, MIRA-Zugang sperren |
| **Authentik** | Anwendungen anlegen (OIDC oder Proxy, eingebetteter Outpost), **Zugriff je Gruppe**, Benutzer (sperren, Wiederherstellungs-Link), Gruppen, Outposts, LDAP-Quellen |
| **HAProxy** | über [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid): Status, Backends/Server, Zertifikate, Dienste-Tabelle, Unterschiede, **Prüfen / Übernehmen / Zurückspringen** (mit automatischem Rücksprung), Dateien. Der Assistent „Neue Adresse“ bleibt in haproxy-ui |
| **nginx** | Hosts als Reverse-Proxy, Webseite, PHP oder Umleitung – HTTPS, HTTP→HTTPS, WebSockets, PROXY-Protokoll, echte Client-IP, Upload-Grenze, Sicherheits-Header, weitere Pfade, eigene Zeilen; **Vorschau**; Speichern = Datei schreiben → `nginx -t` → bei Fehler alter Stand zurück → `reload`. Dateien statischer Seiten hochladen. Von Hand geschriebene Dateien als Text bearbeiten |
| **grommunio** | Domänen, Postfächer (anlegen, Speicherplatz, sperren, Passwort, Aliase, löschen), Übernahme aus LDAP, Systemstatus |
| **Exchange** | Postfächer (aktivieren, trennen, Speichergrenzen, Adressen, Weiterleitung, im Adressbuch ausblenden, Vollzugriff/Senden als), freigegebene Postfächer/Räume, Verteiler, Datenbanken, akzeptierte Domänen, Zertifikate, Warteschlangen, **PowerShell** für alles Weitere |
| **Protokoll** | wer hat wann was geändert – jede Aktion, jede Anmeldung, auch Fehlversuche |
| **Aussehen** | Themes oben rechts: **Konsole** (dunkel, plastisch, Akzent Orange · Grün · Blau), Dunkel schlicht, Hell, wie das System – gemerkt im Browser |

## Anmeldung und Rechte

- **Authentik (OIDC)** – „Anmelden mit Authentik“, mit MFA. MIRA legt die Anwendung in Authentik selbst an (Rücksprung-Adresse, Zugriff nur für die Zugangsgruppe).
- **Domänen-Konto** – Benutzer + Passwort direkt gegen das AD.
- **Lokal** – mindestens ein lokaler Admin bleibt immer, damit man bei einem AD-/Authentik-Ausfall nicht ausgesperrt ist.

Rein kommt nur, wer **in MIRA freigegeben** ist (Seite „Zugang“). Verzeichnis-Benutzer werden bei jeder Anmeldung zusätzlich im AD geprüft: Konto vorhanden, aktiv und Mitglied der **Zugangsgruppe** (z. B. `grp_MIRA_Admins`, auch verschachtelt).

| Rolle | darf |
| --- | --- |
| Leser | alles ansehen |
| Operator | Benutzer, Gruppen, Postfächer, Hosts, Authentik-Anwendungen pflegen; HAProxy prüfen |
| Admin | zusätzlich löschen, HAProxy übernehmen, Verbindungen und Zugang verwalten, PowerShell |

**Zugangsdaten** zu den Diensten (Passwörter, Tokens) werden mit Fernet (AES + HMAC) verschlüsselt in der Datenbank gespeichert und nie wieder an den Browser geschickt. Der Schlüssel liegt in `/config/schluessel.key` – **mit sichern**, sonst sind die gespeicherten Zugangsdaten verloren (oder `MIRA_KEY` setzen).

## Installation in Unraid

1. Vorlage holen und in **Apps › Private** `MIRA` installieren:
   ```bash
   mkdir -p /boot/config/plugins/community.applications/private/sysrix && curl -fsSL https://raw.githubusercontent.com/SysriX/mira_unraid/main/templates/mira.xml -o /boot/config/plugins/community.applications/private/sysrix/mira.xml
   ```
2. Netzwerk `br0` mit fester IP, Ordner `appdata/mira` → `/config`.
3. Optional für nginx: dessen Konfigurations- und Webseiten-Ordner einbinden, und den Docker-Socket (nur dann kann MIRA `nginx -t`/`reload` im nginx-Container ausführen – das gibt MIRA Root-Rechte auf dem Host).
4. Starten, im **Log** steht der **Einrichtungs-Code**, dann `http://<IP_MIRA>:8080` öffnen.

### Einrichtungs-Assistent

1. **Code** aus dem Log eingeben (schützt die Einrichtung vor fremden Zugriffen).
2. **Datenbank**: eingebaut (SQLite) oder MariaDB – vorhandene benutzen oder mit Root-Zugang Datenbank + Benutzer anlegen lassen. Liegt dort schon eine MIRA-Datenbank, wird sie übernommen.
3. **Anmeldung**: lokal / Verzeichnis / Authentik.
4. **Verbindungen** zu AD und Authentik eingeben und testen – oder schon gespeicherte wählen.
5. **Admin**: lokaler Notfall-Admin, dazu einen **vorhandenen AD-Benutzer übernehmen** oder einen **neuen anlegen**; die Zugangsgruppe wird bei Bedarf angelegt, die Authentik-Anwendung auch.

Weitere Dienste (HAProxy, nginx, grommunio, Exchange, weitere ADs) danach unter **Verbindungen**.

## Was die Dienste brauchen

| Dienst | Voraussetzung |
| --- | --- |
| AD / Samba | **LDAPS (636)** oder StartTLS – Passwörter lassen sich im AD nur verschlüsselt setzen. Dienstkonto mit Rechten in den OUs, in denen MIRA anlegen/ändern soll (Delegation, nicht Domänen-Admin) |
| Authentik | API-Token eines Admin-Kontos (Verzeichnis › Tokens, Zweck „API“) |
| HAProxy | [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid) mit Adresse und `UI_PASS` |
| nginx | Konfigurationsordner in MIRA eingebunden; für Prüfen/Neu laden Docker-Socket + Containername |
| grommunio | Admin-Konto der Admin-Oberfläche (Port 8443) |
| Exchange | **WinRM** auf dem Exchange-Server (`winrm quickconfig`), Konto in „Organization Management“ bzw. „Recipient Management“; Standard: WinRM + Snap-In mit NTLM. Alternativ der offizielle Endpunkt `/PowerShell` (meist Kerberos) |

## Umgebungsvariablen

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `MIRA_DATA` | `/config` | Datenordner |
| `MIRA_PORT` | `8080` | Port |
| `MIRA_PROXY_IPS` | `127.0.0.1` | IPs des Reverse-Proxys, dessen `X-Forwarded-*` geglaubt wird |
| `MIRA_KEY` | – | eigener Schlüssel statt `schluessel.key` |
| `MIRA_SETUP_TOKEN` | – | fester Einrichtungs-Code |
| `MIRA_TLS_CERT` / `MIRA_TLS_KEY` | – | MIRA selbst per HTTPS (sonst über HAProxy) |

## Sicherheit

- MIRA hat Admin-Zugriff auf AD, Authentik, Proxy und Mail – **nur intern** erreichbar machen (in HAProxy Zugang „nur intern“) und Anmeldung über Authentik mit MFA.
- Schreibende Aufrufe nur mit eigenem Header (CSRF-Schutz), Sitzungs-Cookie `HttpOnly` + `SameSite=Lax`, strenge Content-Security-Policy, 5 Fehlversuche → 5 Minuten Pause.
- Jede Änderung steht im Protokoll.

## Geplant

- **Rechte-Datenbank**: Dienste, Rechte und Rollen in MariaDB, MIRA schreibt daraus die Gruppen ins AD und die Attribute nach Authentik.
- Weitere Module nach Bedarf (z. B. Nextcloud, Home Assistant, OPNsense).

## Entwicklung

```bash
pip install -r requirements.txt
MIRA_DATA=./daten python app/main.py
```

Lizenz: MIT
