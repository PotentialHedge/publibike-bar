"""Render an average-week flow map of PubliBike Zürich as PNG frames.

Flows are inferred: per station and hour we know pickups and returns (from live
counts), not trips. Net sources are matched to net sinks with entropic optimal
transport (Sinkhorn), which favours short trips and varies smoothly over time.
"""
import csv, math, os, sys, hashlib
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import cairo
from PIL import Image

OUT = sys.argv[1] if len(sys.argv) > 1 else "frames"
FPH = int(os.environ.get("FPH", 4))          # frames per hour
ONLY = os.environ.get("ONLY")                # render a single frame index (preview)
W, MAPH, PANEL = 800, 800, 130
H = MAPH + PANEL
Z, TS = 15, 256
LON0, LON1, LAT0, LAT1 = 8.465, 8.605, 47.335, 47.430
TZ = ZoneInfo("Europe/Zurich")

def mx(lon): return (lon + 180) / 360 * 2**Z * TS
def my(lat):
    r = math.radians(lat)
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2**Z * TS
PX0, PX1, PY0, PY1 = mx(LON0), mx(LON1), my(LAT1), my(LAT0)
SCALE = W / (PX1 - PX0)
CROP_H = MAPH / SCALE  # map height in mercator px
PYC = (PY0 + PY1) / 2
PY0 = PYC - CROP_H / 2
def proj(lat, lon): return ((mx(lon) - PX0) * SCALE, (my(lat) - PY0) * SCALE)

# ---------- base maps ----------
def basemap(style):
    x0, y0 = int(PX0 // TS), int(PY0 // TS)
    x1, y1 = int(PX1 // TS), int((PY0 + CROP_H) // TS)
    big = Image.new("RGB", ((x1 - x0 + 1) * TS, (y1 - y0 + 1) * TS))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            big.paste(Image.open(f"tiles/{style}15/{x}_{y}.png").convert("RGB"), ((x - x0) * TS, (y - y0) * TS))
    ox, oy = PX0 - x0 * TS, PY0 - y0 * TS
    big = big.crop((round(ox), round(oy), round(ox + (PX1 - PX0)), round(oy + CROP_H)))
    return np.asarray(big.resize((W, MAPH), Image.LANCZOS), dtype=np.float32)
LIGHT, DARK = basemap("light"), basemap("dark")
# water mask from the light canvas (water is a flat grey there)
from PIL import ImageFilter
wm = (np.abs(LIGHT - np.array([209, 204, 207])).max(2) < 7).astype(np.uint8) * 255
wm = np.asarray(Image.fromarray(wm).filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2)), np.float32)[..., None] / 255
LIGHT = LIGHT * np.array([1.0, 0.995, 0.975])
LIGHT = LIGHT * (1 - wm) + np.array([172, 200, 224]) * wm
# night: navy land, streets lit warm like street lamps, near-black water
lum = DARK.mean(2, keepdims=True)
road = np.clip((lum - 80) / 45, 0, 1) ** 0.8
land = np.array([20, 26, 44]) + (lum - 74) * 0.25
DARK = land * (1 - road) + np.array([214, 160, 88]) * road * 0.75 + land * road * 0.25
DARK = DARK * (1 - wm) + np.array([8, 12, 26]) * wm
# blue hour: the day map, dimmed and cooled
MID = LIGHT * np.array([0.40, 0.43, 0.55])

# ---------- data ----------
stations = {}
for r in csv.DictReader(open("data/stations.csv")):
    lat, lon = float(r["lat"]), float(r["lon"])
    if LON0 - 0.01 < lon < LON1 + 0.01 and LAT0 - 0.01 < lat < LAT1 + 0.01:
        stations[r["station_id"]] = (r["name"].replace(" - Zürich", ""), lat, lon)
ids = sorted(stations)
idx = {s: i for i, s in enumerate(ids)}
N = len(ids)
XY = np.array([proj(stations[s][1], stations[s][2]) for s in ids])
LL = np.array([(stations[s][1], stations[s][2]) for s in ids])

