#!/usr/bin/env python3
"""Create the admin user."""
import sys
sys.path.insert(0, ".")

from app.database import engine, Base, SessionLocal
from app.models import User
from app.auth import hash_password

# Create all tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

email = input("Admin email: ").strip()
password = input("Admin password: ").strip()
name = input("Admin name: ").strip()

existing = db.query(User).filter(User.email == email).first()
if existing:
    print(f"User {email} already exists. Updating to admin.")
    existing.is_admin = True
    db.commit()
else:
    admin = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        default_address="",
        credits=999,
        code_sites_table="code_sites",
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    print(f"Admin user '{name}' created with 999 credits.")

db.close()
