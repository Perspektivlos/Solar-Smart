**Bevor wir anfangen – ein paar Gegenfragen zum Trucki:**

**Was liefert der Trucki genau?**
- Nur Leistungswerte (W aktuell)?
- Auch Energie (kWh gesamt)?
- Phasenaufgelöst (L1/L2/L3) oder nur Gesamt?

**Wie kommuniziert er?**
- HTTP/REST API?
- Modbus TCP?
- MQTT direkt?

**Was soll er in VenusOS sein?**
- `pvinverter` – wenn er eine PV-Quelle darstellt
- `grid` – wenn er den Netzbezug misst
- `battery` – unwahrscheinlich, aber möglich

---

**Widerspruch vorab:**

**Trucki ist kein `pvinverter` – er ist ein `battery` oder ein Hybrid.**

Er speist aus einer **Hausbatterie** ein, nicht direkt aus PV. VenusOS würde ihn als `pvinverter` falsch in die Bilanz einrechnen.
Die richtige Kategorie wäre wahrscheinlich `pvinverter` trotzdem – aber nur weil Venus keine saubere Kategorie für "Batterie-Einspeisung über Mikrowechselrichter" kennt.

---

**Was wir haben – die relevanten Topics:**

| Topic | Bedeutung | Relevant? |
|---|---|---|
| `ACDISPLAY` | Aktuelle Ausgangsleistung [W] | ✅ Kernwert |
| `VGRID` | Netzspannung [V] | ✅ |
| `VBAT` | Batteriespannung [V] | ✅ |
| `METER` | Hausverbrauch [W] | ✅ sehr wichtig |
| `MAXPOWER` | Max. Leistung [W] | ✅ |
| `DAYENERGY` | Tagesenergie [kWh] | ✅ |
| `TOTALENERGY` | Gesamtenergie [kWh] | ✅ |
| `STATE` | Gerätestatus | ✅ |
| `TEMPERATURE` | Temperatur [°C] | ✅ |
| `TARGET` | Zielleistung [W] | ⚠️ optional |
| `DAC` / `CALSTEP` | Interne Kalibrierung | ❌ nicht nötig |

---

**Widerspruch zur Architektur:**

Viele bauen sowas als simples **Poll-Skript**. Das ist falsch für MQTT – du willst einen **subscriber**, der auf Änderungen reagiert, nicht einen der alle X Sekunden fragt.

Die Struktur sollte sein:

```
MQTT Broker
    ↓ subscribe
Python Dienst (läuft als Service)
    ↓ schreibt
dbus (com.victronenergy.pvinverter.trucki)
    ↓ erscheint
VenusOS Dashboard
```

---

**Die sauberere Architektur:**

```
Trucki MQTT Broker (192.168.0.201:1886)
    ↓ direkter subscribe
Python Dienst auf Cerbo/Raspberry
    ↓ schreibt
dbus
    ↓
VenusOS Dashboard
```

Dein Python-Dienst subscribed **direkt** beim externen Broker – kein Umweg.

---

**Auf welcher Hardware läuft dein VenusOS?**
- Cerbo GX (kein echter Linux-Zugang, eingeschränkt)
- Raspberry Pi mit Venus Image (voller Zugang)

Das entscheidet, ob:
- Ein **normales Python-Skript** als Service deployen können
- Oder ob wir über den **VenusOS Paketmanager** (`opkg`) gehen müssen

Das ist der Unterschied zwischen einfach und kompliziert.

Raspberry mit Venus Large – voller Zugang, kein Einschränkungsproblem.

**Wir brauchen eine Basis, nicht bei null anfangen.**

Es gibt ein offizielles Victron Template genau dafür:
`dbus-serialbattery` und vor allem `dbus-mqtt-*` Beispiele von Victron selbst. Die dbus-Bindings für Python sind bekannt – wir nutzen `vedbus.py` und `dbusmonitor.py` die Venus Large bereits mitbringt.

---

**Die Struktur des 'trucki_dbus.py' Skripts:**

```
trucki_dbus.py
├── MQTT Client (paho-mqtt)
│   └── subscribed auf 192.168.0.201:1886
├── Werte zwischenspeichern
└── dbus Service
    └── com.victronenergy.pvinverter.trucki
        ├── /Ac/Power          ← ACDISPLAY
        ├── /Ac/L1/Power       ← ACDISPLAY (single phase)
        ├── /Ac/L1/Voltage     ← VGRID
        ├── /Ac/L1/Current     ← berechnet (P/U)
        ├── /Ac/Energy/Forward ← TOTALENERGY
        ├── /Connected         ← STATE
        └── /Temperature       ← TEMPERATURE
```

---

# Phase 1. **Deployment – so installierst du es auf dem Raspberry:**




```bash
# 1. Verzeichnis anlegen (überlebt Venus-Updates)
mkdir -p /data/trucki

# 2. Skript hochladen (per scp vom PC)
scp trucki_dbus.py root@<raspberry-ip>:/data/trucki/

# 3. paho-mqtt installieren
pip3 install paho-mqtt

# 4. Service installieren
scp trucki-dbus.service root@<raspberry-ip>:/etc/systemd/system/
systemctl daemon-reload
systemctl enable trucki-dbus
systemctl start trucki-dbus

# 5. Logs beobachten
journalctl -u trucki-dbus -f
```

Phyton Skripte für das VenusOS Image als .zip Archiv:

|- files
  |_ trucki_dbus.py
  |_ trucki_dbus.service

[Trucki_dbus_files.zip](https://github.com/user-attachments/files/28664921/Trucki_dbus_files.zip)
---

**zum nächsten Schritt:**

Teste **zuerst** manuell ohne Service:
```bash
python3 /data/trucki/trucki_dbus.py  
```

Und schau in `dbus-spy` ob der Service auftaucht, **bevor** du ihn als Systemd-Service einrichtest. Fehlersuche im laufenden Service ist unnötig schwer.

Gut. Dann direkt los – aber ich widerspreche dem naheliegenden nächsten Schritt:

**Nicht sofort das Skript hochladen.**

Erst prüfen ob die Voraussetzungen auf dem Raspberry stimmen. Sonst debuggst du blind.

---

**Schritt 1 – SSH rein und Umgebung prüfen:**

```bash
ssh root@<raspberry-ip>
```

Dann diese 3 Checks nacheinander:

```bash
# Python Version (brauchen wir 3.x)
python3 --version

# Ist paho-mqtt bereits installiert?
python3 -c "import paho.mqtt.client; print('paho OK')"

# Sind die Victron dbus Bibliotheken vorhanden?
ls /opt/victronenergy/dbus-systemcalc-py/ext/velib_python/vedbus.py
```

---

**Was wir erwarten:**

| Check | Erwartetes Ergebnis |
|---|---|
| Python | 3.6 oder höher |
| paho-mqtt | entweder OK oder muss installiert werden |
| vedbus.py | muss existieren, sonst falscher Venus-Pfad |

---

**Mein Widerspruch vorab:**

Der Pfad `/opt/victronenergy/dbus-systemcalc-py/ext/velib_python/` ist auf Venus Large **meistens** korrekt – aber nicht garantiert. Wenn `vedbus.py` dort nicht liegt, suchen wir:

```bash
find / -name "vedbus.py" 2>/dev/null
```

Führ die Checks aus und check die Ausgaben – 