OUT0 = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc).timestamp()   # backend outage
OUT1 = datetime(2026, 11, 18, 10, 35, tzinfo=timezone.utc).timestamp()
P = np.zeros((N, 168)); R = np.zeros((N, 168))
prev = {}
tmin, tmax = 1e20, 0
for r in csv.DictReader(open("data/events.csv")):
    s, t, n = r["station_id"], float(r["ts"]), int(r["n"])
    tmin, tmax = min(tmin, t), max(tmax, t)
    p = prev.get(s); prev[s] = n
    if p is None or s not in idx or OUT0 <= t <= OUT1: continue
    d = n - p
    if d == 0 or abs(d) > 2: continue   # >2 at once = rebalancing van
    lt = datetime.fromtimestamp(t, TZ)
    slot = lt.weekday() * 24 + lt.hour
    (P if d < 0 else R)[idx[s], slot] += abs(d)
# how many observed hours fall in each weekly slot
cov = np.zeros(168)
h = datetime.fromtimestamp(math.ceil(tmin / 3600) * 3600, timezone.utc)
while h.timestamp() + 3600 <= tmax:
    if not (OUT0 - 3600 < h.timestamp() < OUT1):
        lt = h.astimezone(TZ); cov[lt.weekday() * 24 + lt.hour] += 1
    h += timedelta(hours=1)
P /= np.maximum(cov, 1); R /= np.maximum(cov, 1)
# slots with no data (Wed 7-11h: the only Wednesday had the outage) -> mean of other weekdays/weekend days
for slot in np.nonzero(cov == 0)[0]:
    d, h = divmod(slot, 24)
    peers = [dd * 24 + h for dd in (range(5) if d < 5 else (5, 6)) if dd != d and cov[dd * 24 + h] > 0]
    P[:, slot] = P[:, peers].mean(1); R[:, slot] = R[:, peers].mean(1)
DAY = bool(os.environ.get("DAY"))      # one average day (all 7 weekdays weighted equally)
SPAN = 24 if DAY else 168
if DAY:
    P = np.tile(P.reshape(-1, 7, 24).mean(1), 7); R = np.tile(R.reshape(-1, 7, 24).mean(1), 7)
print(f"{N} stations, slot coverage {cov.min():.0f}-{cov.max():.0f} days, "
      f"{P.sum()/7:.0f} pickups/day avg", file=sys.stderr)

# distances (km)
lat_r = np.radians(LL[:, 0].mean())
dx = (LL[:, 1][:, None] - LL[:, 1][None]) * 111.32 * math.cos(lat_r)
dy = (LL[:, 0][:, None] - LL[:, 0][None]) * 110.57
D = np.hypot(dx, dy)

def smooth_at(A, tau, sigma=0.75):
    """Circular Gaussian-smoothed rate per station at fractional week-hour tau."""
    c = np.arange(168) + 0.5
    dt = (c - tau + 84) % 168 - 84
    w = np.exp(-0.5 * (dt / sigma) ** 2); w /= w.sum()
    return A @ w

def sinkhorn(a, b, eps=0.35, iters=300):
    K = np.exp(-D / eps)
    u = np.ones_like(a); v = np.ones_like(b)
    for _ in range(iters):
        u = a / (K @ v + 1e-30); v = b / (K.T @ u + 1e-30)
    return u[:, None] * K * v[None]

# ---------- sun ----------
LAT_ZH, LON_ZH = 47.37, 8.54
def sun_alt(hour_local):
    # representative day: 20 Nov (decl -19.7°, equation of time +14 min), CET
    decl = math.radians(-19.7)
    solar = hour_local - 1 + LON_ZH / 15 + 14 / 60
    ha = math.radians(15 * (solar - 12))
    phi = math.radians(LAT_ZH)
    return math.degrees(math.asin(math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)))
