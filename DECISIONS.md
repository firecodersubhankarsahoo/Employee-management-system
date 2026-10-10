# DECISIONS.md

1. **Indexes.**
We created unique indexes on `employees.emp_code` (guarantees business code uniqueness and O(1) lookups) and `attendance_logs.(emp_code, date)` (enforces the natural key and prevents duplicate punch-in races). For query performance and explain compliance, we created compound indexes on `attendance_logs.(date, emp_code)` (supports sorted pagination via IXSCAN), `attendance_logs.(emp_code, punch_in)` (powers reverse-chronological punch-out lookups), `attendance_logs.(date, late_minutes)` (supports late leaderboard and monthly summaries), and `employees.(department, joined_on)` (accelerates cross-collection joins). We considered and rejected an isolated index on `attendance_logs.status`; its low cardinality adds index write overhead without selectivity benefit compared to compound date-prefixed scans.

2. **Punch-in race.**
When two identical punch-in requests arrive simultaneously, both compute the IST attendance date and attempt an `insert_one`. Because MongoDB enforces the compound unique index on `(emp_code, date)`, the database atomically accepts exactly one write (returning 201) and rejects the concurrent duplicate with a `DuplicateKeyError`. Our handler catches `DuplicateKeyError` and returns HTTP 409 Conflict. Application-level check-then-insert was deliberately avoided to eliminate race windows.

3. **Ties.**
The late leaderboard executes MongoDB's `$setWindowFields` stage using standard competition ranking (`$rank`), where ties share a rank and subsequent ranks are skipped (e.g., 1, 2, 2, 4). The `limit` filter (`rank <= limit`) is applied after ranking. If two employees tie at the cutoff rank, both are returned because both satisfy `rank <= limit`. Tied rows are ordered by `emp_code` ascending.

4. **Headcount.**
The department summary aggregation starts from the `employees` collection (matching `joined_on <= last_day_of_month`) rather than `attendance_logs`. It groups by `department` to compute exact headcount (`$sum: 1`) before looking up attendance logs. Consequently, employees with zero logs in the month are accurately included in headcount, while employees joining after the month are excluded.

5. **One thing you would change for 100x data.**
At 100x scale (~10M+ attendance records), computing real-time cross-collection joins and 7-day moving averages on read requests would bottleneck memory and I/O. I would implement an asynchronous daily rollup/materialized view (`department_daily_rollups`) updated incrementally at shift close or via a nightly pipeline, reducing analytical API latency from multi-stage scans to direct single-document lookups.
