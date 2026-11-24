"""Poll Velospot/PubliBike availability every minute and log changes.

Two sources:
  - live_stations: the PubliBike app's public station list (velospot.info, no key).
    Live per-station counts by vehicle type -> live_station_events.
  - the official GBFS feed below (updates only every ~15 min; bike IDs are
    re-randomised after each trip, so they don't follow one bike).


Source: sharedmobility.ch (Swiss Federal Office of Energy), open data,
"Open use. Must provide the source."  https://github.com/SFOE/sharedmobility

Every POLL_SECONDS:
  - fetch free_bike_status and station_status
  - if the feed's last_updated moved, archive the raw JSON (gzipped) and write
    only what changed to Postgres:
      bike_events     one row per bike when it appears, changes, or disappears
      station_events  one row per station when its counts/flags change
  - refresh station_information once a day
"""
import gzip
import json
import logging
import os
import signal
import time
import urllib.request
from datetime import datetime, timezone

import psycopg

BASE = "https://sharedmobility.ch/v2/gbfs/velospot"
LIVE_URL = "https://velospot.info/customer/public/api/pbvsng/stations"  # public app endpoint, live, no key
AUTH = os.environ["GBFS_AUTH_EMAIL"]
DSN = os.environ["DATABASE_URL"]
RAW_DIR = os.environ.get("RAW_DIR", "/data/raw")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
INFO_EVERY = 24 * 3600

log = logging.getLogger("poller")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
  id           bigserial PRIMARY KEY,
  feed         text        NOT NULL,
  fetched_at   timestamptz NOT NULL,
  last_updated timestamptz,
  n_items      int,
  changed      boolean     NOT NULL,
  error        text
);
CREATE INDEX IF NOT EXISTS fetches_feed_time ON fetches (feed, fetched_at);

-- kind: 'appear' (first seen / back after being gone), 'change', 'gone' (left the feed, e.g. rented)
CREATE TABLE IF NOT EXISTS bike_events (
  id           bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL,   -- feed last_updated
  fetched_at   timestamptz NOT NULL,
  bike_id      text        NOT NULL,
  kind         text        NOT NULL,
  station_id   text,
  lat          double precision,
  lon          double precision,
  vehicle_type text,
  range_m      double precision,
  is_reserved  boolean,
  is_disabled  boolean
);
CREATE INDEX IF NOT EXISTS bike_events_bike_ts ON bike_events (bike_id, ts);
CREATE INDEX IF NOT EXISTS bike_events_ts ON bike_events (ts);

CREATE TABLE IF NOT EXISTS station_events (
  id                 bigserial PRIMARY KEY,
  ts                 timestamptz NOT NULL,
  fetched_at         timestamptz NOT NULL,
  station_id         text        NOT NULL,
  num_bikes          int,
  num_docks          int,
  types              jsonb,
  is_installed       boolean,
  is_renting         boolean,
  is_returning       boolean,
  last_reported      timestamptz
);
CREATE INDEX IF NOT EXISTS station_events_station_ts ON station_events (station_id, ts);
CREATE INDEX IF NOT EXISTS station_events_ts ON station_events (ts);

-- live per-station counts from the app's public endpoint; a row only when a station changes.
-- vehicle types: 1 bike, 2 e-bike, 3 bike w/ own battery, 4 cargo, 5 e-scooter, 6 ?
CREATE TABLE IF NOT EXISTS live_station_events (
  id             bigserial PRIMARY KEY,
  ts             timestamptz NOT NULL,   -- fetch time (endpoint is live)
  station_id     text        NOT NULL,
  t1 int, t2 int, t3 int, t4 int, t5 int, t6 int,
  out_of_service boolean,
  lat double precision,
  lon double precision
);
CREATE INDEX IF NOT EXISTS live_station_events_station_ts ON live_station_events (station_id, ts);
CREATE INDEX IF NOT EXISTS live_station_events_ts ON live_station_events (ts);

