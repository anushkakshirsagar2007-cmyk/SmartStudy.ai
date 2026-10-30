"""Back up the database and uploaded files into backups/backup-<timestamp>.zip (keeps the newest 14).
Run:  python backup.py      (schedule it daily, e.g. PythonAnywhere > Tasks)"""
import os, shutil, sqlite3, tempfile, zipfile
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
uri = os.environ.get('DATABASE_URL', 'sqlite:///smartstudy.db')
assert uri.startswith('sqlite:///'), 'backup.py only supports SQLite'
name = uri.replace('sqlite:///', '', 1)
cands = [name, os.path.join(BASE, name), os.path.join(BASE, 'instance', name)]
db_path = next((c for c in cands if os.path.isabs(c) and os.path.exists(c)), None) or next((c for c in cands if os.path.exists(c)), None)
if not db_path: raise SystemExit('Database file not found. Looked in: ' + ', '.join(cands))
os.makedirs(os.path.join(BASE, 'backups'), exist_ok=True)
out = os.path.join(BASE, 'backups', f"backup-{datetime.now():%Y%m%d-%H%M%S}.zip")
with tempfile.TemporaryDirectory() as tmp:
    snap = os.path.join(tmp, 'smartstudy.db')
    src, dst = sqlite3.connect(db_path), sqlite3.connect(snap); src.backup(dst); dst.close(); src.close()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(snap, 'smartstudy.db')
        up = os.path.join(BASE, 'uploads')
        for root, _, files in os.walk(up):
            for f in files:
                full = os.path.join(root, f); z.write(full, os.path.relpath(full, BASE))
old = sorted(f for f in os.listdir(os.path.join(BASE, 'backups')) if f.startswith('backup-'))[:-14]
for f in old: os.remove(os.path.join(BASE, 'backups', f))
print('Backup written:', out)
