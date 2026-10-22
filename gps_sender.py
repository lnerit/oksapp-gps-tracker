#!/usr/bin/env python3
"""Read a USB G-Mouse (NMEA RMC), queue GPS fixes, POST to OKSApp.

Configuration is read from environment variables (oksapp.env under systemd).
API payload fields are proposed; align make_payload() with the real OKSApp DTO.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pynmea2
import requests
import serial

HERE = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("QUEUE_DB", str(HERE / "gps_queue.db")))
GPS_PORT = os.getenv("GPS_PORT", "/dev/ttyUSB0")
GPS_BAUD = int(os.getenv("GPS_BAUD", "9600"))
DEVICE_ID = os.getenv("DEVICE_ID", "PI-GPS-001")
API_URL = os.getenv("OKSAPP_URL", "https://oksapp.com.pg/api/coordinates")
API_TOKEN = os.getenv("OKSAPP_TOKEN", "")
API_KEY = os.getenv("OKSAPP_API_KEY", "")
UPLOAD_INTERVAL = float(os.getenv("UPLOAD_INTERVAL", "10"))  # seconds between saved GPS fixes
RECONNECT_DELAY = 5  # seconds before retrying a disconnected GPS
HTTP_TIMEOUT = 10  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("oksapp-gps")


def open_db():
    return sqlite3.connect(str(DATABASE_PATH), timeout=30)


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open_db() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            )
        """)
        db.commit()


def enqueue(payload):
    # Transaction commits before the uploader is allowed to remove the fix.
    with open_db() as db:
        db.execute(
            "INSERT INTO pending (payload) VALUES (?)",
            (json.dumps(payload, separators=(",", ":")),),
        )
        db.commit()


def oldest_pending():
    with open_db() as db:
        return db.execute(
            "SELECT id, payload FROM pending ORDER BY id LIMIT 1"
        ).fetchone()


def delete_uploaded(row_id):
    with open_db() as db:
        db.execute("DELETE FROM pending WHERE id = ?", (row_id,))
        db.commit()


def pending_count():
    with open_db() as db:
        return db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]


def make_payload(nmea_line: str) -> Optional[dict]:
    """Return a fix from a valid RMC sentence or None for other sentences."""
    try:
        msg = pynmea2.parse(nmea_line, check=True)
    except (pynmea2.ParseError, ValueError, TypeError):
        return None

    if not isinstance(msg, pynmea2.RMC) or msg.status != "A":
        return None
    if msg.datestamp is None or msg.timestamp is None:
        return None

    try:
        latitude = float(msg.latitude)
        longitude = float(msg.longitude)
        speed_knots = float(msg.spd_over_grnd or 0)
    except (ValueError, TypeError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    utc = datetime.combine(msg.datestamp, msg.timestamp, tzinfo=timezone.utc)
    return {
        "eventId": str(uuid.uuid4()),
        "deviceId": DEVICE_ID,
        "latitude": round(latitude, 7),
        "longitude": round(longitude, 7),
        "speedKmh": round(max(0, speed_knots) * 1.852, 2),
        "recordedAtUtc": utc.isoformat().replace("+00:00", "Z"),
    }


def upload_loop():
    """Retries oldest saved GPS point first; only HTTP 2xx removes it."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    if API_TOKEN:
        session.headers["Authorization"] = f"Bearer {API_TOKEN}"
    if API_KEY:
        session.headers["X-API-Key"] = API_KEY

    while True:
        item = oldest_pending()
        if item is None:
            time.sleep(2)
            continue
        row_id, payload_text = item
        try:
            response = session.post(
                API_URL,
                json=json.loads(payload_text),
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,  # login redirects are not successful uploads
            )
            if not 200 <= response.status_code < 300:
                log.warning(
                    "API returned HTTP %s for queued row %s: %s",
                    response.status_code,
                    row_id,
                    response.text[:250].replace("\n", " "),
                )
                time.sleep(30 if 400 <= response.status_code < 500 else 15)
                continue
        except requests.RequestException as exc:
            log.warning("Internet/API unavailable, row %s retained: %s", row_id, exc)
            time.sleep(15)
            continue

        delete_uploaded(row_id)
        log.info("OKSApp accepted row %s (HTTP %s)", row_id, response.status_code)


def gps_loop():
    last_queued_time = -float("inf")
    while True:
        try:
            with serial.Serial(GPS_PORT, GPS_BAUD, timeout=2) as gps:
                log.info("Connected to G-Mouse at %s (%s baud)", GPS_PORT, GPS_BAUD)
                while True:
                    line = gps.readline().decode("ascii", errors="ignore").strip()
                    if not line.startswith("$"):
                        continue
                    payload = make_payload(line)
                    if payload is None:
                        continue
                    now = time.monotonic()
                    if now - last_queued_time < UPLOAD_INTERVAL:
                        continue
                    enqueue(payload)
                    last_queued_time = now
                    log.info(
                        "Saved GPS fix for %s (%.6f, %.6f); pending=%s",
                        payload["deviceId"], payload["latitude"],
                        payload["longitude"], pending_count(),
                    )
        except (serial.SerialException, OSError) as exc:
            log.warning("GPS unavailable/disconnected: %s. Retrying...", exc)
            time.sleep(RECONNECT_DELAY)


def main():
    if not API_URL.startswith("https://"):
        raise SystemExit("Refusing non-HTTPS OKSAPP_URL")
    if UPLOAD_INTERVAL <= 0:
        raise SystemExit("UPLOAD_INTERVAL must be a positive number of seconds")
    if not DEVICE_ID.strip():
        raise SystemExit("DEVICE_ID is required")
    init_db()
    log.info("GPS tracker started: device=%s, queued=%d", DEVICE_ID, pending_count())
    threading.Thread(target=upload_loop, daemon=True, name="uploader").start()
    gps_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("GPS tracker stopped")
