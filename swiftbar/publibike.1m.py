#!/usr/bin/python3
# <xbar.title>PubliBike Favourites</xbar.title>
# <xbar.desc>Live e-bike / bike counts at your favourite PubliBike-Velospot stations.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisappearPlugin>true</swiftbar.hideDisappearPlugin>
"""SwiftBar plugin: live PubliBike counts for favourite stations.

Live counts:   https://velospot.info/customer/public/api/pbvsng/stations (public, no key)
Station names: GBFS station_information (MobiData BW mirror of the Velospot feed), cached daily.

Usage (SwiftBar calls these from menu items):
  publibike.1m.py                 render the menu
  publibike.1m.py add             search dialog -> pick stations to add
  publibike.1m.py remove <id>     remove a favourite
  publibike.1m.py top <id>        move a favourite to the top (shown in the menu bar)
"""
import json
import os
import subprocess
import sys
import time
import unicodedata
import urllib.request

STATIONS_URL = "https://velospot.info/customer/public/api/pbvsng/stations"
INFO_URL = "https://api.mobidata-bw.de/sharing/gbfs/v3/velospot_ch/station_information"

CONFIG_DIR = os.path.expanduser("~/.config/publibike")
FAVS_FILE = os.path.join(CONFIG_DIR, "favorites.json")
CACHE_DIR = os.path.expanduser("~/Library/Caches/publibike-swiftbar")
NAMES_CACHE = os.path.join(CACHE_DIR, "station_names.json")
LIVE_CACHE = os.path.join(CACHE_DIR, "stations_live.json")
NAMES_MAX_AGE = 24 * 3600

# App API vehicle types (checked against GBFS): 1 bike, 2 e-bike,
# 3 bike with own battery, 4 cargo, 5 e-scooter.
ELECTRIC_TYPES = {2}
MECHANICAL_TYPES = {1, 3}

SELF = os.path.abspath(__file__)


# ---------- storage ----------

def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_favs():
    return [str(x) for x in _read_json(FAVS_FILE, [])]


def save_favs(favs):
    _write_json(FAVS_FILE, favs)


# ---------- network ----------

def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "publibike-swiftbar/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _text(v):
    """GBFS v3 localised strings are [{"text":..,"language":..}]."""
    if isinstance(v, list):
        return v[0].get("text", "") if v else ""
    return v or ""


def station_names(force=False):
    """id -> {name, address, lat, lon}; refreshed at most once a day."""
    fresh = os.path.exists(NAMES_CACHE) and time.time() - os.path.getmtime(NAMES_CACHE) < NAMES_MAX_AGE
    if fresh and not force:
        return _read_json(NAMES_CACHE, {})
    try:
        data = _get_json(INFO_URL, timeout=30)
        names = {}
        for s in data["data"]["stations"]:
            sid = str(s["station_id"]).split(":")[-1]
            names[sid] = {
                "name": _text(s.get("name")),
                "address": s.get("address", ""),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
            }
        _write_json(NAMES_CACHE, names)
        return names
    except Exception:
        return _read_json(NAMES_CACHE, {})  # stale is better than nothing


def live_stations():
    """(id -> station dict, error or None). Falls back to last good response."""
    try:
        data = _get_json(STATIONS_URL)
        _write_json(LIVE_CACHE, data)
        err = None
    except Exception as e:  # noqa: BLE001
        data = _read_json(LIVE_CACHE, [])
        err = str(e) or e.__class__.__name__
    return {str(s["id"]): s for s in data}, err


def counts(station):
    e = m = 0
    for v in (station or {}).get("available_vehicles", []):
        if v.get("type") in ELECTRIC_TYPES:
            e += v.get("count", 0)
        elif v.get("type") in MECHANICAL_TYPES:
            m += v.get("count", 0)
    return e, m


# ---------- menu ----------

def esc(s):
    """Keep SwiftBar from parsing '|' in station names as parameters."""
    return str(s).replace("|", "¦")


def label(sid, names):
    info = names.get(sid)
    return info["name"] if info and info.get("name") else "Station " + sid


