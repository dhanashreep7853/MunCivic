"""
migrate_sqlite_to_postgres.py
==============================
Run this ONCE on your LOCAL machine to copy all data from your
existing SQLite database into your new PostgreSQL database.

STEPS:
1. Make sure your .env file has DATABASE_URL set to your PostgreSQL URL
2. Make sure complaints.db is in the same folder as this script
3. Run: python migrate_sqlite_to_postgres.py

Your SQLite file is NOT deleted or modified — it's read-only during migration.
"""

import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

# ── Verify files and config ────────────────────────────────────────────────────
SQLITE_PATH  = "complaints.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not os.path.exists(SQLITE_PATH):
    print(f"❌ SQLite file not found: {SQLITE_PATH}")
    print("   Make sure complaints.db is in the same folder as this script.")
    exit(1)

if not DATABASE_URL:
    print("❌ DATABASE_URL not set in .env")
    print("   Add: DATABASE_URL=postgresql://user:password@host:5432/dbname")
    exit(1)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

print(f"📂 Reading from SQLite: {SQLITE_PATH}")
print(f"🐘 Writing to PostgreSQL: {DATABASE_URL[:50]}...")
print()

# ── Install psycopg2 and sqlalchemy if needed ─────────────────────────────────
try:
    import psycopg2
    from sqlalchemy import create_engine, text
except ImportError:
    print("Installing required packages...")
    os.system("pip install psycopg2-binary sqlalchemy --quiet")
    import psycopg2
    from sqlalchemy import create_engine, text

pg_engine = create_engine(DATABASE_URL)

# ── Read all data from SQLite ──────────────────────────────────────────────────
sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

print("📖 Reading users from SQLite...")
users = sqlite_conn.execute("SELECT * FROM users").fetchall()
print(f"   Found {len(users)} users")

print("📖 Reading complaints from SQLite...")
complaints = sqlite_conn.execute("SELECT * FROM complaints").fetchall()
print(f"   Found {len(complaints)} complaints")

sqlite_conn.close()

# ── Create tables in PostgreSQL ────────────────────────────────────────────────
print()
print("🏗️  Creating tables in PostgreSQL...")

with pg_engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id       SERIAL PRIMARY KEY,
            name     VARCHAR(200) NOT NULL,
            email    VARCHAR(200) UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role     VARCHAR(20) NOT NULL DEFAULT 'user',
            state    VARCHAR(100) DEFAULT '',
            mobile   VARCHAR(20) DEFAULT '',
            verified INTEGER DEFAULT 0
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS complaints (
            id           SERIAL PRIMARY KEY,
            complaint_id VARCHAR(20) UNIQUE NOT NULL,
            name         VARCHAR(200) NOT NULL,
            mobile       VARCHAR(20) DEFAULT '',
            category     VARCHAR(100) NOT NULL,
            description  TEXT NOT NULL,
            status       VARCHAR(20) NOT NULL DEFAULT 'Pending',
            timestamp    VARCHAR(30) NOT NULL,
            deadline     VARCHAR(30) DEFAULT '',
            image        VARCHAR(300) DEFAULT '',
            image_name   VARCHAR(300) DEFAULT '',
            address      TEXT DEFAULT '',
            latitude     VARCHAR(30) DEFAULT '',
            longitude    VARCHAR(30) DEFAULT '',
            state        VARCHAR(100) NOT NULL DEFAULT '',
            user_id      INTEGER REFERENCES users(id)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS otp_store (
            id         SERIAL PRIMARY KEY,
            email      VARCHAR(200) NOT NULL,
            otp        VARCHAR(10) NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))

    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_complaints_state ON complaints(state)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_complaints_id ON complaints(complaint_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_store(email)"))
    conn.commit()

print("   ✅ Tables created")

# ── Migrate users ──────────────────────────────────────────────────────────────
print()
print("👥 Migrating users...")
migrated_users = skipped_users = 0

with pg_engine.connect() as conn:
    for u in users:
        try:
            conn.execute(text("""
                INSERT INTO users (id, name, email, password, role, state, mobile, verified)
                VALUES (:id, :name, :email, :password, :role, :state, :mobile, :verified)
                ON CONFLICT (email) DO NOTHING
            """), {
                "id":       u["id"],
                "name":     u["name"],
                "email":    u["email"],
                "password": u["password"],
                "role":     u["role"],
                "state":    u["state"] or "",
                "mobile":   u["mobile"] or "",
                "verified": u["verified"] or 0,
            })
            migrated_users += 1
        except Exception as e:
            print(f"   ⚠️  Skipped user {u['email']}: {e}")
            skipped_users += 1
    conn.commit()

print(f"   ✅ {migrated_users} users migrated, {skipped_users} skipped")

# ── Migrate complaints ─────────────────────────────────────────────────────────
print()
print("📋 Migrating complaints...")
migrated_c = skipped_c = 0

with pg_engine.connect() as conn:
    for c in complaints:
        try:
            conn.execute(text("""
                INSERT INTO complaints
                    (id, complaint_id, name, mobile, category, description,
                     status, timestamp, deadline, image, image_name,
                     address, latitude, longitude, state, user_id)
                VALUES
                    (:id, :complaint_id, :name, :mobile, :category, :description,
                     :status, :timestamp, :deadline, :image, :image_name,
                     :address, :latitude, :longitude, :state, :user_id)
                ON CONFLICT (complaint_id) DO NOTHING
            """), {
                "id":           c["id"],
                "complaint_id": c["complaint_id"],
                "name":         c["name"],
                "mobile":       c["mobile"] or "",
                "category":     c["category"],
                "description":  c["description"],
                "status":       c["status"],
                "timestamp":    c["timestamp"],
                "deadline":     c["deadline"] or "",
                "image":        c["image"] or "",
                "image_name":   c["image_name"] or "",
                "address":      c["address"] or "",
                "latitude":     c["latitude"] or "",
                "longitude":    c["longitude"] or "",
                "state":        c["state"] or "",
                "user_id":      c["user_id"] if "user_id" in c.keys() else None,
            })
            migrated_c += 1
        except Exception as e:
            print(f"   ⚠️  Skipped complaint {c['complaint_id']}: {e}")
            skipped_c += 1
    conn.commit()

print(f"   ✅ {migrated_c} complaints migrated, {skipped_c} skipped")

# ── Reset PostgreSQL sequences so auto-increment works correctly ───────────────
print()
print("🔄 Resetting auto-increment sequences...")

with pg_engine.connect() as conn:
    conn.execute(text("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users))"))
    conn.execute(text("SELECT setval('complaints_id_seq', (SELECT MAX(id) FROM complaints))"))
    conn.commit()

print("   ✅ Sequences updated")

# ── Final verification ─────────────────────────────────────────────────────────
print()
print("🔍 Verifying migration...")
with pg_engine.connect() as conn:
    pg_users      = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    pg_complaints = conn.execute(text("SELECT COUNT(*) FROM complaints")).scalar()

print(f"   PostgreSQL now has: {pg_users} users, {pg_complaints} complaints")
print()
print("=" * 50)
print("✅ MIGRATION COMPLETE!")
print()
print("Next steps:")
print("1. Update your .env: set DATABASE_URL to your PostgreSQL URL")
print("2. Deploy your app to Railway/Render")
print("3. Set all environment variables in the deployment dashboard")
print("4. Your app will use PostgreSQL automatically")
print()
print("⚠️  Keep complaints.db as a backup — do not delete it yet.")
print("=" * 50)