#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                    Tracker Guardian v4.0.1 - 多实例持久化版                          ║
║            Multi-Instance Tracker Guardian System - 赛博朋克版                        ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os

DB_PATH = os.environ.get("GUARDIAN_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardian.db"))

try:
    import sqlite3
except ImportError:
    print("\n[ERROR] sqlite3 not found")
    input("\nPress Enter to exit...")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("\n[ERROR] requests not found. Install: pip install requests")
    input("\nPress Enter to exit...")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, SpinnerColumn
    from rich import print as rprint
    from rich.prompt import Prompt, Confirm
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

import json
import time
import threading
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urljoin
from datetime import datetime
from enum import Enum
import getpass
from concurrent.futures import ThreadPoolExecutor, as_completed

BLOCK = chr(0x2588)
DOTTED = chr(0x2591)

class Colors:
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'


class Icon:
    HORIZONTAL = '\u2500'
    VERTICAL = '\u2502'
    TL = '\u250c'
    TR = '\u2510'
    BL = '\u2514'
    BR = '\u2518'
    LT = '\u251c'
    RT = '\u2524'
    CHECK = '\u2714'
    CROSS = '\u2718'
    STAR = '\u2605'
    BOLT = '\u26a1'
    WARNING = '\u26a0'
    GEAR = '\u2699'
    TERMINAL = '\u232a'


def banner_line(width=70):
    return Icon.TL + Icon.HORIZONTAL * width + Icon.TR

def banner_mid(width=70):
    return Icon.LT + Icon.HORIZONTAL * width + Icon.RT

def banner_bot(width=70):
    return Icon.BL + Icon.HORIZONTAL * width + Icon.BR


# ============================================================================
# 全局配置
# ============================================================================

DEFAULT_FILTER = "all"
REQUEST_DELAY = 0.05
BATCH_DELETE_DELAY = 0.2
MAX_WORKERS = 20
ENABLE_AUTO_TAGGING = True
NORMAL_TORRENT_TAG = u"\u2705\u6b63\u5e38"
PROBLEM_TORRENT_TAG = u"\u26a0\ufe0f\u95ee\u9898"
OVERWRITE_TAGS = False
KEEP_HISTORY_TAGS = True

API_ENDPOINTS = {
    "login": "/api/v2/auth/login",
    "torrents": "/api/v2/torrents/info",
    "trackers": "/api/v2/torrents/trackers",
    "properties": "/api/v2/torrents/properties",
    "files": "/api/v2/torrents/files",
    "delete": "/api/v2/torrents/delete",
    "pause": "/api/v2/torrents/pause",
    "resume": "/api/v2/torrents/resume",
    "reannounce": "/api/v2/torrents/reannounce",
    "tags": "/api/v2/torrents/tags",
    "add_tags": "/api/v2/torrents/addTags",
    "remove_tags": "/api/v2/torrents/removeTags",
    "create_tag": "/api/v2/torrents/createTags"
}


