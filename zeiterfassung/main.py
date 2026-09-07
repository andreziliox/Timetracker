from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data.db"


class EmployeeCreate(BaseModel):
    name: str
    badge_id: Optional[str] = None


class ScanPayload(BaseModel):
    action: str
    source: str = "manual"
    employee_id: int | None = None


class KioskBookPayload(BaseModel):
    employee_id: int
    action: str


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            badge_id TEXT UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            action TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS kiosk_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL,
            employee_id INTEGER,
            employee_name TEXT,
            badge_id TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        INSERT OR IGNORE INTO kiosk_state
            (id, state, updated_at)
        VALUES (1, 'idle', ?)
    """, (now_iso(),))

    conn.commit()
    conn.close()


app = FastAPI(title="Zeiterfassung")
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "app" / "static")),
    name="static"
)
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={}
    )


@app.get("/api/employees")
def list_employees():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, name, badge_id, active, created_at
        FROM employees
        ORDER BY name
        """
    ).fetchall()
    conn.close()
    return {"employees": [dict(row) for row in rows]}


@app.post("/api/employees")
def create_employee(emp: EmployeeCreate):
    conn = get_connection()
    try:
        created_at = now_iso()
        cur = conn.execute(
            """
            INSERT INTO employees (name, badge_id, active, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (emp.name.strip(), emp.badge_id, created_at)
        )
        emp_id = cur.lastrowid
        conn.commit()
        return {
            "id": emp_id,
            "name": emp.name.strip(),
            "badge_id": emp.badge_id
        }
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail="Name oder Badge-ID ist bereits vorhanden."
        ) from exc
    finally:
        conn.close()


@app.post("/api/scan")
def api_scan(payload: ScanPayload):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings (employee_id, action, source, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (payload.employee_id, payload.action, payload.source, now_iso())
    )
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "received": payload.model_dump()
    }


@app.get("/api/bookings")
def list_bookings():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT b.id, b.employee_id, e.name AS employee_name,
               b.action, b.source, b.created_at
        FROM bookings b
        LEFT JOIN employees e ON b.employee_id = e.id
        ORDER BY b.created_at DESC
        LIMIT 100
        """
    ).fetchall()
    conn.close()
    return {"bookings": [dict(row) for row in rows]}


@app.get("/api/kiosk/status")
def kiosk_status():
    conn = get_connection()
    row = conn.execute(
        """
        SELECT state, employee_id, employee_name, badge_id, updated_at
        FROM kiosk_state
        WHERE id = 1
        """
    ).fetchone()
    conn.close()

    if row is None:
        return {
            "state": "idle",
            "employee_id": None,
            "employee_name": None,
            "badge_id": None,
            "updated_at": now_iso()
        }

    return dict(row)


@app.get("/api/kiosk/today/{employee_id}")
def kiosk_today(employee_id: int):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, action, source, created_at
        FROM bookings
        WHERE employee_id = ?
          AND date(created_at, 'localtime') = date('now', 'localtime')
        ORDER BY created_at ASC
        """,
        (employee_id,)
    ).fetchall()
    conn.close()

    return {"bookings": [dict(row) for row in rows]}


@app.post("/api/kiosk/book")
def kiosk_book(payload: KioskBookPayload):
    if payload.action not in {"Kommen", "Pause", "Gehen"}:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Aktion."
        )

    conn = get_connection()

    try:
        employee = conn.execute(
            """
            SELECT id, name, badge_id
            FROM employees
            WHERE id = ? AND active = 1
            """,
            (payload.employee_id,)
        ).fetchone()

        if employee is None:
            raise HTTPException(
                status_code=404,
                detail="Mitarbeiter nicht gefunden."
            )

        last_booking = conn.execute(
            """
            SELECT action
            FROM bookings
            WHERE employee_id = ?
              AND date(created_at, 'localtime') = date('now', 'localtime')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (payload.employee_id,)
        ).fetchone()

        action = payload.action
        last_action = last_booking["action"] if last_booking else None

        if action == "Pause":
            if last_action == "Pause Beginn":
                action = "Pause Ende"
            else:
                action = "Pause Beginn"

        if action == "Kommen" and last_action not in {
            None,
            "Gehen",
            "Pause Ende"
        }:
            raise HTTPException(
                status_code=400,
                detail="Kommen ist aktuell nicht möglich."
            )

        if action == "Pause Beginn" and last_action != "Kommen":
            raise HTTPException(
                status_code=400,
                detail="Pause kann nur während der Arbeitszeit beginnen."
            )

        if action == "Pause Ende" and last_action != "Pause Beginn":
            raise HTTPException(
                status_code=400,
                detail="Es läuft aktuell keine Pause."
            )

        if action == "Gehen" and last_action not in {
            "Kommen",
            "Pause Ende"
        }:
            raise HTTPException(
                status_code=400,
                detail="Gehen ist aktuell nicht möglich."
            )

        created_at = now_iso()

        conn.execute(
            """
            INSERT INTO bookings
                (employee_id, action, source, created_at)
            VALUES (?, ?, 'kiosk', ?)
            """,
            (payload.employee_id, action, created_at)
        )

        conn.execute(
            """
            UPDATE kiosk_state
            SET state = 'booked',
                employee_id = ?,
                employee_name = ?,
                badge_id = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                employee["id"],
                employee["name"],
                employee["badge_id"],
                created_at
            )
        )

        conn.commit()

        return {
            "ok": True,
            "action": action,
            "employee_id": employee["id"],
            "employee_name": employee["name"],
            "created_at": created_at
        }
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()



@app.post("/api/kiosk/reset")
def kiosk_reset():
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE kiosk_state
            SET state = 'idle',
                employee_id = NULL,
                employee_name = NULL,
                badge_id = NULL,
                updated_at = ?
            WHERE id = 1
            """,
            (now_iso(),)
        )
        conn.commit()
        return {"ok": True, "state": "idle"}
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return {"ok": True}