CREATE TABLE IF NOT EXISTS stations (
  station_id text PRIMARY KEY,
  name       text,
  lat        double precision,
  lon        double precision,
  address    text,
  capacity   int,
  raw        jsonb,
  first_seen timestamptz NOT NULL,
  last_seen  timestamptz NOT NULL
);
"""

running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def fetch(feed):
    url = LIVE_URL if feed == "live_stations" else f"{BASE}/{feed}"
    hdrs = {} if feed == "live_stations" else {"Authorization": AUTH}
    req = urllib.request.Request(
        url,
        headers={**hdrs, "User-Agent": "publibike-research-poller/1.0",
                 "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body, json.loads(body)


def ts_of(doc):
    return datetime.fromtimestamp(doc["last_updated"], timezone.utc)


def archive(feed, fetched_at, body):
    d = os.path.join(RAW_DIR, fetched_at.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{fetched_at.strftime('%H%M%S')}_{feed}.json.gz")
    with gzip.open(path, "wb", compresslevel=6) as f:
        f.write(body)


def bike_state(b):
    return (b.get("station_id"), round(b["lat"], 6), round(b["lon"], 6), b.get("vehicle_type_id"),
            b.get("current_range_meters"), b.get("is_reserved"), b.get("is_disabled"))


def station_state(s):
    types = {t["vehicle_type_id"]: t["count"] for t in s.get("vehicle_types_available", [])}
    return (s.get("num_bikes_available"), s.get("num_docks_available"), json.dumps(types, sort_keys=True),
            s.get("is_installed"), s.get("is_renting"), s.get("is_returning"), s.get("last_reported"))


def live_state(s):
    counts = {v["type"]: v["count"] for v in s.get("available_vehicles", [])}
    return (*(counts.get(t) for t in range(1, 7)), s.get("out_of_service"),
            round(s["latitude"], 6), round(s["longitude"], 6))


def load_live_state(conn):
    rows = conn.execute("""
        SELECT DISTINCT ON (station_id) station_id, t1, t2, t3, t4, t5, t6, out_of_service, lat, lon
        FROM live_station_events ORDER BY station_id, ts DESC, id DESC""").fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def handle_live(conn, doc, fetched_at, prev):
    new, rows = {}, []
    for s in doc:
        st = live_state(s)
        new[s["id"]] = st
        if prev.get(s["id"]) != st:
            rows.append((fetched_at, s["id"], *st))
    with conn.cursor() as c:
        c.executemany("""INSERT INTO live_station_events (ts, station_id, t1, t2, t3, t4, t5, t6,
                         out_of_service, lat, lon) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    return new, len(new), len(rows)