def render():
    favs = load_favs()
    names = station_names()
    live, err = live_stations()
    cmd = 'bash="{}"'.format(SELF)

    # Menu bar: first favourite
    if favs:
        e, m = counts(live.get(favs[0]))
        print("⚡{} 🚲{}{} | sfimage=bicycle".format(e, m, " ⚠" if err else ""))
    else:
        print("| sfimage=bicycle")
    print("---")

    if err:
        print("Could not refresh: {} | color=#d9534f".format(esc(err)[:80]))
        cache_age = time.time() - os.path.getmtime(LIVE_CACHE) if os.path.exists(LIVE_CACHE) else None
        if cache_age is not None:
            print("Showing data from {} min ago | size=11".format(int(cache_age // 60)))
        print("---")

    if not favs:
        print("No favourite stations yet")
    else:
        width = max(len(label(s, names)) for s in favs)
        for i, sid in enumerate(favs):
            st = live.get(sid)
            name = label(sid, names)
            if st is None:
                line = "{}   (not found)".format(name)
            else:
                e, m = counts(st)
                line = "{}   ⚡{:>2}  🚲{:>2}".format(name.ljust(width), e, m)
                if st.get("out_of_service"):
                    line += "  (out of service)"
            print("{} | font=Menlo size=13 trim=false".format(esc(line)))
            info = names.get(sid) or {}
            if info.get("address"):
                print("--{} | size=11".format(esc(info["address"])))
            if info.get("lat") is not None:
                print("--Show in Maps | href=https://maps.apple.com/?q={}&ll={},{}".format(
                    urllib.request.quote(name), info["lat"], info["lon"]))
            if i > 0:
                print("--Show in menu bar | {} param1=top param2={} terminal=false refresh=true".format(cmd, sid))
            print("--Remove | {} param1=remove param2={} terminal=false refresh=true color=#d9534f".format(cmd, sid))

    print("---")
    print("Add station… | {} param1=add terminal=false refresh=true sfimage=plus".format(cmd))
    print("Refresh | refresh=true sfimage=arrow.clockwise")
    print("Updated {} | size=11".format(time.strftime("%H:%M")))


# ---------- dialogs ----------

def osa(script, *args):
    """Run AppleScript with argv; returns stdout or None if cancelled."""
    p = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return p.stdout.rstrip("\n")


ASK_SCRIPT = '''
on run argv
  activate
  set r to display dialog (item 1 of argv) default answer (item 2 of argv) with title "PubliBike" buttons {"Cancel", "Search"} default button "Search"
  return text returned of r
end run
'''

CHOOSE_SCRIPT = '''
on run argv
  activate
  set prompt to item 1 of argv
  set opts to rest of argv
  set picked to choose from list opts with title "PubliBike" with prompt prompt OK button name "Add" with multiple selections allowed
  if picked is false then error number -128
  set AppleScript's text item delimiters to linefeed
  return picked as text
end run
'''

ALERT_SCRIPT = '''
on run argv
  activate
  display alert (item 1 of argv) message (item 2 of argv)
end run
'''


def fold(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def add():
    names = station_names()
    if not names:
        names = station_names(force=True)
    live, _ = live_stations()
    favs = load_favs()
    candidates = [sid for sid in live if sid not in favs]

    query = ""
    while True:
        query = osa(ASK_SCRIPT, "Search for a station (name, street or town).\nLeave empty to list all.", query)
        if query is None:
            return
        terms = fold(query).split()
        hits = []
        for sid in candidates:
            info = names.get(sid, {})
            hay = fold("{} {} {}".format(info.get("name", ""), info.get("address", ""), sid))
            if all(t in hay for t in terms):
                hits.append(sid)
        if hits:
            break
        osa(ALERT_SCRIPT, "No stations found", 'Nothing matches "{}". Try another search.'.format(query))

    hits.sort(key=lambda s: fold(label(s, names)))
    options, by_option = [], {}
    for sid in hits[:300]:
        e, m = counts(live.get(sid))
        addr = names.get(sid, {}).get("address", "")
        opt = "{}{}   ⚡{} 🚲{}".format(label(sid, names), "  ·  " + addr if addr else "", e, m)
        if opt in by_option:
            opt += "  #" + sid
        options.append(opt)
        by_option[opt] = sid

    prompt = "{} station{} found{}. Select one or more (⌘-click):".format(
        len(hits), "" if len(hits) == 1 else "s", " (showing 300)" if len(hits) > 300 else "")
    picked = osa(CHOOSE_SCRIPT, prompt, *options)
    if not picked:
        return
    for opt in picked.split("\n"):
        sid = by_option.get(opt)
        if sid and sid not in favs:
            favs.append(sid)
    save_favs(favs)


def remove(sid):
    save_favs([f for f in load_favs() if f != sid])


def top(sid):
    favs = load_favs()
    if sid in favs:
        favs.remove(sid)
        favs.insert(0, sid)
        save_favs(favs)


def main(argv):
    if len(argv) > 1:
        action = argv[1]
        if action == "add":
            add()
        elif action == "remove" and len(argv) > 2:
            remove(argv[2])
        elif action == "top" and len(argv) > 2:
            top(argv[2])
        return
    render()


if __name__ == "__main__":
    main(sys.argv)