def smoothstep(e0, e1, x):
    t = min(1, max(0, (x - e0) / (e1 - e0))); return t * t * t * (t * (6 * t - 15) + 10)
NIGHT_LO, NIGHT_HI = -16, 8          # sun altitude range (deg) over which day fades into night
def night_of(alt): return 1 - smoothstep(NIGHT_LO, NIGHT_HI, alt)

def lerp(a, b, t): return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))
def hexc(h): return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
# palettes (day, night)
# One overlay style for day and night: only the basemap changes, so the overlay never
# passes through a low-contrast crossover (which falls right in the evening rush).
C_FLOW = (hexc("#22b5f0"), hexc("#22b5f0"))
C_PART = (hexc("#ffffff"), hexc("#ffffff"))
C_SRC = (hexc("#f2542d"), hexc("#f2542d"))    # more pickups than returns
C_SNK = (hexc("#14c08a"), hexc("#14c08a"))    # more returns
C_CASE = (0.04, 0.06, 0.12)                   # dark casing under every mark
C_TXT = (hexc("#1c2230"), hexc("#e8ecf5"))
C_PANEL = (hexc("#f4f2ee"), hexc("#10141d"))
C_MUTED = (hexc("#6b7280"), hexc("#8a93a6"))

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TOTAL = P.sum(0)  # pickups per weekly slot
TOTAL_S = np.array([smooth_at(TOTAL[None], t + 0.5, 0.6)[0] for t in range(168)])

# time warp: frames are spent in proportion to activity, so quiet hours fly by
NFRAMES = int(os.environ.get("NFRAMES", 168 * FPH))
_g = np.linspace(0, SPAN, SPAN * 60, endpoint=False)
_rate = np.array([smooth_at(TOTAL[None], t, 1.0)[0] for t in _g[::10]])
_rate = np.repeat(_rate, 10) / _rate.max()
_nt = np.array([night_of(sun_alt(t % 24)) for t in _g])
_w = 0.07 + _rate ** 1.8 + float(os.environ.get("TWI", 0.45)) * 4 * _nt * (1 - _nt)   # also linger on dawn and dusk
_cum = np.concatenate([[0], np.cumsum(_w)]); _cum /= _cum[-1]
TAUS = np.interp(np.arange(NFRAMES) / NFRAMES, _cum, np.concatenate([_g, [SPAN]]))
SPEED = np.gradient(np.unwrap(TAUS, period=SPAN)) * NFRAMES / SPAN   # relative to real-time pace of FPH

LABELS = [("HB", 47.3779, 8.5403), ("ETH", 47.3763, 8.5480), ("Oerlikon", 47.4113, 8.5443),
          ("Hönggerberg", 47.4085, 8.5075), ("Altstetten", 47.3914, 8.4889), ("Wiedikon", 47.3713, 8.5236),
          ("Bellevue", 47.3672, 8.5450), ("Wollishofen", 47.3445, 8.5325), ("Irchel", 47.3975, 8.5485),
          ("Hardbrücke", 47.3850, 8.5170), ("Stettbach", 47.3973, 8.5960), ("Seebach", 47.4215, 8.5450)]

