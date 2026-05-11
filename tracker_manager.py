#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                    Tracker Guardian v4.1 - 多实例持久化版                             ║
║            Multi-Instance Tracker Guardian System - 赛博朋克版                        ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os

DB_PATH = os.environ.get("GUARDIAN_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardian.db"))

_LANG = ["zh"]

def _(zh: str, en: str = None) -> str:
    if _LANG[0] == "en" and en:
        return en
    return zh

try:
    import sqlite3
except ImportError:
    print(_("\n[错误] 缺少 sqlite3 模块"))
    input(_("\n按 Enter 退出..."))
    sys.exit(1)

try:
    import requests
except ImportError:
    print(_("\n[错误] 缺少 requests 模块，请执行: pip install requests"))
    input(_("\n按 Enter 退出..."))
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
    BLACK = '\033[30m'; RED = '\033[31m'; GREEN = '\033[32m'; YELLOW = '\033[33m'
    BLUE = '\033[34m'; MAGENTA = '\033[35m'; CYAN = '\033[36m'; WHITE = '\033[37m'
    BRIGHT_BLACK = '\033[90m'; BRIGHT_RED = '\033[91m'; BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'; BRIGHT_BLUE = '\033[94m'; BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'; BRIGHT_WHITE = '\033[97m'
    BOLD = '\033[1m'; DIM = '\033[2m'; ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'; RESET = '\033[0m'

class Icon:
    HORIZONTAL = '\u2500'; VERTICAL = '\u2502'
    TL = '\u250c'; TR = '\u2510'; BL = '\u2514'; BR = '\u2518'
    LT = '\u251c'; RT = '\u2524'

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
    "login": "/api/v2/auth/login", "torrents": "/api/v2/torrents/info",
    "trackers": "/api/v2/torrents/trackers", "properties": "/api/v2/torrents/properties",
    "files": "/api/v2/torrents/files", "delete": "/api/v2/torrents/delete",
    "pause": "/api/v2/torrents/pause", "resume": "/api/v2/torrents/resume",
    "reannounce": "/api/v2/torrents/reannounce", "tags": "/api/v2/torrents/tags",
    "add_tags": "/api/v2/torrents/addTags", "remove_tags": "/api/v2/torrents/removeTags",
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
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()
        if not self.get_setting("language"):
            self.set_setting("language", "zh")

    def get_setting(self, key: str, default: str = None) -> str:
        cur = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = cur.fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str):
        with self.write_lock:
            self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()

    def list_instances(self) -> List[Dict]:
        cur = self.conn.execute("SELECT * FROM instances ORDER BY name")
        return [dict(r) for r in cur.fetchall()]

    def get_instance(self, inst_id: int) -> Optional[Dict]:
        cur = self.conn.execute("SELECT * FROM instances WHERE id = ?", (inst_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def save_instance(self, name, host, port, username, password, inst_id=None):
        with self.write_lock:
            if inst_id:
                self.conn.execute(
                    "UPDATE instances SET name=?, host=?, port=?, username=?, password=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (name, host, port, username, password, inst_id))
            else:
                cur = self.conn.execute(
                    "INSERT INTO instances (name, host, port, username, password) VALUES (?, ?, ?, ?, ?)",
                    (name, host, port, username, password))
                inst_id = cur.lastrowid
            self.conn.commit()
        return inst_id

    def delete_instance(self, inst_id: int) -> bool:
        with self.write_lock:
            self.conn.execute("DELETE FROM instances WHERE id = ?", (inst_id,))
            self.conn.commit()
        return True

    def get_torrent(self, instance_id, torrent_hash):
        cur = self.conn.execute("SELECT * FROM torrents WHERE instance_id=? AND hash=?", (instance_id, torrent_hash))
        r = cur.fetchone()
        return dict(r) if r else None

    def upsert_torrent(self, instance_id, torrent_hash, name, status, progress=0, state=None, save_path=None):
        now = datetime.now().isoformat()
        with self.write_lock:
            existing = self.get_torrent(instance_id, torrent_hash)
            if existing:
                self.conn.execute(
                    "UPDATE torrents SET name=?, status=?, progress=?, state=?, save_path=?, last_seen=?, last_checked=? WHERE id=?",
                    (name, status, progress, state, save_path, now, now, existing["id"]))
                tid = existing["id"]
            else:
                cur = self.conn.execute(
                    "INSERT INTO torrents (instance_id, hash, name, status, progress, state, save_path, last_checked) VALUES (?,?,?,?,?,?,?,?)",
                    (instance_id, torrent_hash, name, status, progress, state, save_path, now))
                tid = cur.lastrowid
            self.conn.commit()
        return tid

    def update_torrent_status(self, torrent_id, status, progress=None, state=None, save_path=None):
        now = datetime.now().isoformat()
        fields = ["status=?", "last_checked=?"]
        vals = [status, now]
        if progress is not None:
            fields.append("progress=?"); vals.append(progress)
        if state is not None:
            fields.append("state=?"); vals.append(state)
        if save_path is not None:
            fields.append("save_path=?"); vals.append(save_path)
        vals.append(torrent_id)
        with self.write_lock:
            self.conn.execute(f"UPDATE torrents SET {', '.join(fields)} WHERE id=?", vals)
            self.conn.commit()

    def count_torrents_by_status(self, instance_id=None):
        if instance_id:
            cur = self.conn.execute("SELECT status, COUNT(*) as cnt FROM torrents WHERE instance_id=? GROUP BY status", (instance_id,))
        else:
            cur = self.conn.execute("SELECT status, COUNT(*) as cnt FROM torrents GROUP BY status")
        counts = {"normal": 0, "problematic": 0, "unknown": 0, "deleted": 0}
        for r in cur.fetchall():
            counts[r["status"]] = r["cnt"]
        return counts

    def add_tracker_issue(self, torrent_id, tracker_url, status_code, message):
        with self.write_lock:
            self.conn.execute("INSERT INTO tracker_issues (torrent_id, tracker_url, status_code, message) VALUES (?,?,?,?)",
                              (torrent_id, tracker_url, status_code, message))
            self.conn.commit()

    def close(self):
        self.conn.close()

# ============================================================================
# 动画类
# ============================================================================

class Spinner:
    def __init__(self, message: str = ""):
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
            print(f"\r{self.frames[idx]} {self.message}...", end="", flush=True)
            idx = (idx + 1) % len(self.frames)
            time.sleep(0.1)


class ProgressBar:
    def __init__(self, total: int, width: int = 40, prefix: str = ""):
        self.total = total; self.width = width; self.prefix = prefix
        self.current = 0; self._lock = threading.Lock()

    def update(self, current: int):
        with self._lock:
            self.current = current
            pct = self.current / self.total if self.total > 0 else 0
            filled = int(self.width * pct)
            bar = f"{Colors.CYAN}{BLOCK * filled}{DOTTED * (self.width - filled)}{Colors.RESET}"
            print(f"\r{self.prefix} {bar} {pct*100:5.1f}% [{self.current}/{self.total}]", end="")
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
        self.instance_name = instance.get("name", "?")
        self.connected = False
        self.auth_cookies = {}
        self.request_delay = REQUEST_DELAY
        self.batch_delay = BATCH_DELETE_DELAY
        self.api = API_ENDPOINTS
        self.db = db
        self.stats = {"total": 0, "checked": 0, "skipped": 0, "normal": 0, "problematic": 0, "trackers_checked": 0, "start_time": None}

    def connect(self) -> bool:
        spinner = Spinner(_("正在连接 {0}", "Connecting {0}").format(self.instance_name))
        spinner.start()
        try:
            r = self.session.post(urljoin(self.base_url, self.api["login"]),
                                  data={"username": self.username, "password": self.password})
            spinner.stop()
            if r.status_code == 403:
                print(_("{0}登录凭据错误", "{0}Invalid credentials").format(Colors.RED, Colors.RESET) if _LANG[0]=="zh"
                      else f"{Colors.RED}Invalid credentials for {self.instance_name}{Colors.RESET}")
                return False
            if r.status_code == 200 or "Fails" not in r.text:
                self.connected = True
                self.auth_cookies = self.session.cookies.get_dict()
                print(_("{0}已连接 {1} ({2})", "{0}Connected {1} ({2})").format(Colors.GREEN, self.instance_name, self.base_url) + Colors.RESET)
                return True
            else:
                print(_("{0}{1} 登录失败", "{0}{1} login failed").format(Colors.RED, self.instance_name) + Colors.RESET)
                return False
        except requests.exceptions.ConnectionError:
            spinner.stop()
            loc = _("无法连接 {0}: {1}", "Cannot connect {0}: {1}").format(self.instance_name, self.base_url)
            print(f"{Colors.RED}{loc}{Colors.RESET}")
            return False
        except Exception as e:
            spinner.stop()
            print(f"{Colors.RED}{self.instance_name}: {e}{Colors.RESET}")
            return False

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        for k, v in self.auth_cookies.items():
            s.cookies.set(k, v)
        return s

    def get_torrents(self, filter: str = DEFAULT_FILTER) -> List[Dict]:
        try:
            r = self.session.get(urljoin(self.base_url, self.api["torrents"]), params={"filter": filter})
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    def get_torrent_trackers(self, torrent_hash: str) -> List[Dict]:
        try:
            r = self.session.get(urljoin(self.base_url, self.api["trackers"]), params={"hash": torrent_hash})
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    def get_torrent_properties(self, torrent_hash: str) -> Dict:
        try:
            r = self.session.get(urljoin(self.base_url, self.api["properties"]), params={"hash": torrent_hash})
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    def get_torrent_contents(self, torrent_hash: str) -> List[Dict]:
        try:
            r = self.session.get(urljoin(self.base_url, self.api["files"]), params={"hash": torrent_hash})
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    def delete_torrent(self, torrent_hash, delete_files=False):
        try:
            r = self.session.post(urljoin(self.base_url, self.api["delete"]),
                                  data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"})
            return r.status_code == 200
        except Exception:
            return False

    def pause_torrent(self, torrent_hash):
        try:
            r = self.session.post(urljoin(self.base_url, self.api["pause"]), data={"hashes": torrent_hash})
            return r.status_code == 200
        except Exception:
            return False

    def resume_torrent(self, torrent_hash):
        try:
            r = self.session.post(urljoin(self.base_url, self.api["resume"]), data={"hashes": torrent_hash})
            return r.status_code == 200
        except Exception:
            return False

    def force_reannounce(self, torrent_hash):
        try:
            r = self.session.post(urljoin(self.base_url, self.api["reannounce"]), data={"hashes": torrent_hash})
            return r.status_code == 200
        except Exception:
            return False

    def _check_batch(self, batch: List[Dict]) -> List[Dict]:
        session = self._make_session()
        results = []
        for torrent in batch:
            tname = torrent.get("name", "?")
            thash = torrent.get("hash")
            try:
                r = session.get(urljoin(self.base_url, self.api["trackers"]), params={"hash": thash})
                trackers = r.json() if r.status_code == 200 else []
            except Exception:
                trackers = []
            if not trackers:
                continue

            working = 0; failing = 0; bad = []; real = 0
            for tr in trackers:
                url = tr.get("url", "")
                if url.startswith(("**", "****")):
                    continue
                real += 1
                st = tr.get("status", -1); msg = tr.get("msg", "")
                if st == 2 or st == 4:
                    working += 1
                elif st == 1 or st == 3:
                    pass
                else:
                    failing += 1
                    bad.append({"url": url, "status": st, "message": msg})

            if working > 0 or failing == 0:
                results.append({"hash": thash, "name": tname, "is_problematic": False,
                               "progress": torrent.get("progress", 0) * 100, "state": torrent.get("state", ""),
                               "size": torrent.get("size", 0), "tracker_count": real})
                continue

            try:
                rp = session.get(urljoin(self.base_url, self.api["properties"]), params={"hash": thash})
                props = rp.json() if rp.status_code == 200 else {}
            except Exception:
                props = {}
            try:
                rf = session.get(urljoin(self.base_url, self.api["files"]), params={"hash": thash})
                files = rf.json() if rf.status_code == 200 else []
            except Exception:
                files = []

            results.append({
                "hash": thash, "name": tname, "is_problematic": True,
                "progress": torrent.get("progress", 0) * 100, "state": torrent.get("state", "unknown"),
                "size": torrent.get("size", 0),
                "save_path": props.get("save_path", "?"),
                "working_trackers": working, "total_trackers": real,
                "problematic_trackers": bad,
                "files": [f.get("name", "") for f in files], "tracker_count": real
            })
            time.sleep(self.request_delay)
        return results

    def check_tracker_status(self, torrents=None, force=False):
        if not self.connected:
            print(_("{0}未连接: {1}", "{0}Not connected: {1}").format(Colors.RED, self.instance_name) + Colors.RESET)
            return []
        if torrents is None:
            torrents = self.get_torrents()
        if not torrents:
            print(_("{0} 没有种子", "{0} No torrents").format(self.instance_name))
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
            print(_("全部 {0} 个种子已缓存跳过", "All {0} torrents cached OK").format(self.stats['total']))
            return []

        skipped = self.stats['skipped']
        suffix = _(" ({0} 个跳过)", " ({0} skipped)").format(skipped) if skipped else ""
        print(_("扫描 {0} 个种子 (共 {1} 个) @ {2} ({3} 线程, {4}ms 延迟){5}",
                "Scanning {0} of {1} torrents @ {2} ({3} workers, {4}ms delay){5}").format(
            n, self.stats['total'], self.instance_name, MAX_WORKERS, REQUEST_DELAY * 1000, suffix))

        chunk_size = max(1, n // MAX_WORKERS + 1)
        chunks = [to_check[i:i+chunk_size] for i in range(0, n, chunk_size)]
        problematic = []
        progress = ProgressBar(n, prefix=self.instance_name)

        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            futures = [pool.submit(self._check_batch, c) for c in chunks]
            done = 0
            for future in as_completed(futures):
                for result in future.result():
                    done += 1; progress.update(done)
                    if result is None: continue
                    if result["is_problematic"]:
                        problematic.append(result)
                    if self.db and self.instance_id:
                        tid = self.db.upsert_torrent(
                            self.instance_id, result["hash"], result["name"],
                            "problematic" if result["is_problematic"] else "normal",
                            result["progress"], result.get("state", ""),
                            result.get("save_path") if result["is_problematic"] else None)
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
        parts = [_("结果: {0} 正常", "Result: {0} normal").format(self.stats['normal'])]
        if self.stats["problematic"]:
            parts.append(_("{0} 个问题", "{0} issues").format(self.stats['problematic']))
        else:
            parts.append(_("0 个问题", "0 issues"))
        parts.append(_("({0:.2f}秒, {1:.0f} 个/秒)", "({0:.2f}s, {1:.0f} t/s)").format(elapsed, rate))
        print("  " + ", ".join(parts))

    @staticmethod
    def _fmt_size(size: int) -> str:
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(size) < 1024: return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PiB"

    def print_problematic_torrents(self, problematic: List[Dict]):
        if not problematic:
            return
        print()
        for i, t in enumerate(problematic, 1):
            print(_("  [{0}] {1}", "  [{0}] {1}").format(i, t['name'][:60]))
            print(_("       大小: {0}  进度: {1:.1f}%  状态: {2}",
                    "       Size: {0}  Progress: {1:.1f}%  State: {2}").format(
                self._fmt_size(t['size']), t['progress'], t['state']))
            if t.get("problematic_trackers"):
                for tr in t["problematic_trackers"][:3]:
                    reason = tr.get("message") or f"status={tr['status']}"
                    print(_("       {0}  ({1})", "       {0}  ({1})").format(tr['url'][:65], reason))
            print()

    def batch_delete_torrents(self, hashes: List[str], delete_files=False):
        action = _("删除文件", "delete files") if delete_files else _("保留文件", "keep files")
        print(_("正在删除 {0} 个种子 ({1}) @ {2}", "Deleting {0} torrents ({1}) @ {2}").format(
            len(hashes), action, self.instance_name))
        success = 0
        pb = ProgressBar(len(hashes), prefix=_("删除", "Delete"))
        for i, h in enumerate(hashes, 1):
            pb.update(i)
            if self.delete_torrent(h, delete_files):
                success += 1
                if self.db and self.instance_id:
                    t = self.db.get_torrent(self.instance_id, h)
                    if t: self.db.update_torrent_status(t["id"], "deleted")
            time.sleep(self.batch_delay)
        print(_("  已删除 {0}/{1}", "  Deleted {0}/{1}").format(success, len(hashes)))

# ============================================================================
# 实例管理
# ============================================================================

def manage_instances(db: Database):
    while True:
        instances = db.list_instances()
        print()
        print(_("-- 实例管理 --", "-- Instance Manager --"))
        if instances:
            for inst in instances:
                masked = "*" * len(inst["password"]) if inst["password"] else _("(无)", "(none)")
                print(_("  [{id}] {name}  {host}:{port}  用户:{user}  密码:{pw}",
                        "  [{id}] {name}  {host}:{port}  user:{user}  pass:{pw}").format(
                    id=inst['id'], name=inst['name'], host=inst['host'], port=inst['port'],
                    user=inst['username'], pw=masked))
        else:
            print(_("  (暂无实例)", "  (no instances)"))
        print()
        print(_("  1. 添加实例", "  1. Add instance"))
        print(_("  2. 编辑实例", "  2. Edit instance"))
        print(_("  3. 删除实例", "  3. Delete instance"))
        print(_("  0. 返回", "  0. Back"))
        choice = input(_("\n选择: ", "\nChoice: ")).strip()

        if choice == "0": break
        elif choice == "1":
            name = input(_("  名称: ", "  Name: ")).strip()
            host = input(_("  主机: ", "  Host: ")).strip() or "localhost"
            port = int(input(_("  端口 [8080]: ", "  Port [8080]: ")).strip() or "8080")
            user = input(_("  用户名: ", "  Username: ")).strip()
            pw = getpass.getpass(_("  密码: ", "  Password: "))
            db.save_instance(name, host, port, user, pw)
            print(_("  已添加 '{0}'", "  Added '{0}'").format(name))
        elif choice == "2":
            if not instances: continue
            try: iid = int(input(_("  要编辑的实例 ID: ", "  Instance ID to edit: ")).strip())
            except ValueError: continue
            inst = db.get_instance(iid)
            if not inst: print(_("  未找到", "  Not found")); continue
            name = input(_("  名称 [{0}]: ", "  Name [{0}]: ").format(inst['name'])).strip() or inst["name"]
            host = input(_("  主机 [{0}]: ", "  Host [{0}]: ").format(inst['host'])).strip() or inst["host"]
            port = int(input(_("  端口 [{0}]: ", "  Port [{0}]: ").format(inst['port'])).strip() or str(inst["port"]))
            user = input(_("  用户名 [{0}]: ", "  Username [{0}]: ").format(inst['username'])).strip() or inst["username"]
            pw_input = getpass.getpass(_("  密码 (留空保留): ", "  Password (blank=keep): "))
            pw = pw_input if pw_input else inst["password"]
            db.save_instance(name, host, port, user, pw, inst_id=iid)
            print(_("  已更新", "  Updated"))
        elif choice == "3":
            if not instances: continue
            try: iid = int(input(_("  要删除的实例 ID: ", "  Instance ID to delete: ")).strip())
            except ValueError: continue
            confirm = input(_("  确认删除? 种子记录也将清除 [y/N]: ", "  Confirm? Torrent records deleted too [y/N]: ")).strip().lower()
            if confirm in ("y", "yes"):
                db.delete_instance(iid)
                print(_("  已删除", "  Deleted"))


def choose_instances(db: Database) -> List[Dict]:
    instances = db.list_instances()
    if not instances:
        print(_("没有配置的实例，请先添加", "No instances configured. Add one first."))
        return []
    print(_("\n选择实例:", "\nSelect instances:"))
    for inst in instances:
        print(_("  [{id}] {name} ({host}:{port})", "  [{id}] {name} ({host}:{port})").format(
            id=inst['id'], name=inst['name'], host=inst['host'], port=inst['port']))
    print(_("  [a] 全部", "  [a] All"))
    sel = input(_("\n选择 (ID / a): ", "\nSelect (ID / a): ")).strip().lower()
    if sel == "a": return instances
    try:
        iid = int(sel); inst = db.get_instance(iid)
        return [inst] if inst else []
    except ValueError: return []

# ============================================================================
# 统计
# ============================================================================

def show_dashboard(db: Database):
    instances = db.list_instances()
    if not instances:
        print(_("没有配置实例", "No instances configured"))
        return
    total_torrents = 0
    print()
    print(_("-- 全局统计 --", "-- Global Stats --"))
    for inst in instances:
        counts = db.count_torrents_by_status(inst["id"])
        sub = sum(counts.values()); total_torrents += sub
        print(_("  {name}: {n} 正常, {p} 问题, {u} 未知, {d} 已删除 (共 {t})",
                "  {name}: {n} normal, {p} issues, {u} unknown, {d} deleted (total: {t})").format(
            name=inst['name'], n=counts['normal'], p=counts['problematic'],
            u=counts['unknown'], d=counts['deleted'], t=sub))
    print(_("  数据库: {0}", "  DB: {0}").format(db.db_path))
    print(_("  跟踪种子总数: {0}", "  Total torrents tracked: {0}").format(total_torrents))
    input(_("\n按 Enter 返回...", "\nPress Enter to return..."))


# ============================================================================
# 去重
# ============================================================================

def find_cross_instance_duplicates(db: Database, instances: List[Dict]):
    by_hash: Dict[str, List[Dict]] = {}
    for inst in instances:
        checker = QBittorrentChecker(inst, db)
        if not checker.connect(): continue
        print(_("正在获取 {0} 的种子列表...", "Fetching torrents from {0}...").format(inst['name']))
        for t in checker.get_torrents():
            h = t.get("hash")
            if h: by_hash.setdefault(h, []).append({"inst": inst, "name": t.get("name","?"), "size": t.get("size",0), "hash": h, "checker": checker})

    dups = {h: e for h, e in by_hash.items() if len(e) > 1}
    if not dups:
        print(_("未发现跨实例重复种子", "No cross-instance duplicates found."))
        return

    print(_("\n发现 {0} 个种子在多个实例中存在。\n", "\nFound {0} torrents duplicated across instances.\n").format(len(dups)))
    print(_("实例:", "Instances:"))
    for i, inst in enumerate(instances, 1):
        cnt = sum(1 for e in dups.values() if any(x["inst"]["id"] == inst["id"] for x in e))
        print(_("  [{0}] {1} ({2} 个重复)", "  [{0}] {1} ({2} duplicates)").format(i, inst['name'], cnt))
    print()

    sel = input(_("保留种子在哪个实例? [1]: ", "Keep torrents on which instance? [1]: ")).strip()
    try:
        keep_idx = int(sel) - 1 if sel else 0
        keep_inst = instances[keep_idx]
    except (ValueError, IndexError):
        keep_inst = instances[0]

    deleted = 0; kept = 0
    for h, entries in dups.items():
        for e in entries:
            if e["inst"]["id"] == keep_inst["id"]:
                kept += 1; continue
            if e["checker"].delete_torrent(h, delete_files=False):
                deleted += 1
                t = db.get_torrent(e["inst"]["id"], h) if db else None
                if t: db.update_torrent_status(t["id"], "deleted")

    print(_("在 {0} 保留 {1} 条, 从其他实例删除 {2} 条 (文件已保留)。",
            "Kept {1} on {0}, deleted {2} from others (files retained).").format(keep_inst['name'], kept, deleted))


# ============================================================================
# 主菜单
# ============================================================================

def print_banner():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    h = Icon.HORIZONTAL; v = Icon.VERTICAL; w = 70
    print(f"""
{Colors.BRIGHT_CYAN}{Icon.TL}{h*w}{Icon.TR}{Colors.RESET}
{Colors.BRIGHT_CYAN}{v}{Colors.RESET}  TRACKER GUARDIAN v4.1  -  {_("多实例 Tracker 扫描器", "Multi-Instance Tracker Scanner")}
{Colors.BRIGHT_CYAN}{v}{Colors.RESET}  {now}
{Colors.BRIGHT_CYAN}{Icon.BL}{h*w}{Icon.BR}{Colors.RESET}
""")


def main_menu(db: Database):
    while True:
        instances = db.list_instances()
        inst_count = len(instances)

        print_banner()
        lang_label = _("中文", "English")
        print(f"""
  {Colors.BRIGHT_GREEN}1{Colors.RESET} {_("检查 Tracker (增量)", "Check tracker (incremental)")}
  {Colors.BRIGHT_GREEN}2{Colors.RESET} {_("强制检查 (忽略缓存)", "Force check (ignore cache)")}
  {Colors.BRIGHT_GREEN}3{Colors.RESET} {_("管理实例 ({0})", "Manage instances ({0})").format(inst_count)}
  {Colors.BRIGHT_GREEN}4{Colors.RESET} {_("全局统计", "Global stats")}
  {Colors.BRIGHT_GREEN}5{Colors.RESET} {_("跨实例去重", "Find cross-instance duplicates")}
  {Colors.BRIGHT_GREEN}6{Colors.RESET} {_("语言 / Language ({0})", "Language ({0})").format(lang_label)}
  {Colors.BRIGHT_GREEN}0{Colors.RESET} {_("退出", "Exit")}
""")
        choice = input(_("> 选择 [0-6]: ", "> Choice [0-6]: ")).strip()

        if choice == "0":
            print(_("\n退出。\n", "\nExiting.\n")); break

        elif choice in ("1", "2"):
            selected = choose_instances(db)
            if not selected: continue
            force = (choice == "2")
            for inst in selected:
                hline = Colors.CYAN + "\u2501" * 70 + Colors.RESET
                print(f"\n{hline}")
                checker = QBittorrentChecker(inst, db)
                if not checker.connect(): continue
                problematic = checker.check_tracker_status(checker.get_torrents(), force=force)
                checker.print_problematic_torrents(problematic)

                if problematic:
                    act = input(_("\n  对 [{0}] 的问题种子执行操作? [y/N]: ", "\n  Act on [{0}] problematic torrents? [y/N]: ").format(inst['name'])).strip().lower()
                    if act in ("y", "yes"):
                        print(_("    1. 重新宣布", "    1. Re-announce"))
                        print(_("    2. 删除 (保留文件)", "    2. Delete (keep files)"))
                        print(_("    3. 删除及文件", "    3. Delete with files"))
                        print(_("    4. 暂停", "    4. Pause"))
                        print(_("    5. 恢复", "    5. Resume"))
                        action = input(_("    选择 [1-5]: ", "    Choose [1-5]: ")).strip()
                        if action == "1":
                            for t in problematic: checker.force_reannounce(t["hash"])
                            print(_("  已重新宣布", "  Re-announced"))
                        elif action == "2":
                            checker.batch_delete_torrents([t["hash"] for t in problematic], delete_files=False)
                        elif action == "3":
                            checker.batch_delete_torrents([t["hash"] for t in problematic], delete_files=True)
                        elif action == "4":
                            for t in problematic: checker.pause_torrent(t["hash"])
                            print(_("  已暂停", "  Paused"))
                        elif action == "5":
                            for t in problematic: checker.resume_torrent(t["hash"])
                            print(_("  已恢复", "  Resumed"))
            input(_("\n按 Enter 继续...", "\nPress Enter to continue..."))

        elif choice == "3": manage_instances(db)
        elif choice == "4": show_dashboard(db)
        elif choice == "5":
            instances = db.list_instances()
            if len(instances) < 2:
                print(_("至少需要 2 个实例才能去重", "Need at least 2 instances to find duplicates"))
                continue
            find_cross_instance_duplicates(db, instances)
            input(_("\n按 Enter 继续...", "\nPress Enter to continue..."))
        elif choice == "6":
            cur = db.get_setting("language", "zh")
            new = "en" if cur == "zh" else "zh"
            db.set_setting("language", new)
            _LANG[0] = new
            print(_("语言已切换为 English", "Switched to Chinese") if new == "en" else "Language switched to English")


def main():
    db = Database()
    lang = db.get_setting("language", "zh")
    _LANG[0] = lang
    try:
        main_menu(db)
    except KeyboardInterrupt:
        print(_("\n已中断。", "\nInterrupted."))
    except Exception as e:
        print(_("\n错误: {0}", "\nError: {0}").format(e))
    finally:
        db.close()


if __name__ == "__main__":
    main()
