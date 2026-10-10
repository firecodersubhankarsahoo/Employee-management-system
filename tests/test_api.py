import os
import concurrent.futures
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app, db, init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# 1. System & Health Tests
# --------------------------------------------------------------------------- #
def test_health_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# 2. Employee CRUD & Validation Tests
# --------------------------------------------------------------------------- #
def test_create_employee_success(client):
    emp_code = "EMP9001"
    db.employees.delete_one({"emp_code": emp_code})
    payload = {
        "emp_code": emp_code,
        "name": "Test User",
        "email": "test.user@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == emp_code
    assert isinstance(data["created_at"], int)
    assert data["created_at"] > 100000000000


def test_create_employee_duplicate_409(client):
    emp_code = "EMP9001"
    payload = {
        "emp_code": emp_code,
        "name": "Test User",
        "email": "test.user@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


def test_create_employee_invalid_validation_422(client):
    # Invalid emp_code regex
    res = client.post(
        "/employees",
        json={
            "emp_code": "INVALID",
            "name": "Bad",
            "email": "bad@email.com",
            "department": "IT",
            "joined_on": "2026-01-01",
        },
    )
    assert res.status_code == 422

    # shift_start == shift_end
    res2 = client.post(
        "/employees",
        json={
            "emp_code": "EMP9002",
            "name": "Equal Shift",
            "email": "eq@example.com",
            "department": "IT",
            "shift_start": "10:00",
            "shift_end": "10:00",
            "joined_on": "2026-01-01",
        },
    )
    assert res2.status_code == 422


def test_list_employees_pagination(client):
    res = client.get("/employees?page=1&page_size=3")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert len(data["items"]) <= 3
    assert data["page"] == 1
    assert data["page_size"] == 3


# --------------------------------------------------------------------------- #
# 3. Punch-in Tests (R1, R2, Concurrency)
# --------------------------------------------------------------------------- #
def test_punch_in_unknown_employee_404(client):
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP9999"})
    assert res.status_code == 404
    assert "Employee not found" in res.json()["detail"]


def test_punch_in_late_minutes_rule_r2(client):
    emp_code = "EMP9010"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Late Tester",
            "email": "late@example.com",
            "department": "QA",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # 1. Exactly 09:40:00 IST on 2026-07-06 (inside grace period: 10 minutes) -> late_minutes = 0
    ts_grace = int(datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_grace = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_grace})
    assert res_grace.status_code == 201
    assert res_grace.json()["late_minutes"] == 0

    # Clean up for second test
    db.attendance_logs.delete_many({"emp_code": emp_code})

    # 2. 09:40:01 IST on 2026-07-06 (strictly more than 10:00 after shift_start) -> late_minutes = 10
    ts_late = int(datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    res_late = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_late})
    assert res_late.status_code == 201
    assert res_late.json()["late_minutes"] == 10

    # 3. Duplicate punch-in for same date -> 409
    res_dup = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_late})
    assert res_dup.status_code == 409


def test_punch_in_overnight_shift_r1(client):
    emp_code = "EMP9011"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Overnight Tester",
            "email": "overnight@example.com",
            "department": "Security",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-01-01",
        },
    )

    # Punch in at 01:15 IST on 2026-07-07 (01:15 < 06:00, belongs to 2026-07-06)
    ts = int(datetime(2026, 7, 6, 19, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts})
    assert res.status_code == 201
    data = res.json()
    assert data["date"] == "2026-07-06"
    assert data["late_minutes"] == 195


