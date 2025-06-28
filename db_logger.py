import sqlite3
import os
import csv
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sniperx_log.db')

CSV_TABLE_MAP = {
    'sniperx_results_1m.csv': 'sniperx_results_1m',
    'cluster_summaries.csv': 'cluster_summaries',
    'token_risk_analysis.csv': 'token_risk_analysis',
    'trades.csv': 'trades'
}

def _get_headers(csv_file: str) -> list[str]:
    if os.path.exists(csv_file):
        try:
            with open(csv_file, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                return [h.strip() for h in headers if h]
        except Exception:
            pass
    return []

def create_tables():
    """Initialise database tables for logging CSV rows."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for csv_file, table in CSV_TABLE_MAP.items():
        headers = _get_headers(csv_file)
        cols = ', '.join(f'"{h}" TEXT' for h in headers)
        if cols:
            cols += ', '
        cols += '"logged_at" TEXT'
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols});')
    conn.commit()
    conn.close()

def log_to_db(table_name: str, row_dict: dict):
    """Insert a row dictionary into the specified table with a timestamp."""
    if not row_dict:
        return
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row_dict = dict(row_dict)  # copy
    row_dict['logged_at'] = ts

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        cur.execute(f'PRAGMA table_info("{table_name}")')
        existing_cols = {row[1] for row in cur.fetchall()}
        for key in row_dict.keys():
            if key not in existing_cols:
                try:
                    cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{key}" TEXT')
                    existing_cols.add(key)
                except Exception as e:
                    print(f"[DB-ERROR] Failed to add column {key} to {table_name}: {e}")

        columns = list(row_dict.keys())
        placeholders = ', '.join('?' for _ in columns)
        col_sql = ', '.join(f'"{c}"' for c in columns)
        values = [row_dict.get(c) for c in columns]
        cur.execute(f'INSERT INTO "{table_name}" ({col_sql}) VALUES ({placeholders})', values)
        conn.commit()
    except Exception as e:
        print(f"[DB-ERROR] Failed to insert into {table_name}: {e}")
    finally:
        conn.close()
# Initialize tables on import
create_tables()
