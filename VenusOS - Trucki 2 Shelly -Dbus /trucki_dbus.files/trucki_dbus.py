# Datei nach "/data/trucki/" kopieren.
#!/usr/bin/env python3

"""
trucki_dbus.py – Phase 1
Integriert das Trucki2ShellyGateway in VenusOS über dbus.
Subscribed auf externen MQTT Broker, schreibt Werte in dbus als pvinverter.

Autor: Thcoding with Claude
Venus Large auf Raspberry Pi 3B+
"""

import sys
import os
import json
import logging
import paho.mqtt.client as mqtt

# VenusOS dbus Bibliotheken (bereits auf Venus Large vorhanden)
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
from vedbus import VeDbusService
from gi.repository import GLib

# ── Konfiguration ────────────────────────────────────────────────────────────

MQTT_BROKER   = "192.168.0.201"
MQTT_PORT     = 1883
MQTT_TOPIC    = "TRUCKI/#"

MQTT_USER     = "mqtttrucki"
MQTT_PASS     = "mqttpw"

DBUS_SERVICE  = "com.victronenergy.pvinverter.trucki"
DBUS_INSTANCE = 40  # Eindeutige Instanznummer (nicht mit anderen kollidieren)

LOG_LEVEL     = logging.INFO

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("trucki_dbus")

# ── Globaler Zustand ─────────────────────────────────────────────────────────

state = {
    "power":        0.0,   # ACDISPLAY [W]
    "voltage":      0.0,   # VGRID [V]
    "current":      0.0,   # berechnet: P / U
    "energy_day":   0.0,   # DAYENERGY [kWh]
    "energy_total": 0.0,   # TOTALENERGY [kWh]
    "temperature":  0.0,   # TEMPERATURE [°C]
    "vbat":         0.0,   # VBAT [V]
    "meter":        0.0,   # METER [W] – Hausverbrauch
    "max_power":    0.0,   # MAXPOWER [W]
    "target":       0.0,   # TARGET [W]
    "raw_state":    "OFF", # STATE [LOW, ON, OFF, HIGH]
    "connected":    0,     # 1 = verbunden, 0 = getrennt
}

# ── MQTT Callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info(f"MQTT verbunden mit {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC)
        log.info(f"Subscribed auf: {MQTT_TOPIC}")
        state["connected"] = 1
    else:
        log.error(f"MQTT Verbindung fehlgeschlagen, Code: {rc}")
        state["connected"] = 0


def on_disconnect(client, userdata, rc):
    log.warning(f"MQTT getrennt (Code: {rc}), versuche Reconnect...")
    state["connected"] = 0


def on_message(client, userdata, msg):
    topic   = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()

    try:
        value = float(payload)
    except ValueError:
        value = payload  # z.B. STATE liefert Strings wie "ON"

    suffix = topic.split("/")[-1].upper()

    if suffix == "ACDISPLAY":
        state["power"] = float(value)
        # Strom berechnen wenn Spannung bekannt
        if state["voltage"] > 0:
            state["current"] = state["power"] / state["voltage"]

    elif suffix == "VGRID":
        state["voltage"] = float(value)
        if state["voltage"] > 0 and state["power"] != 0:
            state["current"] = state["power"] / state["voltage"]

    elif suffix == "VBAT":
        state["vbat"] = float(value)

    elif suffix == "TEMPERATURE":
        state["temperature"] = float(value)

    elif suffix == "DAYENERGY":
        state["energy_day"] = float(value)

    elif suffix == "TOTALENERGY":
        state["energy_total"] = float(value)

    elif suffix == "METER":
        state["meter"] = float(value)

    elif suffix == "MAXPOWER":
        state["max_power"] = float(value)

    elif suffix == "TARGET":
        state["target"] = float(value)

    elif suffix == "STATE":
        state["raw_state"] = str(value)

    else:
        log.debug(f"Unbekanntes Topic ignoriert: {topic} = {payload}")


# ── dbus Service aufbauen ─────────────────────────────────────────────────────

def create_dbus_service():
    service = VeDbusService(DBUS_SERVICE)

    # Pflichtpfade für pvinverter
    service.add_path("/Mgmt/ProcessName",    __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.0.0 Phase 1")
    service.add_path("/Mgmt/Connection",     f"MQTT {MQTT_BROKER}:{MQTT_PORT}")
    service.add_path("/DeviceInstance",      DBUS_INSTANCE)
    service.add_path("/ProductId",           0xFFFF)
    service.add_path("/ProductName",         "Trucki2Shelly")
    service.add_path("/FirmwareVersion",     0)
    service.add_path("/HardwareVersion",     0)
    service.add_path("/Connected",           0)

    # AC Leistungswerte – einphasig auf L1
    service.add_path("/Ac/Power",            0.0, gettextcallback=lambda p, v: f"{v:.0f} W")
    service.add_path("/Ac/L1/Power",         0.0, gettextcallback=lambda p, v: f"{v:.0f} W")
    service.add_path("/Ac/L1/Voltage",       0.0, gettextcallback=lambda p, v: f"{v:.1f} V")
    service.add_path("/Ac/L1/Current",       0.0, gettextcallback=lambda p, v: f"{v:.2f} A")

    # Energie
    service.add_path("/Ac/Energy/Forward",   0.0, gettextcallback=lambda p, v: f"{v:.2f} kWh")

    # Zusatzwerte (sichtbar in Gerätemenü)
    service.add_path("/Temperature",         0.0, gettextcallback=lambda p, v: f"{v:.1f} °C")
    service.add_path("/Dc/0/Voltage",        0.0, gettextcallback=lambda p, v: f"{v:.1f} V")
    service.add_path("/StatusCode",          0)

    log.info(f"dbus Service erstellt: {DBUS_SERVICE} (Instanz {DBUS_INSTANCE})")
    return service


# ── dbus updaten (wird per GLib Timer aufgerufen) ────────────────────────────

STATE_MAP = {
    "ON":   7,  # Running
    "OFF":  0,  # Stopped
    "LOW":  11, # Low power
    "HIGH": 11, # High power (kein besserer Code)
}

def update_dbus(service):
    service["/Connected"]          = state["connected"]
    service["/Ac/Power"]           = state["power"]
    service["/Ac/L1/Power"]        = state["power"]
    service["/Ac/L1/Voltage"]      = state["voltage"]
    service["/Ac/L1/Current"]      = state["current"]
    service["/Ac/Energy/Forward"]  = state["energy_total"]
    service["/Temperature"]        = state["temperature"]
    service["/Dc/0/Voltage"]       = state["vbat"]
    service["/StatusCode"]         = STATE_MAP.get(state["raw_state"], 0)

    log.debug(
        f"dbus Update: {state['power']:.0f}W | "
        f"{state['voltage']:.1f}V | "
        f"{state['current']:.2f}A | "
        f"Status={state['raw_state']}"
    )
    return True  # GLib Timer weiterlaufen lassen


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    log.info("Trucki dbus Service startet...")

    # MQTT Client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    # dbus Service
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    service = create_dbus_service()

    # Alle 2 Sekunden dbus aktualisieren
    GLib.timeout_add(2000, lambda: update_dbus(service))

    log.info("Service läuft. STRG+C zum Beenden.")
    mainloop = GLib.MainLoop()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        log.info("Beendet.")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