# ============================================================================
# SQLite 数据库层
# ============================================================================

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.write_lock = threading.Lock()
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS instances (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                host        TEXT NOT NULL,
                port        INTEGER NOT NULL DEFAULT 8080,
                username    TEXT NOT NULL DEFAULT '',
                password    TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS torrents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL,
                hash        TEXT NOT NULL,
                name        TEXT,
                status      TEXT NOT NULL DEFAULT 'unknown',
                progress    REAL DEFAULT 0,
                state       TEXT,
                save_path   TEXT,
                first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP,
                FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE CASCADE,
                UNIQUE(instance_id, hash)
            );

            CREATE TABLE IF NOT EXISTS tracker_issues (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                torrent_id  INTEGER NOT NULL,
                tracker_url TEXT,
                status_code INTEGER,
                message     TEXT,
                seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (torrent_id) REFERENCES torrents(id) ON DELETE CASCADE
            );
        """)
        self.conn.commit()

    def list_instances(self) -> List[Dict]:
        cur = self.conn.execute("SELECT * FROM instances ORDER BY name")
        return [dict(r) for r in cur.fetchall()]

    def get_instance(self, inst_id: int) -> Optional[Dict]:
        cur = self.conn.execute("SELECT * FROM instances WHERE id = ?", (inst_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def save_instance(self, name: str, host: str, port: int, username: str, password: str, inst_id: int = None) -> int:
        with self.write_lock:
            if inst_id:
                self.conn.execute(
                    "UPDATE instances SET name=?, host=?, port=?, username=?, password=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (name, host, port, username, password, inst_id)
                )
            else:
                cur = self.conn.execute(
                    "INSERT INTO instances (name, host, port, username, password) VALUES (?, ?, ?, ?, ?)",
                    (name, host, port, username, password)
                )
                inst_id = cur.lastrowid
            self.conn.commit()
        return inst_id

    def delete_instance(self, inst_id: int) -> bool:
        with self.write_lock:
            self.conn.execute("DELETE FROM instances WHERE id = ?", (inst_id,))
            self.conn.commit()
        return True

    def get_torrent(self, instance_id: int, torrent_hash: str) -> Optional[Dict]:
        cur = self.conn.execute(
            "SELECT * FROM torrents WHERE instance_id = ? AND hash = ?",
            (instance_id, torrent_hash)
        )
        r = cur.fetchone()
        return dict(r) if r else None

    def upsert_torrent(self, instance_id: int, torrent_hash: str, name: str, status: str,
                       progress: float = 0, state: str = None, save_path: str = None) -> int:
        now = datetime.now().isoformat()
        with self.write_lock:
            existing = self.get_torrent(instance_id, torrent_hash)
            if existing:
                self.conn.execute("""
                    UPDATE torrents SET name=?, status=?, progress=?, state=?, save_path=?,
                        last_seen=?, last_checked=?
                    WHERE id=?
                """, (name, status, progress, state, save_path, now, now, existing["id"]))
                tid = existing["id"]
            else:
                cur = self.conn.execute("""
                    INSERT INTO torrents (instance_id, hash, name, status, progress, state, save_path, last_checked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (instance_id, torrent_hash, name, status, progress, state, save_path, now))
                tid = cur.lastrowid
            self.conn.commit()
        return tid

    def update_torrent_status(self, torrent_id: int, status: str, progress: float = None,
                              state: str = None, save_path: str = None):
        now = datetime.now().isoformat()
        fields = ["status=?", "last_checked=?"]
        vals = [status, now]
        if progress is not None:
            fields.append("progress=?")
            vals.append(progress)
        if state is not None:
            fields.append("state=?")
            vals.append(state)
        if save_path is not None:
            fields.append("save_path=?")
            vals.append(save_path)
        vals.append(torrent_id)
        with self.write_lock:
            self.conn.execute(f"UPDATE torrents SET {', '.join(fields)} WHERE id=?", vals)
            self.conn.commit()

    def count_torrents_by_status(self, instance_id: int = None) -> Dict[str, int]:
        if instance_id:
            cur = self.conn.execute(
                "SELECT status, COUNT(*) as cnt FROM torrents WHERE instance_id=? GROUP BY status",
                (instance_id,)
            )
        else:
            cur = self.conn.execute(
                "SELECT status, COUNT(*) as cnt FROM torrents GROUP BY status"
            )
        counts = {"normal": 0, "problematic": 0, "unknown": 0, "deleted": 0}
        for r in cur.fetchall():
            counts[r["status"]] = r["cnt"]
        return counts

    def add_tracker_issue(self, torrent_id: int, tracker_url: str, status_code: int, message: str):
        with self.write_lock:
            self.conn.execute(
                "INSERT INTO tracker_issues (torrent_id, tracker_url, status_code, message) VALUES (?, ?, ?, ?)",
                (torrent_id, tracker_url, status_code, message)
            )
            self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================================
# 动画类
# ============================================================================

class Spinner:
    def __init__(self, message: str = "Processing"):
        self.message = message
        self.spinning = False
        self.thread = None
        self.frames = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u280f", "\u280f"]

    def start(self):
        self.spinning = True
        self.thread = threading.Thread(target=self._spin)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.spinning = False
        if self.thread:
            self.thread.join()
        print("\r\033[K", end="", flush=True)

    def _spin(self):
        idx = 0
        while self.spinning:
            print(f"\r{Colors.CYAN}{self.frames[idx]} {self.message}...{Colors.RESET}", end="", flush=True)
            idx = (idx + 1) % len(self.frames)
            time.sleep(0.1)


class ProgressBar:
    def __init__(self, total: int, width: int = 40, prefix: str = ""):
        self.total = total
        self.width = width
        self.prefix = prefix
        self.current = 0
        self._lock = threading.Lock()

    def update(self, current: int):
        with self._lock:
            self.current = current
            percent = self.current / self.total if self.total > 0 else 0
            filled = int(self.width * percent)
            bar = f"{Colors.CYAN}{BLOCK * filled}{DOTTED * (self.width - filled)}{Colors.RESET}"
            print(f"\r{self.prefix} {bar} {percent*100:5.1f}% [{self.current}/{self.total}]", end="")
            if self.current == self.total:
                print()




