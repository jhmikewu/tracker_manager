# Tracker Guardian

Multi-instance qBittorrent tracker status checker with SQLite persistence and concurrent scanning.

## Usage

```bash
docker run -it --rm -v tracker_data:/data ghcr.io/jhmikewu/tracker_manager
```

First run: add your qBittorrent instances via menu option **3**.

## What counts as "problematic"?

The tool queries each torrent's trackers via the qBittorrent Web API. Each tracker has a status code:

| Status | Meaning | Classification |
|--------|---------|---------------|
| `2` | Working (contacted successfully) | **Working** |
| `4` | Working (encrypted connection) | **Working** |
| `1` | Not contacted yet | **Ignored** — tracker hasn't been queried yet, usually resolves on next announce |
| `3` | Updating | **Ignored** — transient state |
| `0` | Disabled | **Failing** — tracker is disabled |
| `-1` / other | Error | **Failing** |

### Per-tracker logic

- Trackers with URLs starting with `**` or `****` (DHT, PEX, LSD) are always skipped.
- A tracker is "failing" only if its status is `0`, `-1`, or any unrecognized value.
- Status `1` (not contacted) and `3` (updating) are **ignored** — they do not count as working or failing.

### Per-torrent logic

A torrent is flagged as **problematic** only if **both** conditions are true:

1. **Zero** working trackers (no status 2 or 4)
2. **At least one** genuinely failing tracker (status 0, -1, or error)

If ALL of a torrent's trackers are status 1 (not contacted yet), it is **not** flagged. If at least one tracker is working, it is **not** flagged — the torrent is considered healthy regardless of other failing trackers.

## Menu options

1. **Check tracker (incremental)** — Skips torrents previously marked as normal in the DB
2. **Force check** — Re-checks every torrent regardless of cached status
3. **Manage instances** — Add/edit/delete qBittorrent connection profiles
4. **Global stats** — Summary of tracked torrents across all instances
5. **Find cross-instance duplicates** — Detects torrents with identical hashes across multiple instances and lets you keep only one copy (files are hardlinked, so deletion is safe)

## Volumes

| Path | Purpose |
|------|---------|
| `/data` | SQLite database (instances, torrent history, tracker issues) |

Set `GUARDIAN_DB` env var to customise the database path.
