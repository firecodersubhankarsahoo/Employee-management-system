# Employee Attendance & Analytics API

A FastAPI backend implementing employee attendance tracking, manual regularization with audit trails, and MongoDB aggregation analytics.

## Prerequisites

- **Python**: 3.11+ (tested on Python 3.12)
- **MongoDB**: 6.0+ (MongoDB 7.0+ supported; uses `$densify` and `$setWindowFields`)

## Installation

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux / macOS:
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Configure the MongoDB connection via environment variables. Real environment variables override any `.env` file values:

| Variable | Description | Default |
|---|---|---|
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGO_DB` | MongoDB database name | `attendance_db` |

Example setup:
```bash
# Windows PowerShell:
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"

# Linux / macOS / Bash:
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"
```

## Running the Database

Ensure MongoDB 6.0+ is running locally or provide a connection string to a MongoDB Atlas cluster.

To run a local MongoDB instance using Docker:
```bash
docker run -d -p 27017:27017 --name mongodb mongo:7
```

## Seeding Sample Data

To load the sample documents from `sample_data/`:
```bash
python sample_seed.py
```
*Note: Sample data is not loaded automatically on application startup. Run `sample_seed.py` explicitly when sample data is required.*

## Running the Application

Start the FastAPI application with Uvicorn:
```bash
uvicorn app.main:app --port 8000
```

All indexes are created idempotently at application startup during the lifespan context.

## Application & Documentation URLs

Once the server is running, you can access:
- **Web Application Dashboard**: [http://localhost:8000](http://localhost:8000) (Interactive SPA for punching in/out, viewing real-time analytics, attendance logs, employee directory, and running explain query plans)
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Raw OpenAPI Schema**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

## Running Automated Tests

Run the complete test suite using pytest:
```bash
python -m pytest -v
```

The test suite covers:
- Readiness probe (`/health`)
- Employee creation, validation rules, and duplicate conflict handling (`POST /employees`)
- Employee pagination and department filtering (`GET /employees`)
- Punch-in business rules: R1 overnight shift logic, R2 grace period / late calculation, duplicate concurrency protection (`POST /attendance/punch-in`)
- Punch-out business rules: R3 overtime threshold, R4 half-up work hours, R5 half-day flag, repeat punch-out rejection, concurrent punch-out race safety (`POST /attendance/punch-out`)
- Attendance listing, compound sorting, and date validation (`GET /attendance`)
- Regularization audit trail, field change tracking, optimistic concurrency safety (`PATCH /attendance/{emp_code}/{date}`)
- Monthly employee analytics (`/analytics/employees/{emp_code}/monthly`)
- Department monthly cross-collection summary (`/analytics/departments/summary`)
- Late leaderboard with competition rank ties and post-ranking limit (`/analytics/leaderboard/late`)
- Department daily trend with `$densify` gap filling and 7-day moving averages (`/analytics/departments/{department}/trend`)
- Query execution plans verifying index scans (`IXSCAN`) and absence of collection scans (`COLLSCAN`) via `/admin/explain/{endpoint}`

## Implemented Features

1. **System & Health**:
   - `GET /health`: Pings MongoDB and returns 200 if connected, 503 otherwise.

2. **Employee Management**:
   - `POST /employees`: Enforces unique index on `emp_code`, validates `shift_start != shift_end`, formats `created_at` in UTC epoch ms.
   - `GET /employees`: Paginated, filtered by department, sorted by `emp_code` ascending.

3. **Attendance Logging**:
   - `POST /attendance/punch-in`: Enforces unique compound index `(emp_code, date)`, calculates IST attendance day and late minutes (R1, R2).
   - `POST /attendance/punch-out`: Atomically closes open session, calculates work hours (R4), overtime (R3), and half day (R5).
   - `GET /attendance`: Paginated, sorted by `date` desc then `emp_code` asc.
   - `PATCH /attendance/{emp_code}/{date}`: Validates regularization rules, recomputes derived metrics, appends audit history with field diffs.

4. **Analytics Aggregations**:
   - `GET /analytics/employees/{emp_code}/monthly`: Working days accounting for join date, Mon-Fri presence days, late/overtime totals, percentage attendance.
   - `GET /analytics/departments/summary`: Cross-collection pipeline counting headcounts with zero logs, department-level averages and counts.
   - `GET /analytics/leaderboard/late`: MongoDB `$setWindowFields` standard competition ranking (`$rank`) with post-ranking limit.
   - `GET /analytics/departments/{department}/trend`: Gap filling with `$densify` and rolling 7-day moving averages using `$setWindowFields`.

5. **Admin & Query Plans**:
   - `GET /admin/explain/{endpoint}`: Returns raw MongoDB explain documents with `executionStats` for all 5 target queries.

## Known Limitations

- Real-time MongoDB aggregation is used directly on all analytics endpoints as specified. For datasets scaling past 10 million documents (100x scale), asynchronous daily rollups or materialized summary collections should be introduced to minimize I/O load.