# ============================================================================
# 核心 qBittorrent 检查器
# ============================================================================

class QBittorrentChecker:
    def __init__(self, instance: Dict, db: Database = None):
        self.base_url = f"http://{instance['host']}:{instance['port']}"
        self.session = requests.Session()
        self.username = instance.get("username", "")
        self.password = instance.get("password", "")
        self.instance_id = instance.get("id")
        self.instance_name = instance.get("name", "Unknown")
        self.connected = False
        self.auth_cookies = {}
        self.request_delay = REQUEST_DELAY
        self.batch_delay = BATCH_DELETE_DELAY
        self.api = API_ENDPOINTS
        self.db = db
        self.stats = {"total": 0, "checked": 0, "skipped": 0, "normal": 0, "problematic": 0, "trackers_checked": 0, "start_time": None}

    def connect(self) -> bool:
        spinner = Spinner(f"Connecting {self.instance_name} ({self.base_url})")
        spinner.start()
        try:
            login_url = urljoin(self.base_url, self.api["login"])
            login_data = {"username": self.username, "password": self.password}
            response = self.session.post(login_url, data=login_data)
            spinner.stop()
            if response.status_code == 403:
                print(f"{Colors.RED}Invalid credentials for {self.instance_name}{Colors.RESET}")
                return False
            if response.status_code == 200 or "Fails" not in response.text:
                self.connected = True
                self.auth_cookies = self.session.cookies.get_dict()
                print(f"{Colors.GREEN}Connected to {self.instance_name} ({self.base_url}){Colors.RESET}")
                return True
            else:
                print(f"{Colors.RED}Login failed for {self.instance_name}{Colors.RESET}")
                return False
        except requests.exceptions.ConnectionError:
            spinner.stop()
            print(f"{Colors.RED}Cannot connect to {self.instance_name}: {self.base_url}{Colors.RESET}")
            return False
        except Exception as e:
            spinner.stop()
            print(f"{Colors.RED}Connection error for {self.instance_name}: {e}{Colors.RESET}")
            return False

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        for k, v in self.auth_cookies.items():
            s.cookies.set(k, v)
        return s

    def get_torrents(self, filter: str = DEFAULT_FILTER) -> List[Dict]:
        try:
            url = urljoin(self.base_url, self.api["torrents"])
            response = self.session.get(url, params={"filter": filter})
            return response.json() if response.status_code == 200 else []
        except Exception:
            return []

    def get_torrent_trackers(self, torrent_hash: str) -> List[Dict]:
        try:
            url = urljoin(self.base_url, self.api["trackers"])
            response = self.session.get(url, params={"hash": torrent_hash})
            return response.json() if response.status_code == 200 else []
        except Exception:
            return []

    def get_torrent_properties(self, torrent_hash: str) -> Dict:
        try:
            url = urljoin(self.base_url, self.api["properties"])
            response = self.session.get(url, params={"hash": torrent_hash})
            return response.json() if response.status_code == 200 else {}
        except Exception:
            return {}

    def get_torrent_contents(self, torrent_hash: str) -> List[Dict]:
        try:
            url = urljoin(self.base_url, self.api["files"])
            response = self.session.get(url, params={"hash": torrent_hash})
            return response.json() if response.status_code == 200 else []
        except Exception:
            return []

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        try:
            url = urljoin(self.base_url, self.api["delete"])
            response = self.session.post(url, data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"})
            return response.status_code == 200
        except Exception:
            return False

    def pause_torrent(self, torrent_hash: str) -> bool:
        try:
            url = urljoin(self.base_url, self.api["pause"])
            response = self.session.post(url, data={"hashes": torrent_hash})
            return response.status_code == 200
        except Exception:
            return False

    def resume_torrent(self, torrent_hash: str) -> bool:
        try:
            url = urljoin(self.base_url, self.api["resume"])
            response = self.session.post(url, data={"hashes": torrent_hash})
            return response.status_code == 200
        except Exception:
            return False

    def force_reannounce(self, torrent_hash: str) -> bool:
        try:
            url = urljoin(self.base_url, self.api["reannounce"])
            response = self.session.post(url, data={"hashes": torrent_hash})
            return response.status_code == 200
        except Exception:
            return False

    def _check_batch(self, batch: List[Dict]) -> List[Dict]:
        session = self._make_session()
        results = []
        for torrent in batch:
            torrent_name = torrent.get("name", "Unknown")
            torrent_hash = torrent.get("hash")

            try:
                url = urljoin(self.base_url, self.api["trackers"])
                r = session.get(url, params={"hash": torrent_hash})
                trackers = r.json() if r.status_code == 200 else []
            except Exception:
                trackers = []

            if not trackers:
                continue

            working = 0
            failing = 0
            problematic_trackers = []
            real_total = 0

            for tracker in trackers:
                url = tracker.get("url", "")
                if url.startswith(("**", "****")):
                    continue
                real_total += 1
                status = tracker.get("status", -1)
                msg = tracker.get("msg", "")

                if status == 2 or status == 4:
                    working += 1
                elif status == 1 or status == 3:
                    pass
                else:
                    failing += 1
                    problematic_trackers.append({"url": url, "status": status, "message": msg})

            if working > 0 or failing == 0:
                results.append({"hash": torrent_hash, "name": torrent_name, "is_problematic": False,
                               "progress": torrent.get("progress", 0) * 100, "state": torrent.get("state", ""),
                               "size": torrent.get("size", 0), "tracker_count": real_total})
                continue

            try:
                pu = urljoin(self.base_url, self.api["properties"])
                rp = session.get(pu, params={"hash": torrent_hash})
                properties = rp.json() if rp.status_code == 200 else {}
            except Exception:
                properties = {}
            try:
                fu = urljoin(self.base_url, self.api["files"])
                rf = session.get(fu, params={"hash": torrent_hash})
                files = rf.json() if rf.status_code == 200 else []
            except Exception:
                files = []

            results.append({
                "hash": torrent_hash, "name": torrent_name, "is_problematic": True,
                "progress": torrent.get("progress", 0) * 100, "state": torrent.get("state", "unknown"),
                "size": torrent.get("size", 0),
                "save_path": properties.get("save_path", "Unknown"),
                "working_trackers": working, "total_trackers": real_total,
                "problematic_trackers": problematic_trackers,
                "files": [f.get("name", "") for f in files], "tracker_count": real_total
            })
            time.sleep(self.request_delay)
        return results

    def check_tracker_status(self, torrents: List[Dict] = None, force: bool = False) -> List[Dict]:
        if not self.connected:
            print(f"{Colors.RED}Not connected: {self.instance_name}{Colors.RESET}")
            return []

        if torrents is None:
            torrents = self.get_torrents()

        if not torrents:
            print(f"No torrents found on {self.instance_name}")
            return []

        self.stats["total"] = len(torrents)
        self.stats["start_time"] = datetime.now()

        to_check = []
        for t in torrents:
            if not force and self.db and self.instance_id:
                existing = self.db.get_torrent(self.instance_id, t["hash"])
                if existing and existing["status"] == "normal":
                    self.stats["skipped"] += 1
                    self.stats["normal"] += 1
                    continue
            to_check.append(t)

        n = len(to_check)
        if n == 0:
            print(f"All {self.stats['total']} torrents on {self.instance_name} skipped (cached OK)")
            return []

        skipped = self.stats['skipped']
        print(f"Scanning {n} of {self.stats['total']} torrents on {self.instance_name} ({MAX_WORKERS} workers, {REQUEST_DELAY*1000:.0f}ms delay)" + (f", {skipped} skipped" if skipped else ""))

        chunk_size = max(1, n // MAX_WORKERS + 1)
        chunks = [to_check[i:i+chunk_size] for i in range(0, n, chunk_size)]

        problematic = []
        progress = ProgressBar(n, prefix=f"{self.instance_name}")

        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            futures = [pool.submit(self._check_batch, c) for c in chunks]
            done = 0
            for future in as_completed(futures):
                for result in future.result():
                    done += 1
                    progress.update(done)
                    if result is None:
                        continue
                    if result["is_problematic"]:
                        problematic.append(result)
                    if self.db and self.instance_id:
                        tid = self.db.upsert_torrent(
                            self.instance_id, result["hash"], result["name"],
                            "problematic" if result["is_problematic"] else "normal",
                            result["progress"], result.get("state", ""),
                            result.get("save_path") if result["is_problematic"] else None
                        )
                        if result["is_problematic"] and result.get("problematic_trackers"):
                            for tr in result["problematic_trackers"]:
                                self.db.add_tracker_issue(tid, tr["url"], tr["status"], tr.get("message", ""))
                    self.stats["checked"] += 1
                    self.stats["trackers_checked"] += result.get("tracker_count", 0)
                    if result["is_problematic"]:
                        self.stats["problematic"] += 1
                    else:
                        self.stats["normal"] += 1

        self._print_summary()
        return problematic

    def _print_summary(self):
        elapsed = (datetime.now() - self.stats["start_time"]).total_seconds()
        rate = self.stats["checked"] / elapsed if elapsed > 0 else 0
        parts = [f"Result: {self.stats['normal']} normal"]
        if self.stats["problematic"]:
            parts.append(f"{self.stats['problematic']} issues")
        else:
            parts.append("0 issues")
        parts.append(f"({elapsed:.2f}s, {rate:.0f} t/s)")
        print("  " + ", ".join(parts))

    @staticmethod
    def _fmt_size(size: int) -> str:
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(size) < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PiB"

    def print_problematic_torrents(self, problematic: List[Dict]):
        if not problematic:
            return
        p = len(problematic)
        print(f"")
        for i, t in enumerate(problematic, 1):
            print(f"  [{i}] {t['name'][:60]}")
            print(f"       Size: {self._fmt_size(t['size'])}  Progress: {t['progress']:.1f}%  State: {t['state']}")
            if t.get("problematic_trackers"):
                for tr in t["problematic_trackers"][:3]:
                    reason = tr.get("message") or f"status={tr['status']}"
                    print(f"       {tr['url'][:65]}  ({reason})")
            print()

    def batch_delete_torrents(self, hashes: List[str], delete_files: bool = False):
        action = "with files" if delete_files else "keep files"
        print(f"Deleting {len(hashes)} torrents ({action}) on {self.instance_name}")
        success = 0
        pb = ProgressBar(len(hashes), prefix=f"Delete")
        for i, h in enumerate(hashes, 1):
            pb.update(i)
            if self.delete_torrent(h, delete_files):
                success += 1
                if self.db and self.instance_id:
                    t = self.db.get_torrent(self.instance_id, h)
                    if t:
                        self.db.update_torrent_status(t["id"], "deleted")
            time.sleep(self.batch_delay)
        print(f"  Deleted {success}/{len(hashes)}")


# ============================================================================
# 实例管理
# ============================================================================

def manage_instances(db: Database):
    while True:
        instances = db.list_instances()
        print()
        print("-- Instance Manager --")
        if instances:
            for inst in instances:
                masked = "*" * len(inst["password"]) if inst["password"] else "(none)"
                print(f"  [{inst['id']}] {inst['name']}  {inst['host']}:{inst['port']}  user:{inst['username']}  pass:{masked}")
        else:
            print("  (no instances)")
        print()
        print("  1. Add instance")
        print("  2. Edit instance")
        print("  3. Delete instance")
        print("  0. Back")
        choice = input("\nChoice: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            name = input("  Name: ").strip()
            host = input("  Host: ").strip() or "localhost"
            port = int(input(f"  Port [8080]: ").strip() or "8080")
            user = input("  Username: ").strip()
            pw = getpass.getpass("  Password: ")
            db.save_instance(name, host, port, user, pw)
            print(f"  Added '{name}'")
        elif choice == "2":
            if not instances:
                continue
            try:
                iid = int(input("  Instance ID to edit: ").strip())
            except ValueError:
                continue
            inst = db.get_instance(iid)
            if not inst:
                print("  Not found")
                continue
            name = input(f"  Name [{inst['name']}]: ").strip() or inst["name"]
            host = input(f"  Host [{inst['host']}]: ").strip() or inst["host"]
            port = int(input(f"  Port [{inst['port']}]: ").strip() or str(inst["port"]))
            user = input(f"  Username [{inst['username']}]: ").strip() or inst["username"]
            pw_input = getpass.getpass("  Password (blank = keep): ")
            pw = pw_input if pw_input else inst["password"]
            db.save_instance(name, host, port, user, pw, inst_id=iid)
            print("  Updated")
        elif choice == "3":
            if not instances:
                continue
            try:
                iid = int(input("  Instance ID to delete: ").strip())
            except ValueError:
                continue
            confirm = input("  Confirm? Torrent records will also be deleted [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                db.delete_instance(iid)
                print("  Deleted")


def choose_instances(db: Database) -> List[Dict]:
    instances = db.list_instances()
    if not instances:
        print("No instances configured. Add one first.")
        return []
    print("\nSelect instances:")
    for inst in instances:
        print(f"  [{inst['id']}] {inst['name']} ({inst['host']}:{inst['port']})")
    print("  [a] All")
    sel = input("\nSelect (ID / a): ").strip().lower()
    if sel == "a":
        return instances
    try:
        iid = int(sel)
        inst = db.get_instance(iid)
        return [inst] if inst else []
    except ValueError:
        return []


# ============================================================================
# 统计 / 历史
# ============================================================================

def show_dashboard(db: Database):
    instances = db.list_instances()
    if not instances:
        print("No instances configured")
        return
    total_torrents = 0
    print()
    print("-- Global Stats --")
    for inst in instances:
        counts = db.count_torrents_by_status(inst["id"])
        sub = sum(counts.values())
        total_torrents += sub
        print(f"  {inst['name']}: {counts['normal']} normal, {counts['problematic']} issues, {counts['unknown']} unknown, {counts['deleted']} deleted (total: {sub})")
    print(f"  DB: {db.db_path}")
    print(f"  Total torrents tracked: {total_torrents}")
    input("\nPress Enter to return...")


# ============================================================================
# 主菜单
# ============================================================================

def print_banner():
    h = Icon.HORIZONTAL
    v = Icon.VERTICAL
    w = 70
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ts = f"{now}"
    print(f"""
{Colors.BRIGHT_CYAN}{Icon.TL}{h*w}{Icon.TR}{Colors.RESET}
{Colors.BRIGHT_CYAN}{v}{Colors.RESET}  TRACKER GUARDIAN v4.0.1  -  Multi-Instance Tracker Scanner
{Colors.BRIGHT_CYAN}{v}{Colors.RESET}  {ts}
{Colors.BRIGHT_CYAN}{Icon.BL}{h*w}{Icon.BR}{Colors.RESET}
""")


def main_menu(db: Database):
    while True:
        instances = db.list_instances()
        inst_count = len(instances)
        h = Icon.HORIZONTAL
        v = Icon.VERTICAL
        w = 50

        print_banner()
        menu = f"""
  {Colors.BRIGHT_GREEN}1{Colors.RESET} Check tracker (incremental)
  {Colors.BRIGHT_GREEN}2{Colors.RESET} Force check (ignore cache)
  {Colors.BRIGHT_GREEN}3{Colors.RESET} Manage instances ({inst_count})
  {Colors.BRIGHT_GREEN}4{Colors.RESET} Global stats
  {Colors.BRIGHT_GREEN}0{Colors.RESET} Exit
"""
        print(menu)
        choice = input(f"> Choice [0-4]: ").strip()

        if choice == "0":
            print(f"\nExiting.\n")
            break

        elif choice in ("1", "2"):
            selected = choose_instances(db)
            if not selected:
                continue
            force = (choice == "2")
            for inst in selected:
                hline = Colors.CYAN + "\u2501" * 70 + Colors.RESET
                print(f"\n{hline}")
                checker = QBittorrentChecker(inst, db)
                if not checker.connect():
                    continue
                torrents = checker.get_torrents()
                problematic = checker.check_tracker_status(torrents, force=force)
                checker.print_problematic_torrents(problematic)

                if problematic and input(f"\n  Act on [{inst['name']}] problematic torrents? [y/N]: ").strip().lower() in ("y", "yes"):
                    print(f"    1. Re-announce")
                    print(f"    2. Delete (keep files)")
                    print(f"    3. Delete with files")
                    print(f"    4. Pause")
                    print(f"    5. Resume")
                    action = input(f"    Choose [1-5]: ").strip()
                    if action == "1":
                        for t in problematic:
                            checker.force_reannounce(t["hash"])
                        print(f"  Re-announced")
                    elif action == "2":
                        checker.batch_delete_torrents([t["hash"] for t in problematic], delete_files=False)
                    elif action == "3":
                        checker.batch_delete_torrents([t["hash"] for t in problematic], delete_files=True)
                    elif action == "4":
                        for t in problematic:
                            checker.pause_torrent(t["hash"])
                        print(f"  Paused")
                    elif action == "5":
                        for t in problematic:
                            checker.resume_torrent(t["hash"])
                        print(f"  Resumed")
            input(f"\nPress Enter to continue...")

        elif choice == "3":
            manage_instances(db)

        elif choice == "4":
            show_dashboard(db)


def main():
    db = Database()
    try:
        main_menu(db)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