def test_punch_in_concurrency_race(client):
    emp_code = "EMP9012"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Race Tester",
            "email": "race@example.com",
            "department": "QA",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    ts = int(datetime(2026, 7, 20, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    # Run parallel requests
    def do_punch():
        with TestClient(app) as c:
            return c.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_punch) for _ in range(5)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 4


# --------------------------------------------------------------------------- #
# 4. Punch-out Tests (R3, R4, R5, Concurrency)
# --------------------------------------------------------------------------- #
def test_punch_out_lifecycle(client):
    emp_code = "EMP9020"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Punchout Tester",
            "email": "po@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Punch in at 09:30 IST on 2026-07-10 (04:00 UTC)
    ts_in = int(datetime(2026, 7, 10, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_in})

    # Invalid punch-out (punched_at == punch_in) -> 422
    res_equal = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_in})
    assert res_equal.status_code == 422

    # Invalid punch-out (> 24 hours after punch-in) -> 422
    ts_25h = ts_in + 25 * 3600 * 1000
    res_25h = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_25h})
    assert res_25h.status_code == 422

    # Valid punch-out at 19:15 IST (13:45 UTC): 9.75 work hours, 45 min overtime (>= 30 min)
    ts_out = int(datetime(2026, 7, 10, 13, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_out = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_out})
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["work_hours"] == 9.75
    assert data["overtime_minutes"] == 45
    assert data["half_day"] is False

    # Repeated punch-out on same record -> 409
    res_repeat = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_out})
    assert res_repeat.status_code == 409


def test_punch_out_concurrency_race(client):
    emp_code = "EMP9021"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Race PO Tester",
            "email": "racepo@example.com",
            "department": "QA",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    ts_in = int(datetime(2026, 7, 21, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_in})

    ts_out = int(datetime(2026, 7, 21, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    def do_punch_out():
        with TestClient(app) as c:
            return c.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_out})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_punch_out) for _ in range(5)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4


# --------------------------------------------------------------------------- #
# 5. List Attendance & Validation Tests
# --------------------------------------------------------------------------- #
def test_list_attendance_filters_and_validation(client):
    # Invalid date range: date_from > date_to -> 422
    res_bad = client.get("/attendance?date_from=2026-07-20&date_to=2026-07-10")
    assert res_bad.status_code == 422

    # Normal list
    res = client.get("/attendance?page=1&page_size=5")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    if data["items"]:
        item = data["items"][0]
        assert "_id" not in item
        assert "date" in item
        assert "emp_code" in item


