import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import board
import busio
from adafruit_pn532.i2c import PN532_I2C

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data.db"

i2c = busio.I2C(board.SCL, board.SDA)
pn532 = PN532_I2C(i2c, debug=False)

ic, ver, rev, support = pn532.firmware_version
print(f"PN532 ready: IC=0x{ic:02X}, firmware={ver}.{rev}", flush=True)

pn532.SAM_configuration()
print("NFC recognition service started.", flush=True)

last_uid = None
last_seen = 0.0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def set_kiosk_state(state, employee_id=None, employee_name=None, badge_id=None):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kiosk_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL,
            employee_id INTEGER,
            employee_name TEXT,
            badge_id TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        INSERT INTO kiosk_state
            (id, state, employee_id, employee_name, badge_id, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            state = excluded.state,
            employee_id = excluded.employee_id,
            employee_name = excluded.employee_name,
            badge_id = excluded.badge_id,
            updated_at = excluded.updated_at
        """,
        (state, employee_id, employee_name, badge_id, now_iso())
    )

    conn.commit()
    conn.close()


def get_employee(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """
        SELECT id, name
        FROM employees
        WHERE badge_id = ? AND active = 1
        """,
        (uid,)
    ).fetchone()

    conn.close()
    return dict(row) if row else None


set_kiosk_state("idle")


while True:
    try:
        uid = pn532.read_passive_target(timeout=0.5)

        if uid is None:
            time.sleep(0.1)
            continue

        uid_hex = uid.hex().upper()
        now = time.monotonic()

        if uid_hex == last_uid and now - last_seen < 5:
            time.sleep(0.1)
            continue

        last_uid = uid_hex
        last_seen = now

        employee = get_employee(uid_hex)

        if employee is None:
            print(f"Unknown tag: {uid_hex}", flush=True)
            set_kiosk_state(
                "unknown",
                badge_id=uid_hex
            )
            continue

        print(
            f"Employee detected: {employee['name']} ({uid_hex})",
            flush=True
        )

        set_kiosk_state(
            "employee_detected",
            employee_id=employee["id"],
            employee_name=employee["name"],
            badge_id=uid_hex
        )

    except KeyboardInterrupt:
        print("\nNFC service stopped.", flush=True)
        break

    except Exception as exc:
        print(f"NFC error: {exc}", flush=True)
        time.sleep(2)
