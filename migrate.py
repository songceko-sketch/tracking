"""
Run this once to add new shipment detail columns to the existing database.
Usage: python migrate.py
"""
import sqlite3


def migrate():
    conn = sqlite3.connect("shipments.db")
    new_cols = [
        ("weight_kg", "REAL DEFAULT 0"),
        ("height_cm", "REAL DEFAULT 0"),
        ("width_cm", "REAL DEFAULT 0"),
        ("length_cm", "REAL DEFAULT 0"),
        ("description", 'TEXT DEFAULT ""'),
        ("package_type", 'TEXT DEFAULT ""'),
        ("image_filename", 'TEXT DEFAULT ""'),
        ("sender_name", 'TEXT DEFAULT ""'),
        ("sender_email", 'TEXT DEFAULT ""'),
        ("sender_contact", 'TEXT DEFAULT ""'),
        ("sender_country", 'TEXT DEFAULT ""'),
        ("sender_freight_type", 'TEXT DEFAULT ""'),
        ("sender_date", 'TEXT DEFAULT ""'),
        ("sender_time", 'TEXT DEFAULT ""'),
        ("sender_address", 'TEXT DEFAULT ""'),
        ("sender_pickup_date", 'TEXT DEFAULT ""'),
        ("sender_pickup_time", 'TEXT DEFAULT ""'),
        ("receiver_name", 'TEXT DEFAULT ""'),
        ("receiver_email", 'TEXT DEFAULT ""'),
        ("receiver_contact", 'TEXT DEFAULT ""'),
        ("receiver_country", 'TEXT DEFAULT ""'),
        ("receiver_freight_type", 'TEXT DEFAULT ""'),
        ("receiver_date", 'TEXT DEFAULT ""'),
        ("receiver_time", 'TEXT DEFAULT ""'),
        ("receiver_address", 'TEXT DEFAULT ""'),
    ]

    existing = [row[1] for row in conn.execute("PRAGMA table_info(shipments)").fetchall()]
    for col, typedef in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE shipments ADD COLUMN {col} {typedef}")
            print(f"  ✓ Added column: {col}")
        else:
            print(f"  – Already exists: {col}")

    conn.commit()
    conn.close()
    print("\nMigration complete.")
    print("Columns:", [row[1] for row in sqlite3.connect("shipments.db").execute("PRAGMA table_info(shipments)").fetchall()])


if __name__ == "__main__":
    migrate()