# --------------------------------------------------------------------------- #
# 6. Regularization (PATCH) Tests
# --------------------------------------------------------------------------- #
def test_regularize_attendance(client):
    emp_code = "EMP9030"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Reg Tester",
            "email": "reg@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Punch in at 10:15 IST on 2026-07-15 (45 min late)
    ts_in = int(datetime(2026, 7, 15, 4, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_in})

    # Punch out at 18:30 IST (13:00 UTC)
    ts_out = int(datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ts_out})

    # 1. Reject if no changes are made -> 422
    res_noop = client.patch(
        f"/attendance/{emp_code}/2026-07-15",
        json={"reason": "Testing noop", "regularized_by": "admin"},
    )
    assert res_noop.status_code == 422

    # 2. Reject setting status ABSENT with punch times -> 422
    res_absent_bad = client.patch(
        f"/attendance/{emp_code}/2026-07-15",
        json={
            "status": "ABSENT",
            "punch_in": ts_in,
            "reason": "Setting absent",
            "regularized_by": "admin",
        },
    )
    assert res_absent_bad.status_code == 422

    # 3. Correct punch_in to 09:28 IST (03:58 UTC) -> late_minutes should become 0
    new_in = int(datetime(2026, 7, 15, 3, 58, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_patch = client.patch(
        f"/attendance/{emp_code}/2026-07-15",
        json={
            "punch_in": new_in,
            "reason": "Biometric scanner error",
            "regularized_by": "hr.manager",
        },
    )
    assert res_patch.status_code == 200
    data = res_patch.json()
    assert data["late_minutes"] == 0
    assert len(data["history"]) == 1
    assert data["history"][0]["by"] == "hr.manager"
    assert "punch_in" in data["history"][0]["changes"]
    assert "late_minutes" in data["history"][0]["changes"]

    # 4. Correct status to ABSENT (without punch times) -> clears punch times
    res_absent = client.patch(
        f"/attendance/{emp_code}/2026-07-15",
        json={
            "status": "ABSENT",
            "reason": "Marked absent after dispute",
            "regularized_by": "hr.manager",
        },
    )
    assert res_absent.status_code == 200
    absent_data = res_absent.json()
    assert absent_data["status"] == "ABSENT"
    assert absent_data["punch_in"] is None
    assert absent_data["punch_out"] is None
    assert absent_data["work_hours"] is None
    assert len(absent_data["history"]) == 2


# --------------------------------------------------------------------------- #
# 7. Analytics Tests
# --------------------------------------------------------------------------- #
def test_analytics_mid_month_joiner(client):
    emp_code = "EMP9040"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    # Joined on July 20, 2026
    client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Joiner Tester",
            "email": "joiner@example.com",
            "department": "Sales",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-20",
        },
    )

    # July 20 to July 31, 2026:
    # July 20 (Mon), 21 (Tue), 22 (Wed), 23 (Thu), 24 (Fri),
    # July 27 (Mon), 28 (Tue), 29 (Wed), 30 (Thu), 31 (Fri) = 10 working days
    res = client.get(f"/analytics/employees/{emp_code}/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 10


def test_leaderboard_ties_and_limit(client):
    # Set up tied employees for late leaderboard
    # EMP9051 and EMP9052 tie on 60 minutes, EMP9053 has 30 minutes
    for code, mins in [("EMP9051", 60), ("EMP9052", 60), ("EMP9053", 30)]:
        db.employees.delete_one({"emp_code": code})
        db.attendance_logs.delete_many({"emp_code": code})
        client.post(
            "/employees",
            json={
                "emp_code": code,
                "name": f"Tie {code}",
                "email": f"{code.lower()}@example.com",
                "department": "TieDept",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-01-01",
            },
        )
        db.attendance_logs.insert_one(
            {
                "emp_code": code,
                "date": "2026-08-05",
                "status": "PRESENT",
                "punch_in": datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc),
                "late_minutes": mins,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            }
        )

    # With limit=1: EMP9051 and EMP9052 both have rank 1.
    # Because limit is applied AFTER ranking (rank <= 1), BOTH tied employees should be returned!
    res = client.get("/analytics/leaderboard/late?month=2026-08&limit=1&department=TieDept")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2  # Both rank 1 rows returned
    assert items[0]["rank"] == 1
    assert items[1]["rank"] == 1
    # Sorted by total_late_minutes desc, emp_code asc
    assert items[0]["emp_code"] == "EMP9051"
    assert items[1]["emp_code"] == "EMP9052"

    # With limit=2: Ranks are 1, 1, and 3 (rank 2 is skipped!).
    # So EMP9053 (rank 3) is NOT <= 2! Only the 2 rank 1 rows should be returned.
    res2 = client.get("/analytics/leaderboard/late?month=2026-08&limit=2&department=TieDept")
    assert res2.status_code == 200
    items2 = res2.json()["items"]
    assert len(items2) == 2


def test_department_trend_gap_filling(client):
    # Department with employees but no logs on certain days
    res = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 7  # Exactly 7 days
    dates = [item["date"] for item in data["items"]]
    assert dates == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
        "2026-07-06",
        "2026-07-07",
    ]
    # Check weekend flags
    assert data["items"][3]["is_working_day"] is False  # July 4 is Saturday
    assert data["items"][4]["is_working_day"] is False  # July 5 is Sunday
    assert data["items"][3]["attendance_rate"] is None
    assert data["items"][4]["attendance_rate"] is None


# --------------------------------------------------------------------------- #
# 8. Admin Explain Tests
# --------------------------------------------------------------------------- #
def test_admin_explain_endpoints(client):
    # 1. attendance_list
    r1 = client.get("/admin/explain/attendance_list?page=1&page_size=20")
    assert r1.status_code == 200
    assert r1.json()["endpoint"] == "attendance_list"
    assert r1.json()["collection"] == "attendance_logs"

    # 2. employee_monthly
    r2 = client.get("/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07")
    assert r2.status_code == 200
    assert r2.json()["endpoint"] == "employee_monthly"

    # 3. department_summary
    r3 = client.get("/admin/explain/department_summary?month=2026-07")
    assert r3.status_code == 200
    assert r3.json()["endpoint"] == "department_summary"

    # 4. late_leaderboard
    r4 = client.get("/admin/explain/late_leaderboard?month=2026-07")
    assert r4.status_code == 200
    assert r4.json()["endpoint"] == "late_leaderboard"

    # 5. department_trend
    r5 = client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-10")
    assert r5.status_code == 200
    assert r5.json()["endpoint"] == "department_trend"
