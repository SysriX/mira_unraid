<p align="center"><img src="icon.svg" alt="" width="120"><br><img src="app/web/logo.svg" alt="MIRA" width="360"></p>

# MIRA

**Management · Identity · Routing · Access** – die Zentrale für die ganze Infrastruktur: Benutzer im AD, Single Sign-on mit Authentik, Reverse-Proxy mit beliebig vielen HAProxy- (haproxy-ui oder OPNsense) und nginx-Instanzen, Zertifikate mit Cert Warden, Postfächer in grommunio oder Exchange – und über **Plugins** jedes weitere Programm (z. B. [VERA](https://github.com/SysriX/vera_unraid) für WireGuard), so als würde man direkt darin arbeiten.

Weitere Unraid-Pakete: [haproxy-unraid](https://github.com/SysriX/haproxy_unraid), [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid), [certwarden-unraid](https://github.com/SysriX/certwarden_unraid)

`ghcr.io/sysrix/mira:latest`

> **Stand 0.4.0:** mehrere Server (HAProxy über haproxy-ui oder OPNsense, nginx lokal oder per SSH) mit Migration der bisherigen Verbindungen, Cert Warden als mitgeliefertes Plugin. Lokal gegen nachgebaute Cert-Warden-/OPNsense-Schnittstellen getestet – **gegen echte OPNsense, Cert Warden und SSH-Hosts noch ungetestet.** Änderungen: [CHANGELOG.md](CHANGELOG.md).
>
> **Stand 0.3.0:** Plugin-System (getestet mit dem VERA-Plugin gegen eine nachgebaute OPNsense). **Stand 0.1.0:** erste Version. Einrichtung, Anmeldung, Verbindungen, HAProxy (über haproxy-ui) und nginx sind lokal getestet. **AD/Samba, Authentik, grommunio und Exchange sind noch nicht gegen echte Server getestet.**

## Was es kann

| Bereich | Inhalt |
| --- | --- |
| **Benutzer** (AD/Samba) | suchen, anlegen, ändern (Name, Mail, Telefon, Abteilung …), Passwort setzen (mit „muss ändern“), aktivieren/deaktivieren, entsperren, in andere OU verschieben, löschen, Gruppen zuordnen |
| **Gruppen** (AD/Samba) | anlegen (Sicherheit/Verteiler, global/universal/lokal), Mitglieder pflegen, Beschreibung, löschen; Organisationseinheiten anlegen |
| **Neuer Mitarbeiter** | in einem Rutsch: AD-Konto mit Vorschlag für Anmeldename und Mail, Start-Passwort, Gruppen, **Postfach** in Exchange oder grommunio, **Zugang zu MIRA** |
| **Mitarbeiter sperren** | AD-Konto deaktivieren, Authentik-Konto sperren, in Exchange aus dem Adressbuch nehmen, MIRA-Zugang sperren |
| **Authentik** | Anwendungen anlegen (OIDC oder Proxy, eingebetteter Outpost), **Zugriff je Gruppe**, Benutzer (sperren, Wiederherstellungs-Link), Gruppen, Outposts, LDAP-Quellen |
| **Server** | beliebig viele HAProxy- und nginx-Instanzen in einer Liste (Name, Art, Zugang, Adresse, Standort): testen, **prüfen**, **übernehmen/neu laden** je Server; oben in HAProxy/nginx umschalten |
| **HAProxy (OPNsense)** | HAProxy-Plugin der OPNsense über deren API: Status/Statistik, **Frontends, Backends, Server, ACLs, Aktionen** anlegen/ändern/ein-aus/löschen, `haproxy -c` prüfen, Unterschiede, **Übernehmen** nur nach fehlerfreier Prüfung |
| **Cert Warden** | mitgeliefertes Plugin: Zertifikate mit Ablaufdaten, **jetzt erneuern**, neu anlegen, Aufträge (Status, erneut versuchen, widerrufen, PEM), ACME-Konten (anlegen, registrieren), Schlüssel |
| **HAProxy** | über [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid): Status, Backends/Server, Zertifikate, Dienste-Tabelle, Unterschiede, **Prüfen / Übernehmen / Zurückspringen** (mit automatischem Rücksprung), Dateien. Der Assistent „Neue Adresse“ bleibt in haproxy-ui |
| **nginx** | lokal (Ordner + Docker-Socket) oder **per SSH** auf anderen Hosts (SFTP, sudo oder `docker exec`). Hosts als Reverse-Proxy, Webseite, PHP oder Umleitung – HTTPS, HTTP→HTTPS, WebSockets, PROXY-Protokoll, echte Client-IP, Upload-Grenze, Sicherheits-Header, weitere Pfade, eigene Zeilen; **Vorschau**; Speichern = Datei schreiben → `nginx -t` → bei Fehler alter Stand zurück → `reload`. Dateien statischer Seiten hochladen. Von Hand geschriebene Dateien als Text bearbeiten |
| **grommunio** | Domänen, Postfächer (anlegen, Speicherplatz, sperren, Passwort, Aliase, löschen), Übernahme aus LDAP, Systemstatus |
| **Exchange** | Postfächer (aktivieren, trennen, Speichergrenzen, Adressen, Weiterleitung, im Adressbuch ausblenden, Vollzugriff/Senden als), freigegebene Postfächer/Räume, Verteiler, Datenbanken, akzeptierte Domänen, Zertifikate, Warteschlangen, **PowerShell** für alles Weitere |
| **Plugins** | andere Programme in MIRA bedienen: Plugin-Ordner kopieren → freigeben → Assistent sucht das Programm im Netz (oder Adresse eingeben) → einmal mit dessen Admin anmelden → fertig. Beispiel **VERA** (WireGuard auf der OPNsense: Zugänge, neue Geräte, QR/Datei/Freigabelink, Standortverbindungen). Siehe unten und [docs/PLUGINS.md](docs/PLUGINS.md) |
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

Weitere Dienste (grommunio, Exchange, weitere ADs) danach unter **Verbindungen**, HAProxy- und nginx-Instanzen unter **Routing › Server**.

### Update von 0.3.x

Beim ersten Start von 0.4.0 werden die bisherigen HAProxy- und nginx-**Verbindungen** automatisch zu **Servern** (Einstellungen und Zugangsdaten bleiben, Protokolleintrag „Migration auf Server-Liste“). Eine Sicherung der alten Einträge liegt in der Datenbank-Einstellung `migration_v2_backup`. Wer eigene Skripte gegen die MIRA-API hat: HAProxy/nginx liegen jetzt unter `/api/srv/<Server>/haproxy|nginx/…` statt `/api/m/haproxy|nginx/<Verbindung>/…`.

## Was die Dienste brauchen

| Dienst | Voraussetzung |
| --- | --- |
| AD / Samba | **LDAPS (636)** oder StartTLS – Passwörter lassen sich im AD nur verschlüsselt setzen. Dienstkonto mit Rechten in den OUs, in denen MIRA anlegen/ändern soll (Delegation, nicht Domänen-Admin) |
| Authentik | API-Token eines Admin-Kontos (Verzeichnis › Tokens, Zweck „API“) |
| HAProxy (haproxy-ui) | [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid) mit Adresse und `UI_PASS` |
| HAProxy (OPNsense) | Plugin **os-haproxy**; API-Key/Secret eines Benutzers mit den Rechten „Services: HAProxy“ (System › Zugang › Benutzer › API-Schlüssel) |
| nginx (lokal) | Konfigurationsordner in MIRA eingebunden; für Prüfen/Neu laden Docker-Socket + Containername |
| nginx (SSH) | SSH-Zugang (Passwort oder Schlüssel) mit Schreibrecht auf den Konfigurationsordner; `nginx -t`/`-s reload` direkt, mit `sudo -n` oder per `docker exec` |
| Cert Warden | ab v0.31, lokales Konto (Benutzer + Passwort; OIDC geht für MIRA nicht), Adresse mit Port, z. B. `https://<IP_CertWarden>:4055` |
| grommunio | Admin-Konto der Admin-Oberfläche (Port 8443) |
| Plugins | das jeweilige Programm (z. B. VERA ab 1.1.0) mit einem Admin-Konto für die einmalige Kopplung |
| Exchange | **WinRM** auf dem Exchange-Server (`winrm quickconfig`), Konto in „Organization Management“ bzw. „Recipient Management“; Standard: WinRM + Snap-In mit NTLM. Alternativ der offizielle Endpunkt `/PowerShell` (meist Kerberos) |

## Plugins

Ein Plugin bindet ein anderes Programm in MIRA ein. Es liegt als Ordner mit `plugin.json` vor – SysriX-Programme liefern ihn im eigenen Repository mit (VERA: `mira-plugin/vera/`).

**Mitgeliefert:** **Cert Warden** (Zertifikate) – ist sofort aktiv, nur noch einrichten: Navigation › Cert Warden › Einrichtung starten → Adresse (Suche auf Port 4055/4050) → Benutzer und Passwort von Cert Warden. Weil Cert Warden keine API-Schlüssel für die Verwaltung kennt, speichert MIRA die Anmeldedaten verschlüsselt und holt sich selbst die kurzlebigen Tokens.

Weitere Plugins:

1. Plugin-Ordner nach **`/config/plugins/`** kopieren (z. B. `/config/plugins/vera/`).
2. MIRA › **Plugins** › „Ordner neu einlesen“ → das Plugin erscheint als **neu** und unter seinem Namen in der Navigation. Ein Admin gibt es frei (es bringt eigenen Programmcode mit). Ändern sich seine Dateien, muss es neu freigegeben werden.
3. Auf den Knopf (z. B. **VERA**) klicken → **Einrichtungs-Assistent**: sucht das Programm im eigenen Netz (oder in angegebenen Netzen, auch mit Port: `192.168.10.0/24:8443`), alternativ Adresse eingeben.
4. Einmal mit einem **Admin des Programms** anmelden. Das Programm stellt MIRA dafür einen eigenen **Schlüssel** aus; Benutzer und Passwort werden nicht gespeichert. Im Programm unter „Verbundene Apps“ jederzeit widerrufbar.
5. Danach erscheinen dessen Seiten in der Navigation (VERA: WireGuard, Firewalls, Benutzer, Protokoll) – mit derselben Oberfläche wie im Programm selbst.

Rechte: MIRA-**Leser** sehen nur, **Operatoren** dürfen ändern, nur MIRA-**Admins** bekommen die Admin-Funktionen des Programms. Beim Verbinden lässt sich der Schlüssel zusätzlich auf „Bearbeiten“ oder „Nur lesen“ begrenzen. Im Protokoll des Programms steht, welcher MIRA-Benutzer etwas geändert hat.

Mehrere Installationen desselben Programms (z. B. eine VERA je Standort) lassen sich nebeneinander verbinden; oben in der Ansicht wird umgeschaltet.

## Umgebungsvariablen

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `MIRA_DATA` | `/config` | Datenordner |
| `MIRA_PORT` | `8080` | Port |
| `MIRA_PROXY_IPS` | `127.0.0.1` | IPs des Reverse-Proxys, dessen `X-Forwarded-*` geglaubt wird |
| `MIRA_KEY` | – | eigener Schlüssel statt `schluessel.key` |
| `MIRA_SETUP_TOKEN` | – | fester Einrichtungs-Code |
| `MIRA_PLUGINS` | `/config/plugins` | Plugin-Ordner |
| `MIRA_TLS_CERT` / `MIRA_TLS_KEY` | – | MIRA selbst per HTTPS (sonst über HAProxy) |

## Sicherheit

- MIRA hat Admin-Zugriff auf AD, Authentik, Proxy und Mail – **nur intern** erreichbar machen (in HAProxy Zugang „nur intern“) und Anmeldung über Authentik mit MFA.
- Schreibende Aufrufe nur mit eigenem Header (CSRF-Schutz), Sitzungs-Cookie `HttpOnly` + `SameSite=Lax`, strenge Content-Security-Policy, 5 Fehlversuche → 5 Minuten Pause.
- Jede Änderung steht im Protokoll.
- Plugins laufen erst nach Freigabe durch einen Admin (Fingerabdruck aller Dateien); ihre Aufrufe gehen nur über MIRA (`/api/p/<Verbindung>/…`), nur auf die im Plugin freigegebenen Pfade, und der Schlüssel des Programms erreicht nie den Browser.

## Geplant

- **Rechte-Datenbank**: Dienste, Rechte und Rollen in MariaDB, MIRA schreibt daraus die Gruppen ins AD und die Attribute nach Authentik.
- Weitere Module nach Bedarf (z. B. Nextcloud, Home Assistant).
- Cert Warden ↔ Server: ausgestellte Zertifikate direkt an HAProxy/nginx-Server verteilen.
- Plugins für weitere SysriX-Programme (ALMA, NINA, LIMA …) und fremde Programme (eigener Python-Teil im Plugin).
- Die eingebauten Module (AD, Authentik, HAProxy, nginx, grommunio, Exchange) als mitgelieferte Plugins nach demselben Muster.

## Entwicklung

```bash
pip install -r requirements.txt
MIRA_DATA=./daten python app/main.py
```

Lizenz: MIT
