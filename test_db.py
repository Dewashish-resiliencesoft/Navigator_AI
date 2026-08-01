import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
print(dict(conn.execute("SELECT * FROM products").fetchone()))
print(dict(conn.execute("SELECT * FROM site_graph_revisions").fetchone()))
