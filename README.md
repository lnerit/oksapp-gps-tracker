# OKSApp GPS Tracker — Raspberry Pi Zero 2 W

**Pipeline:** USB G-Mouse → Raspberry Pi Zero 2 W → SQLite offline queue → HTTPS POST → `https://oksapp.com.pg/api/coordinates`

Runs continuously, starts automatically after every reboot, retries after G-Mouse disconnects and resumes queued uploads after Wi-Fi returns. Uses valid NMEA RMC fixes (with GPS UTC time). Does not require GPIO soldering for a USB GPS.

**Important:** The API route is specified by you, but its accepted JSON fields and authentication method are not confirmed. Align `make_payload()` in `gps_sender.py` with your actual ASP.NET Core endpoint DTO, and ensure server-side authentication and `eventId` deduplication. Only track devices and vehicles with authorization.

## Files

- `gps_sender.py`: GPS serial reader, NMEA parser, SQLite queue, HTTPS uploader and reconnection handling.
- `oksapp.env.example`: server URL, device identifier, GPS port, baud rate and optional credentials.
- `install.sh`: dependency install and automatic systemd boot service.
- `requirements.txt`: Python dependencies.
- `tests/test_gps_sender.py`: example parser and queue tests.

## Hardware & Raspberry Pi OS

1. Raspberry Pi Zero 2 W with Raspberry Pi OS Lite installed, 2.4 GHz Wi-Fi configured and access to internet (e.g., Starlink).
2. G-Mouse USB receiver via **USB OTG** port (not PWR IN). Provide sufficient power, or a powered USB hub if needed.
3. Position G-Mouse outdoors or under an unobstructed view of sky for the first GPS fix.

## Find the serial device

```bash
lsusb
ls -l /dev/serial/by-id/ /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
sudo dmesg | tail -n 40
```

Set `GPS_PORT` to the actual device; prefer a stable `/dev/serial/by-id/...` path if present. Set `GPS_BAUD=9600` for many models, but 4800 (and other values) are also possible. Check the hardware documentation if NMEA messages aren't readable.

## Install the complete project on the Pi

Copy `oksapp_gps_complete.zip` onto the Raspberry Pi user's home directory. Then:

```bash
cd ~
unzip oksapp_gps_complete.zip
cd ~/oks-gps
cp -n oksapp.env.example oksapp.env
nano oksapp.env  # Set DEVICE_ID, GPS_PORT, baud and authentication (if required)
chmod +x install.sh
./install.sh
```

The installer will create `oksapp.env` if absent; **review it**, especially `DEVICE_ID`, `GPS_PORT`, baud, and credentials:

```bash
nano ~/oks-gps/oksapp.env
sudo systemctl restart oksapp-gps  # Starts it if not already running
sudo systemctl status oksapp-gps
sudo journalctl -u oksapp-gps -f
```

If you already have an earlier version at `~/oks-gps`, back up `gps_queue.db` and your `oksapp.env` before replacing application files; don't overwrite the existing queue or your secrets. The queue database table is compatible with the earlier starter package.

If you prefer to test manually first, stop the service and load the environment variables:

```bash
sudo systemctl stop oksapp-gps
cd ~/oks-gps
set -a
source ./oksapp.env
set +a
.venv/bin/python gps_sender.py
# Ctrl+C to stop
sudo systemctl start oksapp-gps
```

For manual serial access after installation, log out and back in once if you get 'permission denied' (`install.sh` adds your user to `dialout`). The systemd service is directly given the `dialout` supplementary group.

## Expected request body (proposed, not confirmed)

```http
POST /api/coordinates HTTP/1.1
Content-Type: application/json
Authorization: Bearer YOUR_DEVICE_TOKEN  # Only if OKSApp requires it
```

```json
{
  "eventId": "37c36ca8-77c1-4a51-8b39-5728c963c157",
  "deviceId": "PI-GPS-001",
  "latitude": -6.6744,
  "longitude": 146.9987,
  "speedKmh": 35.5,
  "recordedAtUtc": "2026-10-22T02:00:00Z"
}
```

The example coordinates are fabricated for illustration. A new point is queued every `UPLOAD_INTERVAL` seconds once the GPS has a valid fix; the uploader drains the queue as the server accepts each point.

HTTP 2xx means the client removes that point from the local queue. Network errors and non-2xx HTTP responses **keep the point for retry**. If the server accepted it but the response was lost, a retry can send a duplicate; deduplicate using `eventId` on OKSApp. There is no queue size limit: monitor SD card space during prolonged outages.

A 400/422 often means API field names or validation requirements differ. A 401/403 suggests credentials/permissions, and a 404/405 suggests route or HTTP method mismatch. A 502/503 might be a gateway or server problem. The program does not follow HTTP redirects to a login page.

## Useful commands

```bash
sudo systemctl enable --now oksapp-gps
sudo systemctl status oksapp-gps
sudo journalctl -u oksapp-gps -n 100 --no-pager
sudo journalctl -u oksapp-gps -f
sudo systemctl restart oksapp-gps
sudo systemctl stop oksapp-gps
sudo systemctl disable --now oksapp-gps
```

Automatic startup and crashes are handled by `systemd` (`Restart=always`, 10-second delay). GPS serial disconnects are retried in Python every 5 seconds. No internet is needed to capture coordinates; a GPS satellite fix is needed to queue new data.

## Backend note

For robust ingestion, OKSApp should: authenticate individual devices, validate latitude (-90 to 90) and longitude (-180 to 180), accept GPS timestamps in UTC, enforce a UNIQUE constraint on `eventId`, return a 2xx response only after durable storage/queueing, and protect tracking location access. Store device secrets in `oksapp.env` with `chmod 600` and never commit it to source control.
