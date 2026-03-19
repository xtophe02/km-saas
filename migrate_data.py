#!/usr/bin/env python3
"""Migrate distance cache and code_sites from SQLite (CT 103 dump) to PostgreSQL."""
import sys
import sqlite3
sys.path.insert(0, ".")

from app.database import engine, Base, SessionLocal
from app.models import Distance, CodeSite
from sqlalchemy import text

# Create all tables
Base.metadata.create_all(bind=engine)

SQLITE_PATH = "/tmp/office_locations.db"

print(f"Opening SQLite database: {SQLITE_PATH}")
sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

db = SessionLocal()

# 1. Migrate distances
print("Migrating distances...")
cursor = sqlite_conn.execute("SELECT origin, destination, distance FROM distances")
rows = cursor.fetchall()
print(f"  Found {len(rows)} distance records")

batch = []
for row in rows:
    batch.append({
        "origin": row["origin"],
        "destination": row["destination"],
        "distance_km": float(row["distance"]),
    })

if batch:
    # Use raw SQL for bulk insert with ON CONFLICT
    for i in range(0, len(batch), 500):
        chunk = batch[i:i+500]
        for rec in chunk:
            db.execute(
                text("""
                    INSERT INTO distances (origin, destination, distance_km)
                    VALUES (:origin, :destination, :distance_km)
                    ON CONFLICT (origin, destination) DO NOTHING
                """),
                rec,
            )
        db.commit()
        print(f"  Inserted {min(i+500, len(batch))}/{len(batch)}")

print(f"  Distances migrated: {len(batch)}")

# 2. Migrate code_sites tables
print("\nMigrating code sites...")
# Get all tables that aren't system tables
tables_cursor = sqlite_conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT IN ('distances', 'trips', 'offices', 'sqlite_sequence')"
)
tables = [row["name"] for row in tables_cursor.fetchall()]
print(f"  Found tables: {tables}")

total_sites = 0
for table_name in tables:
    try:
        rows = sqlite_conn.execute(f"SELECT code, address FROM [{table_name}]").fetchall()
        for row in rows:
            db.add(CodeSite(
                table_name=table_name,
                code=row["code"],
                address=row["address"],
            ))
            total_sites += 1
        db.commit()
        print(f"  {table_name}: {len(rows)} sites")
    except Exception as e:
        print(f"  {table_name}: SKIPPED ({e})")
        db.rollback()

print(f"\nTotal sites migrated: {total_sites}")
db.close()
sqlite_conn.close()
print("Migration complete!")