def bezier_pts(p0, p2, bend=0.22, n=24):
    mxp, myp = (p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2
    vx, vy = p2[0] - p0[0], p2[1] - p0[1]
    c = (mxp - vy * bend, myp + vx * bend)
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * np.array(p0) + 2 * (1 - t) * t * np.array(c) + t ** 2 * np.array(p2)

def render(frame, nframes):
    tau = TAUS[frame]                       # week hour (0 = Mon 00:00)
    hour_local = tau % 24
    alt = sun_alt(hour_local)
    night = night_of(alt)
    golden = math.exp(-((alt - 1.5) / 4.0) ** 2)
    ui = smoothstep(0.35, 0.65, night)      # overlay/UI switch faster so text stays readable
    # ---- base map: day -> blue hour -> night ----
    if night < 0.5:
        base = LIGHT + (MID - LIGHT) * (night * 2)
    else:
        base = MID + (DARK - MID) * ((night - 0.5) * 2)
    glow = np.array([255, 165, 95], dtype=np.float32)
    base = base * (1 - 0.15 * golden) + glow * 0.15 * golden
    base = np.clip(base, 0, 255).astype(np.uint8)
    rgba = np.dstack([base[..., 2], base[..., 1], base[..., 0], np.full(base.shape[:2], 255, np.uint8)])
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    buf = np.ndarray((H, W, 4), np.uint8, surf.get_data())
    buf[:MAPH] = rgba
    surf.mark_dirty()   # else cairo thinks the surface is still clear and draws the first stroke with SOURCE
    cr = cairo.Context(surf)
    col = lambda pair: pair[1]

    # ---- flows ----
    p = smooth_at(P, tau); r = smooth_at(R, tau)
    net = r - p
    src = np.clip(-net, 0, None); snk = np.clip(net, 0, None)
    m = min(src.sum(), snk.sum())
    if m > 1e-6:
        T = sinkhorn(src / src.sum() * m, snk / snk.sum() * m)
    else:
        T = np.zeros((N, N))
    np.fill_diagonal(T, 0)
    ii, jj = np.nonzero(T > 0.12)
    order = np.argsort(T[ii, jj])
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    fc, pc = col(C_FLOW), col(C_PART)
    add = False
    cr.set_operator(cairo.OPERATOR_ADD if add else cairo.OPERATOR_OVER)
    arcs = []
    for k in order:
        i, j = ii[k], jj[k]
        arcs.append((i, j, min(1, (T[i, j] - 0.12) / 1.2), bezier_pts(XY[i], XY[j])))
    # pass 1: dark casings, pass 2: coloured lines (so stacked arcs never turn black)
    for stage in (0, 1):
        for i, j, a, pts in arcs:
            cr.move_to(*pts[0])
            for q in pts[1:]: cr.line_to(*q)
            if stage == 0:
                cr.set_source_rgba(*C_CASE, 0.08 + 0.22 * a); cr.set_line_width(2.2 + 3.0 * a)
            else:
                cr.set_source_rgba(*fc, 0.35 + 0.60 * a); cr.set_line_width(0.8 + 2.0 * a)
            cr.stroke()
    for i, j, a, pts in arcs:
        # particles: 3 per pair, travel the arc every 1.5 simulated hours
        seed = int(hashlib.md5(f"{i}-{j}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
        for kk in range(3):
            ph = (frame / 10 + seed + kk / 3) % 1
            n1 = len(pts) - 1
            head = ph * n1; tail = max(0, head - 3.5)
            seg = [pts[int(tail)]] + [pts[x] for x in range(int(tail) + 1, int(head) + 1)]
            hp = pts[int(head)] + (pts[min(n1, int(head) + 1)] - pts[int(head)]) * (head % 1)
            seg.append(hp)
            if len(seg) > 1:
                g = cairo.LinearGradient(*seg[0], *seg[-1])
                g.add_color_stop_rgba(0, *pc, 0); g.add_color_stop_rgba(1, *pc, 0.3 + 0.7 * a)
                cr.set_source(g); cr.set_line_width(1.2 + 1.8 * a)
                cr.move_to(*seg[0])
                for q in seg[1:]: cr.line_to(*q)
                cr.stroke()
            cr.arc(hp[0], hp[1], 1.1 + 1.4 * a, 0, 2 * math.pi)
            cr.set_source_rgba(*C_CASE, 0.35 + 0.5 * a); cr.set_line_width(1.4); cr.stroke_preserve()
            cr.set_source_rgba(*pc, 0.45 + 0.55 * a); cr.fill()
    cr.set_operator(cairo.OPERATOR_OVER)

    # ---- stations ----
    act = p + r
    for k in np.argsort(act):
        if act[k] < 0.05: continue
        rad = 1.2 + 2.6 * math.sqrt(act[k])
        bal = net[k] / (act[k] + 0.4)               # -1 pickups .. +1 returns
        c = lerp(col(C_SRC), col(C_SNK), (bal + 1) / 2)
        neutral = col(C_MUTED)
        c = lerp(neutral, c, min(1, abs(bal) * 1.6))
        cr.set_source_rgba(*c, 0.92)
        cr.arc(XY[k][0], XY[k][1], rad, 0, 2 * math.pi); cr.fill_preserve()
        cr.set_source_rgba(*C_CASE, 0.75)
        cr.set_line_width(1.0); cr.stroke()

    # ---- place labels ----
    cr.select_font_face("Helvetica Neue", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(11)
    tc = col(C_TXT)
    for name, lat, lon in LABELS:
        x, y = proj(lat, lon)
        ext = cr.text_extents(name)
        cr.move_to(x - ext.width / 2, y - 9)
        cr.text_path(name)
        cr.set_source_rgba(*C_CASE, 0.8)
        cr.set_line_width(3); cr.stroke_preserve()
        cr.set_source_rgba(1, 1, 1, 0.92); cr.fill()

    # ---- header card ----
    day = int(tau // 24) % 7
    st = 1 if DAY else 5; tm = int(round(hour_local * 60 / st)) * st % 1440
    hh, mm = divmod(tm, 60)
    card = hexc("#141925")
    # text colour by card brightness, so it never goes grey-on-grey mid-transition
    tc = C_TXT[0] if 0.3 * card[0] + 0.59 * card[1] + 0.11 * card[2] > 0.5 else C_TXT[1]
    mt = lerp(tc, card, 0.42)
    cr.set_source_rgba(*card, 0.86)
    rounded(cr, 14, 14, 250, 78, 10); cr.fill()
    cr.set_source_rgb(*tc)
    cr.select_font_face("Helvetica Neue", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(30); cr.move_to(28, 52); cr.show_text(f"{hh:02d}:{mm:02d}")
    cr.select_font_face("Helvetica Neue", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(16); cr.move_to(120, 50); cr.show_text("Average day" if DAY else DAYS[day])
    cr.set_font_size(11.5); cr.set_source_rgb(*mt)
    cr.move_to(28, 76); cr.show_text(f"≈ {p.sum():.0f} rentals / hour")
    sp = SPEED[frame]; lab = "slow motion" if sp < 0.6 else ("fast forward" if sp > 1.6 else "")
    if lab:
        ext = cr.text_extents(lab); cr.move_to(250 - ext.width, 76); cr.show_text(lab)
    # sun / moon glyph
    gx, gy = 236, 36
    if night < 0.5:
        cr.set_source_rgba(1, 0.72, 0.2, 1 - night * 2); cr.arc(gx, gy, 7, 0, 2 * math.pi); cr.fill()
    else:
        cr.set_source_rgba(0.92, 0.93, 1, (night - 0.5) * 2); cr.arc(gx, gy, 7, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgba(*card, 1); cr.arc(gx + 3.5, gy - 2.5, 6, 0, 2 * math.pi); cr.fill()

    # ---- legend ----
    lx, ly = W - 234, 14
    cr.set_source_rgba(*card, 0.86); rounded(cr, lx, ly, 220, 74, 10); cr.fill()
    cr.set_font_size(11.5)
    for k, (cc, txt) in enumerate([(col(C_SRC), "more pickups than returns"), (col(C_SNK), "more returns than pickups")]):
        cr.set_source_rgb(*cc); cr.arc(lx + 18, ly + 18 + k * 19, 5, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(*tc); cr.move_to(lx + 30, ly + 22 + k * 19); cr.show_text(txt)
    cr.set_source_rgba(*fc, 0.9); cr.set_line_width(2)
    cr.move_to(lx + 10, ly + 61); cr.curve_to(lx + 16, ly + 55, lx + 22, ly + 55, lx + 27, ly + 59); cr.stroke()
    cr.set_source_rgb(*tc); cr.move_to(lx + 30, ly + 61); cr.show_text("inferred net flow of bikes")

    # ---- bottom panel: week timeline ----
    cr.set_source_rgb(*col(C_PANEL)); cr.rectangle(0, MAPH, W, PANEL); cr.fill()
    x0, x1, yb, ht = 24, W - 24, MAPH + 92, 58
    xs = lambda t: x0 + (x1 - x0) * t / SPAN
    # night shading
    for d in range(SPAN // 24):
        for h in range(24):
            nf = night_of(sun_alt(h + 0.5))
            if nf > 0.01:
                cr.set_source_rgba(*col(C_MUTED), 0.13 * nf)
                cr.rectangle(xs(d * 24 + h), yb - ht, xs(1) - x0 + 0.5, ht); cr.fill()
    mxv = TOTAL_S.max()
    cr.move_to(xs(0), yb)
    if DAY:
        for t in np.linspace(0, 24, 97): cr.line_to(xs(t), yb - ht * smooth_at(TOTAL[None], t, 0.6)[0] / mxv)
    else:
        for t in range(168): cr.line_to(xs(t + 0.5), yb - ht * TOTAL_S[t] / mxv)
    cr.line_to(xs(SPAN), yb); cr.close_path()
    cr.set_source_rgba(*fc, 0.35); cr.fill_preserve()
    cr.set_source_rgba(*fc, 0.9); cr.set_line_width(1.2); cr.stroke()
    cr.set_font_size(11)
    ticks = [(h, f"{h:02d}:00", False) for h in range(0, 24, 3)] if DAY else [(d * 24, DAYS[d][:3], d == day) for d in range(7)]
    for tx_, lab, cur in ticks:
        cr.set_source_rgba(*col(C_MUTED), 0.6); cr.set_line_width(1)
        cr.move_to(xs(tx_), yb - ht); cr.line_to(xs(tx_), yb + 4); cr.stroke()
        cr.set_source_rgba(*tc, 1.0 if cur else 0.55)
        cr.move_to(xs(tx_) + 4, yb + 17); cr.show_text(lab)
    cr.set_source_rgb(*col(C_SRC)); cr.set_line_width(2)
    cr.move_to(xs(tau), yb - ht - 4); cr.line_to(xs(tau), yb + 2); cr.stroke()
    cr.arc(xs(tau), yb - ht - 4, 3.5, 0, 2 * math.pi); cr.fill()
    cr.set_source_rgb(*tc); cr.set_font_size(12.5)
    cr.select_font_face("Helvetica Neue", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.move_to(x0, MAPH + 22); cr.show_text("PubliBike Zürich · an average day" if DAY else "PubliBike Zürich · an average week")
    cr.select_font_face("Helvetica Neue", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(10); cr.set_source_rgb(*mt)
    note = ("Live station counts, 13–24 Nov 2026, averaged over all days" if DAY else "Live station counts, 13–24 Nov 2026, averaged by weekday") + " · Basemap: Esri, HERE, Garmin, © OpenStreetMap"
    ext = cr.text_extents(note); cr.move_to(x1 - ext.width, MAPH + 22); cr.show_text(note)
    surf.flush()
    out = np.ndarray((H, W, 4), np.uint8, surf.get_data())[..., [2, 1, 0]].copy()
    Image.fromarray(out).save(f"{OUT}/f{frame:04d}.png")

def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    nf = NFRAMES
    frames = [int(x) for x in ONLY.split(",")] if ONLY else range(nf)
    if not ONLY and os.environ.get("JOBS"):
        from multiprocessing import Pool
        with Pool(int(os.environ["JOBS"])) as pool: pool.starmap(render, [(f, nf) for f in frames])
    else:
        for f in frames: render(f, nf)