def load_bike_state(conn):
    rows = conn.execute("""
        SELECT DISTINCT ON (bike_id) bike_id, kind, station_id, lat, lon, vehicle_type, range_m,
               is_reserved, is_disabled
        FROM bike_events ORDER BY bike_id, ts DESC, id DESC""").fetchall()
    return {r[0]: (r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows if r[1] != "gone"}


def load_station_state(conn):
    rows = conn.execute("""
        SELECT DISTINCT ON (station_id) station_id, num_bikes, num_docks, types::text, is_installed,
               is_renting, is_returning, extract(epoch from last_reported)::bigint
        FROM station_events ORDER BY station_id, ts DESC, id DESC""").fetchall()
    return {r[0]: (r[1], r[2], json.dumps(json.loads(r[3]), sort_keys=True), r[4], r[5], r[6], r[7])
            for r in rows}


def last_updated_of(conn, feed):
    r = conn.execute("SELECT max(last_updated) FROM fetches WHERE feed=%s AND changed", (feed,)).fetchone()
    return r[0]


def handle_bikes(conn, doc, fetched_at, prev):
    ts = ts_of(doc)
    cur = {b["bike_id"]: b for b in doc["data"]["bikes"]}
    rows = []
    for bid, b in cur.items():
        st = bike_state(b)
        old = prev.get(bid)
        if old != st:
            rows.append((ts, fetched_at, bid, "appear" if old is None else "change", *st))
    for bid, old in prev.items():
        if bid not in cur:
            rows.append((ts, fetched_at, bid, "gone", *old))
    with conn.cursor() as c:
        c.executemany("""INSERT INTO bike_events (ts, fetched_at, bike_id, kind, station_id, lat, lon,
                         vehicle_type, range_m, is_reserved, is_disabled)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    return {bid: bike_state(b) for bid, b in cur.items()}, len(cur), len(rows)


def handle_stations(conn, doc, fetched_at, prev):
    ts = ts_of(doc)
    new = {}
    rows = []
    for s in doc["data"]["stations"]:
        st = station_state(s)
        new[s["station_id"]] = st
        if prev.get(s["station_id"]) != st:
            lr = datetime.fromtimestamp(st[6], timezone.utc) if st[6] else None
            rows.append((ts, fetched_at, s["station_id"], st[0], st[1], st[2], st[3], st[4], st[5], lr))
    with conn.cursor() as c:
        c.executemany("""INSERT INTO station_events (ts, fetched_at, station_id, num_bikes, num_docks, types,
                         is_installed, is_renting, is_returning, last_reported)
                         VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)""", rows)
    return new, len(new), len(rows)


def handle_info(conn, doc, fetched_at):
    with conn.cursor() as c:
        c.executemany("""
            INSERT INTO stations (station_id, name, lat, lon, address, capacity, raw, first_seen, last_seen)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (station_id) DO UPDATE SET name=EXCLUDED.name, lat=EXCLUDED.lat, lon=EXCLUDED.lon,
              address=EXCLUDED.address, capacity=EXCLUDED.capacity, raw=EXCLUDED.raw, last_seen=EXCLUDED.last_seen
        """, [(s["station_id"], s.get("name"), s.get("lat"), s.get("lon"), s.get("address"),
               s.get("capacity"), json.dumps(s), fetched_at, fetched_at) for s in doc["data"]["stations"]])
    return len(doc["data"]["stations"])


def main():
    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute(SCHEMA)
    bikes = load_bike_state(conn)
    stations = load_station_state(conn)
    live = load_live_state(conn)
    last = {f: last_updated_of(conn, f) for f in ("free_bike_status", "station_status")}
    last_info = 0.0
    log.info("start: %d bikes, %d stations in state", len(bikes), len(stations))

    while running:
        t0 = time.time()
        if t0 - last_info > INFO_EVERY:
            try:
                body, doc = fetch("station_information")
                fetched_at = datetime.now(timezone.utc)
                archive("station_information", fetched_at, body)
                log.info("station_information: %d stations", handle_info(conn, doc, fetched_at))
                last_info = t0
            except Exception as e:  # noqa: BLE001
                log.warning("station_information failed: %s", e)

        fetched_at = datetime.now(timezone.utc)
        try:
            body, doc = fetch("live_stations")
            with conn.transaction():
                live, n, n_ev = handle_live(conn, doc, fetched_at, live)
                conn.execute("""INSERT INTO fetches (feed, fetched_at, last_updated, n_items, changed)
                                VALUES ('live_stations',%s,%s,%s,%s)""", (fetched_at, fetched_at, n, n_ev > 0))
            archive("live_stations", fetched_at, body)
            log.info("live_stations: %d stations, %d changed", n, n_ev)
        except Exception as e:  # noqa: BLE001
            log.warning("live_stations failed: %s", e)
            try:
                conn.execute("""INSERT INTO fetches (feed, fetched_at, changed, error)
                                VALUES ('live_stations',%s,false,%s)""", (fetched_at, str(e)[:500]))
            except Exception:  # noqa: BLE001
                conn = psycopg.connect(DSN, autocommit=True)

        for feed in ("free_bike_status", "station_status"):
            fetched_at = datetime.now(timezone.utc)
            try:
                body, doc = fetch(feed)
                ts = ts_of(doc)
                changed = last[feed] is None or ts > last[feed]
                n = n_ev = None
                if changed:
                    with conn.transaction():
                        if feed == "free_bike_status":
                            bikes, n, n_ev = handle_bikes(conn, doc, fetched_at, bikes)
                        else:
                            stations, n, n_ev = handle_stations(conn, doc, fetched_at, stations)
                        conn.execute("""INSERT INTO fetches (feed, fetched_at, last_updated, n_items, changed)
                                        VALUES (%s,%s,%s,%s,true)""", (feed, fetched_at, ts, n))
                    archive(feed, fetched_at, body)
                    last[feed] = ts
                    log.info("%s updated %s: %d items, %d events", feed, ts.isoformat(), n, n_ev)
                else:
                    conn.execute("""INSERT INTO fetches (feed, fetched_at, last_updated, changed)
                                    VALUES (%s,%s,%s,false)""", (feed, fetched_at, ts))
            except Exception as e:  # noqa: BLE001
                log.warning("%s failed: %s", feed, e)
                try:
                    conn.execute("""INSERT INTO fetches (feed, fetched_at, changed, error)
                                    VALUES (%s,%s,false,%s)""", (feed, fetched_at, str(e)[:500]))
                except Exception:  # noqa: BLE001
                    conn = psycopg.connect(DSN, autocommit=True)
                    bikes, stations = load_bike_state(conn), load_station_state(conn)

        # sleep to the next full interval
        while running and time.time() < t0 + POLL_SECONDS:
            time.sleep(1)
    log.info("stopped")


if __name__ == "__main__":
    main()
