"""Animated SVG for a GitHub profile README, drawn over the contribution graph.

Three cybersecurity scenes take turns, one per day:
  - drone: a UAV secures every day with contributions with a key exchange
    (green turns blue), and the encrypted days block forged packets from a
    man-in-the-middle attacker with a blue shield;
  - firewall: packets reach the graph through a firewall that lets the
    legitimate ones in and blocks the malicious ones;
  - malware: a piece of malware spreads through the graph, and an intrusion
    detection system quarantines and cleans every infected day.

The scene of the day is picked from the date (UTC), so every daily run of the
workflow shows the next one. Two files are written, a light and a dark version,
so the README can follow the viewer's GitHub theme.

Usage:
    python3 scripts/contribution_animation.py --user USERNAME --out dist
    python3 scripts/contribution_animation.py --input days.json --style firewall --out dist

The online mode reads the contribution calendar from the GitHub GraphQL API
and needs a token in the GITHUB_TOKEN environment variable. The token that
GitHub Actions creates for each run is enough. Only the Python standard
library is used.
"""

import argparse
import datetime as dt
import heapq
import json
import math
import os
import random
import urllib.request


# ------------------------------------------------------------ shared parts --

SQUARE = 10           # size of one day in the graph (px)
GAP = 3               # space between days (px)
PITCH = SQUARE + GAP


LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
          "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


GRID = {
    "light": {"empty": "#ebedf0", "green": ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
              "text": "#57606a", "blue": "#0969da", "red": "#cf222e", "ok": "#1a7f37"},
    "dark": {"empty": "#161b22", "green": ["#0e4429", "#006d32", "#26a641", "#39d353"],
             "text": "#8b949e", "blue": "#58a6ff", "red": "#f85149", "ok": "#3fb950"},
}

MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { weekday contributionLevel } }
      }
    }
  }
}
"""


def fetch_calendar(user, token):
    """Return a list of (week, weekday, level) from the GitHub GraphQL API."""
    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": f"bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "contribution-animation"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise SystemExit(f"GitHub API error: {payload['errors']}")
    weeks = (payload["data"]["user"]["contributionsCollection"]
             ["contributionCalendar"]["weeks"])
    days = []
    for w, week in enumerate(weeks):
        for day in week["contributionDays"]:
            days.append((w, day["weekday"], LEVELS[day["contributionLevel"]]))
    return days


def load_input(path):
    """Offline mode: a JSON list of {"date": "YYYY-MM-DD", "level": 0-4}."""
    with open(path) as f:
        items = json.load(f)
    dates = [dt.date.fromisoformat(i["date"]) for i in items]
    first = min(dates)
    first_sunday = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    days = []
    for item, date in zip(items, dates):
        weekday = (date.weekday() + 1) % 7          # 0 = Sunday, like GitHub
        week = (date - first_sunday).days // 7
        days.append((week, weekday, int(item["level"])))
    return days


def pct(t, total):
    """A moment of the loop as a CSS keyframe percentage."""
    return f"{100 * min(max(t, 0.0), total) / total:.3f}%"


def keyframes(name, total, points):
    """CSS keyframes from (time, declarations) pairs, skipping repeated moments."""
    rows, last = [], None
    for t, decl in points:
        p = pct(t, total)
        if p == last:
            rows[-1] = f"{p}{{{decl}}}"
            continue
        rows.append(f"{p}{{{decl}}}")
        last = p
    return f"@keyframes {name}{{{''.join(rows)}}}"


def svg_open(width, height, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
            f'<title id="title">{title}</title>']


# ---------------------------------------------------------- scene 1: drone --

DRONE_MARGIN_X = 30         # room for the drone to enter and leave
DRONE_MARGIN_Y = 22         # room above the graph for the attack
DRONE_ARM = 8               # distance from the drone center to each rotor (px)

DRONE_SPEED = 70.0          # flying speed (px/s)
DRONE_SECURE_PAUSE = 0.45   # hover time to secure one day (s)
DRONE_SCAN_PAUSE = 0.15     # short hover over an empty spot (s)
DRONE_RESET = 1.8           # time for the graph to go back to green (s)
DRONE_MIN_STOPS = 14        # with few contributions, the drone also patrols empty spots
DRONE_MAX_LOOP = 60.0       # longest loop allowed (s)


DRONE_THEMES = {
    "light": {
        "empty": "#ebedf0",
        "green": ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "blue": ["#b6e3ff", "#54aeff", "#0969da", "#033d8b"],
        "body": "#24292f", "rotor": "#57606a", "blade": "#24292f",
        "led": "#1a7f37", "ring": "#0969da", "attack": "#cf222e",
        "shadow": "#000000",
    },
    "dark": {
        "empty": "#161b22",
        "green": ["#0e4429", "#006d32", "#26a641", "#39d353"],
        "blue": ["#0c2d6b", "#1158c7", "#388bfd", "#79c0ff"],
        "body": "#c9d1d9", "rotor": "#8b949e", "blade": "#e6edf3",
        "led": "#3fb950", "ring": "#58a6ff", "attack": "#f85149",
        "shadow": "#000000",
    },
}


def drone_center(week, weekday):
    return (DRONE_MARGIN_X + week * PITCH + SQUARE / 2,
            DRONE_MARGIN_Y + weekday * PITCH + SQUARE / 2)


def drone_route(days, seed):
    """Stops ordered through the year: active days are secured, the rest patrolled."""
    stops = [{"week": w, "day": d, "level": lvl, "secure": True}
             for (w, d, lvl) in days if lvl > 0]

    n_weeks = max(w for (w, _, _) in days) + 1
    missing = DRONE_MIN_STOPS - len(stops)
    if missing > 0:
        rng = random.Random(seed)
        taken = {(s["week"], s["day"]) for s in stops}
        band = n_weeks / missing
        for i in range(missing):
            for _ in range(20):
                w = min(n_weeks - 1, int(i * band + rng.random() * band))
                d = rng.randrange(7)
                if (w, d) not in taken:
                    taken.add((w, d))
                    stops.append({"week": w, "day": d, "level": 0, "secure": False})
                    break

    # left to right through the year, zig-zagging inside each week
    stops.sort(key=lambda s: (s["week"], s["day"] if s["week"] % 2 == 0 else -s["day"]))
    for s in stops:
        s["x"], s["y"] = drone_center(s["week"], s["day"])
    return stops, n_weeks


def drone_schedule(stops, width):
    """Times for every stop, plus the moment the drone leaves and the loop length."""
    mid_y = DRONE_MARGIN_Y + 3 * PITCH + SQUARE / 2
    start = (-2.5 * DRONE_ARM, mid_y)
    end = (width + 2.5 * DRONE_ARM, mid_y)

    points = [start] + [(s["x"], s["y"]) for s in stops] + [end]
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    n_secure = sum(s["secure"] for s in stops)
    n_scan = len(stops) - n_secure

    speed, secure_pause = DRONE_SPEED, DRONE_SECURE_PAUSE
    busy = n_secure * secure_pause + n_scan * DRONE_SCAN_PAUSE + DRONE_RESET
    if length / speed + busy > DRONE_MAX_LOOP:              # busy graphs: hurry up
        secure_pause = min(DRONE_SECURE_PAUSE, 0.25 * DRONE_MAX_LOOP / max(n_secure, 1))
        busy = n_secure * secure_pause + n_scan * DRONE_SCAN_PAUSE + DRONE_RESET
        speed = length / max(DRONE_MAX_LOOP - busy, 1.0)

    t = 0.0
    position = start
    frames = [(0.0, start)]                           # (time, position)
    for s in stops:
        t += math.dist(position, (s["x"], s["y"])) / speed
        position = (s["x"], s["y"])
        s["t"] = t
        frames.append((t, position))
        s["pause"] = secure_pause if s["secure"] else DRONE_SCAN_PAUSE
        t += s["pause"]
        frames.append((t, position))
    t += math.dist(position, end) / speed
    frames.append((t, end))
    return frames, t, t + DRONE_RESET


def drone_svg(days, stops, n_weeks, theme_name, seed):
    th = DRONE_THEMES[theme_name]
    width = 2 * DRONE_MARGIN_X + n_weeks * PITCH - GAP
    height = 2 * DRONE_MARGIN_Y + 7 * PITCH - GAP
    frames, leave, total = drone_schedule(stops, width)
    rng = random.Random(seed)
    css = []

    # drone movement: smooth start and stop at every hover point
    rows = []
    last = None
    for t, (x, y) in frames:
        p = pct(t, total)
        if p == last:
            continue
        last = p
        rows.append(f"{p}{{transform:translate({x:.2f}px,{y:.2f}px);"
                    f"animation-timing-function:ease-in-out}}")
    x, y = frames[-1][1]
    rows.append(f"100%{{transform:translate({x:.2f}px,{y:.2f}px)}}")
    css.append("@keyframes fly{" + "".join(rows) + "}")
    css.append(f"#uav{{animation:fly {total:.2f}s infinite}}")

    secured = [s for s in stops if s["secure"]]

    # each secured day flashes and turns from green to blue, then back at the end
    for i, s in enumerate(secured):
        green = th["green"][s["level"] - 1]
        blue = th["blue"][s["level"] - 1]
        t_in, t_peak, t_out = s["t"] - 0.02, s["t"] + 0.12, s["t"] + 0.32
        back = leave + rng.uniform(0.05, 0.6) * (total - leave)
        css.append(
            f"@keyframes k{i}{{0%,{pct(t_in, total)}{{fill:{green};transform:scale(1)}}"
            f"{pct(t_peak, total)}{{fill:{blue};transform:scale(1.35)}}"
            f"{pct(t_out, total)},{pct(back, total)}{{fill:{blue};transform:scale(1)}}"
            f"100%{{fill:{green};transform:scale(1)}}}}"
            f".k{i}{{animation:k{i} {total:.2f}s linear infinite}}")

    # broadcast rings sent by the drone while it secures a day
    pool = 4
    rings = []
    for k in range(pool):
        mine = [s for j, s in enumerate(secured) if j % pool == k]
        if not mine:
            continue
        rows = ["0%{opacity:0;transform:scale(.4)}"]
        for j, s in enumerate(mine):
            start, stop = s["t"], s["t"] + 0.75
            if j + 1 < len(mine):
                stop = min(stop, mine[j + 1]["t"] - 0.01)
            stop = min(stop, total - 0.01)
            if stop <= start + 0.05:
                continue
            rows.append(f"{pct(start, total)}{{opacity:.7;transform:scale(.4)}}"
                        f"{pct(stop, total)}{{opacity:0;transform:scale(2.6)}}")
        rows.append("100%{opacity:0;transform:scale(.4)}")
        css.append(f"@keyframes r{k}{{" + "".join(rows) + "}")
        css.append(f".r{k}{{animation:r{k} {total:.2f}s linear infinite}}")
        rings.append(f'<circle class="ring r{k}" r="6" fill="none" stroke="{th["ring"]}" '
                     f'stroke-width="1.2" opacity="0"/>')

    # the man-in-the-middle attacks: a forged packet hits a day that is already
    # encrypted (blue) and is blocked by a blue shield
    attacks = []
    for share in (0.35, 0.72):
        t_go = share * leave
        done = [s for s in secured if s["t"] + s["pause"] <= t_go - 2.5]
        if not done:
            done = [s for s in secured if s["t"] + s["pause"] <= t_go - 0.5]
        if not done:
            continue
        target = max(done, key=lambda s: s["t"])
        if any(target is a["s"] for a in attacks):
            continue
        attacks.append({"s": target, "t_go": t_go})
    for a, attack in enumerate(attacks):
        s, t_go = attack["s"], attack["t_go"]
        tx, ty = s["x"], s["y"]
        sx, sy = tx + 42, -12
        t_hit = t_go + 0.8
        css.append(
            f"@keyframes pk{a}{{0%,{pct(t_go - 0.01, total)}{{opacity:0;transform:translate({sx:.2f}px,{sy}px)}}"
            f"{pct(t_go, total)}{{opacity:1;transform:translate({sx:.2f}px,{sy}px)}}"
            f"{pct(t_hit, total)}{{opacity:1;transform:translate({tx + 5:.2f}px,{ty - 10:.2f}px)}}"
            f"{pct(t_hit + 0.08, total)},100%{{opacity:0;transform:translate({tx + 5:.2f}px,{ty - 10:.2f}px)}}}}"
            f"#packet{a}{{animation:pk{a} {total:.2f}s linear infinite}}")
        css.append(
            f"@keyframes sh{a}{{0%,{pct(t_hit - 0.05, total)}{{opacity:0;transform:scale(.5)}}"
            f"{pct(t_hit + 0.05, total)}{{opacity:1;transform:scale(1)}}"
            f"{pct(t_hit + 0.7, total)}{{opacity:.9;transform:scale(1.1)}}"
            f"{pct(t_hit + 1.1, total)},100%{{opacity:0;transform:scale(1.5)}}}}"
            f"#shield{a}{{animation:sh{a} {total:.2f}s linear infinite}}")
        css.append(
            f"@keyframes cr{a}{{0%,{pct(t_hit + 0.04, total)}{{opacity:0}}"
            f"{pct(t_hit + 0.1, total)},{pct(t_hit + 0.8, total)}{{opacity:1}}"
            f"{pct(t_hit + 1.1, total)},100%{{opacity:0}}}}"
            f"#cross{a}{{animation:cr{a} {total:.2f}s linear infinite}}")
        attack["x"], attack["y"] = tx, ty

    css.append("@keyframes spin{to{transform:rotate(360deg)}}"
               ".blade{animation:spin .18s linear infinite}"
               "@keyframes hover{50%{transform:scale(1.07)}}"
               "#craft{animation:hover 1.6s ease-in-out infinite}"
               "@keyframes blink{0%,55%{opacity:1}56%,100%{opacity:.15}}"
               ".led{animation:blink 1s linear infinite}")
    css.append("rect.day,.ring,.blade,#craft,.shield"
               "{transform-box:fill-box;transform-origin:center}")

    # ---- SVG ----
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
           '<title id="title">A UAV flies over the GitHub contribution graph, secures '
           'each day with contributions with a key exchange, and the encrypted days block '
           'forged packets from a man-in-the-middle attacker</title>',
           "<style>" + "".join(css) + "</style>"]

    for (w, d, _) in days:                               # empty grid
        x = DRONE_MARGIN_X + w * PITCH
        y = DRONE_MARGIN_Y + d * PITCH
        out.append(f'<rect x="{x}" y="{y}" width="{SQUARE}" height="{SQUARE}" '
                   f'rx="2" fill="{th["empty"]}"/>')
    for i, s in enumerate(secured):                      # days with contributions
        x = DRONE_MARGIN_X + s["week"] * PITCH
        y = DRONE_MARGIN_Y + s["day"] * PITCH
        out.append(f'<rect class="day k{i}" x="{x}" y="{y}" width="{SQUARE}" '
                   f'height="{SQUARE}" rx="2" fill="{th["green"][s["level"] - 1]}"/>')

    # attacks: a blue shield around the encrypted day, and a red cross on the packet
    for a, attack in enumerate(attacks):
        tx, ty = attack["x"], attack["y"]
        out.append(f'<circle id="shield{a}" class="shield" cx="{tx:.2f}" cy="{ty:.2f}" r="9" '
                   f'fill="{th["ring"]}" fill-opacity=".18" stroke="{th["ring"]}" '
                   f'stroke-width="1.8" opacity="0"/>')
        cx, cy = tx + 5, ty - 10
        out.append(f'<g id="cross{a}" opacity="0" stroke="{th["attack"]}" stroke-width="2.2" '
                   f'stroke-linecap="round"><line x1="{cx - 3.5:.2f}" y1="{cy - 3.5:.2f}" '
                   f'x2="{cx + 3.5:.2f}" y2="{cy + 3.5:.2f}"/><line x1="{cx + 3.5:.2f}" '
                   f'y1="{cy - 3.5:.2f}" x2="{cx - 3.5:.2f}" y2="{cy + 3.5:.2f}"/></g>')

    # the drone (top view): shadow, arms, body, LED and four spinning rotors
    x0, y0 = frames[0][1]
    out.append(f'<g id="uav" transform="translate({x0:.2f} {y0:.2f})">')
    out.extend(rings)
    out.append(f'<ellipse cx="3" cy="4" rx="11" ry="10" fill="{th["shadow"]}" opacity=".12"/>')
    out.append('<g id="craft">')
    for ax in (-DRONE_ARM, DRONE_ARM):
        for ay in (-DRONE_ARM, DRONE_ARM):
            out.append(f'<line x1="0" y1="0" x2="{ax}" y2="{ay}" stroke="{th["body"]}" '
                       f'stroke-width="2.2" stroke-linecap="round"/>')
    for ax in (-DRONE_ARM, DRONE_ARM):
        for ay in (-DRONE_ARM, DRONE_ARM):
            out.append(f'<circle cx="{ax}" cy="{ay}" r="5" fill="{th["rotor"]}" '
                       f'fill-opacity=".22" stroke="{th["rotor"]}" stroke-width=".7"/>')
            out.append(f'<line class="blade" x1="{ax - 4.3}" y1="{ay}" x2="{ax + 4.3}" '
                       f'y2="{ay}" stroke="{th["blade"]}" stroke-width="1.3" '
                       f'stroke-linecap="round"/>')
    out.append(f'<rect x="-4.5" y="-4.5" width="9" height="9" rx="2.5" fill="{th["body"]}"/>')
    out.append(f'<circle class="led" cx="0" cy="0" r="1.6" fill="{th["led"]}"/>')
    out.append("</g></g>")

    # the forged packets, drawn last so they fly over everything
    for a in range(len(attacks)):
        out.append(f'<g id="packet{a}" opacity="0"><rect x="-4" y="-3" width="8" height="6" '
                   f'rx="1" fill="{th["attack"]}"/><path d="M-4,-3 L0,0.5 L4,-3" fill="none" '
                   f'stroke="#ffffff" stroke-width=".8"/></g>')
    out.append("</svg>")
    return "\n".join(out)


def render_drone(days, theme, seed):
    stops, n_weeks = drone_route(days, seed)
    return drone_svg(days, stops, n_weeks, theme, seed)


# ------------------------------------------------------- scene 2: firewall --

FW_MARGIN_LEFT = 58      # room for the incoming packets and the wall
FW_MARGIN_RIGHT = 24
FW_MARGIN_Y = 24
FW_WALL_W = 9
FW_SPEED = 260.0         # packet speed (px/s)
FW_SPACING = 0.24        # time between two packets (s)
FW_MAX_STREAM = 28.0     # longest time spent sending packets (s)
FW_HOLD = 2.2            # time showing the full graph (s)
FW_FADE = 1.4            # time to clear the graph (s)


FW_THEMES = {
    "light": {"brick": "#d0d7de", "mortar": "#8c959f", "flame": "#fb8500", "core": "#ffb703"},
    "dark": {"brick": "#30363d", "mortar": "#6e7681", "flame": "#f0883e", "core": "#f2cc60"},
}


def render_firewall(days, theme, seed):
    g, fw = GRID[theme], FW_THEMES[theme]
    rng = random.Random(seed)
    n_weeks = max(w for (w, _, _) in days) + 1
    width = FW_MARGIN_LEFT + FW_MARGIN_RIGHT + n_weeks * PITCH - GAP
    height = 2 * FW_MARGIN_Y + 7 * PITCH - GAP
    wall_x = FW_MARGIN_LEFT - 22

    def row_y(d):
        return FW_MARGIN_Y + d * PITCH + SQUARE / 2

    good = sorted(((w, d, lvl) for (w, d, lvl) in days if lvl > 0),
                  key=lambda c: (c[0], c[1]))
    n_bad = max(6, len(good) // 6)
    n_packets = len(good) + n_bad
    spacing = min(FW_SPACING, FW_MAX_STREAM / max(n_packets, 1))

    # mix the malicious packets into the stream at random places
    order = [("good", c) for c in good]
    for _ in range(n_bad):
        order.insert(rng.randrange(len(order) + 1), ("bad", rng.randrange(7)))

    start_x = -8
    packets, flashes = [], {d: [] for d in range(7)}
    t = 0.6
    for kind, info in order:
        if kind == "good":
            w, d, lvl = info
            target = FW_MARGIN_LEFT + w * PITCH + SQUARE / 2
        else:
            d, target = info, wall_x
        t_wall = t + (wall_x - start_x) / FW_SPEED
        t_end = t_wall + max(target - wall_x, 0) / FW_SPEED
        packets.append({"kind": kind, "d": d, "t0": t, "t_wall": t_wall, "t_end": t_end,
                        "x_end": target, "info": info})
        flashes[d].append((t_wall, kind))
        t += spacing
    last = max(p["t_end"] for p in packets) if packets else 1.0
    clear = last + FW_HOLD
    total = clear + FW_FADE

    css = []
    for i, p in enumerate(packets):
        y = row_y(p["d"])
        if p["kind"] == "good":
            css.append(keyframes(f"p{i}", total, [
                (0, f"opacity:0;transform:translate({start_x}px,{y}px)"),
                (p["t0"], f"opacity:0;transform:translate({start_x}px,{y}px)"),
                (p["t0"] + 0.01, f"opacity:1;transform:translate({start_x}px,{y}px)"),
                (p["t_end"], f"opacity:1;transform:translate({p['x_end']:.2f}px,{y}px)"),
                (p["t_end"] + 0.01, f"opacity:0;transform:translate({p['x_end']:.2f}px,{y}px)"),
                (total, f"opacity:0;transform:translate({p['x_end']:.2f}px,{y}px)")]))
            # the day lights up when its packet arrives, and clears at the end
            w, d, lvl = p["info"]
            fade = clear + rng.uniform(0, 0.7)
            css.append(keyframes(f"d{i}", total, [
                (0, "opacity:0;transform:scale(.4)"), (p["t_end"], "opacity:0;transform:scale(.4)"),
                (p["t_end"] + 0.12, "opacity:1;transform:scale(1.35)"),
                (p["t_end"] + 0.32, "opacity:1;transform:scale(1)"),
                (fade, "opacity:1;transform:scale(1)"),
                (fade + 0.6, "opacity:0;transform:scale(1)"), (total, "opacity:0;transform:scale(1)")]))
            css.append(f".d{i}{{animation:d{i} {total:.2f}s linear infinite}}")
        else:
            css.append(keyframes(f"p{i}", total, [
                (0, f"opacity:0;transform:translate({start_x}px,{y}px)"),
                (p["t0"], f"opacity:0;transform:translate({start_x}px,{y}px)"),
                (p["t0"] + 0.01, f"opacity:1;transform:translate({start_x}px,{y}px)"),
                (p["t_wall"] - 0.02, f"opacity:1;transform:translate({wall_x - 4}px,{y}px)"),
                (p["t_wall"], f"opacity:0;transform:translate({wall_x - 4}px,{y}px)"),
                (total, f"opacity:0;transform:translate({wall_x - 4}px,{y}px)")]))
            # a red burst where the packet is blocked
            css.append(keyframes(f"b{i}", total, [
                (0, "opacity:0;transform:scale(.3)"), (p["t_wall"] - 0.01, "opacity:0;transform:scale(.3)"),
                (p["t_wall"], "opacity:1;transform:scale(.3)"),
                (p["t_wall"] + 0.45, "opacity:0;transform:scale(1.8)"), (total, "opacity:0;transform:scale(1.8)")]))
            css.append(f".b{i}{{animation:b{i} {total:.2f}s linear infinite}}")
        css.append(f".p{i}{{animation:p{i} {total:.2f}s linear infinite}}")

    # each brick flashes green when it lets a packet through and red when it blocks one
    for d in range(7):
        points = [(0, f"fill:{fw['brick']}")]
        busy = -1.0
        for t_hit, kind in sorted(flashes[d]):
            if t_hit < busy + 0.05:
                continue
            color = g["ok"] if kind == "good" else g["red"]
            points += [(t_hit - 0.01, f"fill:{fw['brick']}"), (t_hit, f"fill:{color}"),
                       (t_hit + 0.22, f"fill:{fw['brick']}")]
            busy = t_hit + 0.22
        points.append((total, f"fill:{fw['brick']}"))
        css.append(keyframes(f"w{d}", total, points))
        css.append(f".w{d}{{animation:w{d} {total:.2f}s linear infinite}}")

    # counter of blocked packets
    blocked = sorted(p["t_wall"] for p in packets if p["kind"] == "bad")
    counts = [0.0] + blocked
    for k, t in enumerate(counts):
        end = counts[k + 1] if k + 1 < len(counts) else clear + 0.6
        css.append(keyframes(f"c{k}", total, [
            (0, "opacity:0"), (t, "opacity:0"), (t + 0.001, "opacity:1"),
            (end, "opacity:1"), (end + 0.001, "opacity:0"), (total, "opacity:0")]))
        css.append(f".c{k}{{animation:c{k} {total:.2f}s step-end infinite}}")

    css.append("@keyframes flicker{0%,100%{transform:scale(1,1)}30%{transform:scale(.9,1.12)}"
               "60%{transform:scale(1.08,.92)}}#flame{animation:flicker .7s ease-in-out infinite}")
    css.append("rect.day,.burst,#flame{transform-box:fill-box;transform-origin:center bottom}"
               "rect.day,.burst{transform-origin:center}")

    out = svg_open(width, height, "Packets reach the GitHub contribution graph through a "
                   "firewall that lets legitimate packets in and blocks malicious ones")
    out.append("<style>" + "".join(css) + "</style>")
    out.append(f'<text x="{FW_MARGIN_LEFT}" y="{FW_MARGIN_Y - 9}" font-family="{MONO}" '
               f'font-size="9" fill="{g["text"]}"><tspan fill="{g["ok"]}">$</tspan> '
               f'iptables -A INPUT -m conntrack --ctstate INVALID -j DROP</text>')
    for k in range(len(counts)):
        out.append(f'<text class="c{k}" x="{width - FW_MARGIN_RIGHT}" y="{FW_MARGIN_Y - 9}" '
                   f'text-anchor="end" font-family="{MONO}" font-size="9" fill="{g["red"]}" '
                   f'opacity="0">{k} blocked</text>')

    for (w, d, _) in days:
        out.append(f'<rect x="{FW_MARGIN_LEFT + w * PITCH}" y="{FW_MARGIN_Y + d * PITCH}" '
                   f'width="{SQUARE}" height="{SQUARE}" rx="2" fill="{g["empty"]}"/>')
    for i, p in enumerate(packets):
        if p["kind"] == "good":
            w, d, lvl = p["info"]
            out.append(f'<rect class="day d{i}" x="{FW_MARGIN_LEFT + w * PITCH}" '
                       f'y="{FW_MARGIN_Y + d * PITCH}" width="{SQUARE}" height="{SQUARE}" rx="2" '
                       f'fill="{g["green"][lvl - 1]}" opacity="0"/>')

    # the wall: one brick per lane and a small flame on top
    for d in range(7):
        y = FW_MARGIN_Y + d * PITCH - 1
        out.append(f'<rect class="w{d}" x="{wall_x}" y="{y}" width="{FW_WALL_W}" height="{PITCH - 1}" '
                   f'rx="1.5" fill="{fw["brick"]}" stroke="{fw["mortar"]}" stroke-width=".8"/>')
    fx, fy = wall_x + FW_WALL_W / 2, FW_MARGIN_Y - 3
    out.append(f'<g id="flame"><path d="M{fx},{fy} c-4,-2 -4,-6 -1,-10 c0,3 2,3 2,1 '
               f'c0,-2 1,-3 2,-4 c0,3 3,5 2,9 c-1,3 -3,4 -5,4z" fill="{fw["flame"]}"/>'
               f'<path d="M{fx},{fy} c-2,-1 -2,-3 -0.5,-5 c0.5,1.5 1.5,1.5 1.5,0 '
               f'c1,1.5 1.5,3 0.5,4.5 c-0.5,0.5 -1,0.5 -1.5,0.5z" fill="{fw["core"]}"/></g>')

    for i, p in enumerate(packets):
        if p["kind"] == "good":
            w, d, lvl = p["info"]
            out.append(f'<circle class="p{i}" r="2.6" fill="{g["green"][max(lvl, 3) - 1]}" '
                       f'opacity="0"/>')
        else:
            out.append(f'<g class="p{i}" opacity="0"><rect x="-3.5" y="-3.5" width="7" height="7" '
                       f'rx="1" transform="rotate(45)" fill="{g["red"]}"/></g>')
            y = row_y(p["d"])
            out.append(f'<circle class="burst b{i}" cx="{wall_x - 2}" cy="{y}" r="6" '
                       f'fill="none" stroke="{g["red"]}" stroke-width="1.8" opacity="0"/>')
    out.append("</svg>")
    return "\n".join(out)


# -------------------------------------------------------- scene 3: malware --

MW_MARGIN_X = 30
MW_MARGIN_Y = 24
MW_START = 1.2           # calm time before the infection (s)
MW_SPREAD = 9.0          # time for the malware to reach every day (s)
MW_DETECT = 1.0          # time between the last infection and the alert (s)
MW_QUARANTINE = 1.2      # time to quarantine every infected day (s)
MW_CLEAN = 6.0           # time to clean every day (s)
MW_HOLD = 1.6            # calm time at the end (s)
MW_NEAR = 2.5            # days closer than this (in squares) infect each other quickly


def malware_infection_order(points, start):
    """Earliest infection time of each day, and who infected it (Dijkstra)."""
    n = len(points)
    best = [math.inf] * n
    parent = [None] * n
    best[start] = 0.0
    heap = [(0.0, start)]
    while heap:
        t, u = heapq.heappop(heap)
        if t > best[u]:
            continue
        for v in range(n):
            if v == u:
                continue
            d = math.dist(points[u], points[v]) / PITCH
            cost = d if d <= MW_NEAR else d * 3            # far jumps are slow
            if t + cost < best[v]:
                best[v], parent[v] = t + cost, u
                heapq.heappush(heap, (t + cost, v))
    return best, parent


def render_malware(days, theme, seed):
    g = GRID[theme]
    rng = random.Random(seed)
    n_weeks = max(w for (w, _, _) in days) + 1
    width = 2 * MW_MARGIN_X + n_weeks * PITCH - GAP
    height = 2 * MW_MARGIN_Y + 7 * PITCH - GAP

    hosts = sorted(((w, d, lvl) for (w, d, lvl) in days if lvl > 0),
                   key=lambda c: (c[0], c[1]))
    points = [(MW_MARGIN_X + w * PITCH + SQUARE / 2, MW_MARGIN_Y + d * PITCH + SQUARE / 2)
              for (w, d, _) in hosts]
    n = len(hosts)

    if n:
        first = rng.randrange(max(1, n // 3), max(2, 2 * n // 3)) if n > 2 else 0
        reach, parent = malware_infection_order(points, first)
        longest = max(reach) or 1.0
        hit = [MW_START + MW_SPREAD * r / longest if n > 1 else MW_START for r in reach]
        t_alert = max(hit) + MW_DETECT
        rank = sorted(range(n), key=lambda i: hit[i])
        quarantine = {i: t_alert + 0.3 + MW_QUARANTINE * k / max(n, 1) for k, i in enumerate(rank)}
        cleaned = {i: t_alert + 0.3 + MW_QUARANTINE + 0.4 + MW_CLEAN * k / max(n, 1)
                   for k, i in enumerate(rank)}
        t_clean = max(cleaned.values()) + 0.4
    else:
        hit, parent, t_alert, t_clean = [], [], MW_START, MW_START
        quarantine, cleaned = {}, {}
    total = t_clean + MW_HOLD

    css = []
    for i, (w, d, lvl) in enumerate(hosts):
        green = g["green"][lvl - 1]
        css.append(keyframes(f"h{i}", total, [
            (0, f"fill:{green};transform:scale(1)"),
            (hit[i] - 0.02, f"fill:{green};transform:scale(1)"),
            (hit[i] + 0.1, f"fill:{g['red']};transform:scale(1.45)"),
            (hit[i] + 0.3, f"fill:{g['red']};transform:scale(1)"),
            (cleaned[i], f"fill:{g['red']};transform:scale(1)"),
            (cleaned[i] + 0.12, f"fill:{green};transform:scale(1.35)"),
            (cleaned[i] + 0.35, f"fill:{green};transform:scale(1)"),
            (total, f"fill:{green};transform:scale(1)")]))
        css.append(f".h{i}{{animation:h{i} {total:.2f}s linear infinite}}")
        # quarantine box around the infected day until it is clean
        css.append(keyframes(f"q{i}", total, [
            (0, "opacity:0;transform:scale(1.8)"), (quarantine[i], "opacity:0;transform:scale(1.8)"),
            (quarantine[i] + 0.25, "opacity:1;transform:scale(1)"),
            (cleaned[i] + 0.1, "opacity:1;transform:scale(1)"),
            (cleaned[i] + 0.4, "opacity:0;transform:scale(1)"), (total, "opacity:0;transform:scale(1)")]))
        css.append(f".q{i}{{animation:q{i} {total:.2f}s linear infinite}}")
        # infection path from the day that passed the malware on
        if parent[i] is not None:
            length = math.dist(points[i], points[parent[i]])
            t0 = hit[parent[i]]
            css.append(keyframes(f"s{i}", total, [
                (0, f"stroke-dashoffset:{length:.2f};opacity:1"),
                (t0, f"stroke-dashoffset:{length:.2f};opacity:1"),
                (hit[i], "stroke-dashoffset:0;opacity:1"),
                (t_alert + 0.3, "stroke-dashoffset:0;opacity:1"),
                (t_alert + 0.9, "stroke-dashoffset:0;opacity:0"),
                (total, f"stroke-dashoffset:{length:.2f};opacity:0")]))
            css.append(f".s{i}{{stroke-dasharray:{length:.2f};animation:s{i} {total:.2f}s linear infinite}}")

    # status text with a counter of infected days
    states = [("st0", 0, MW_START, g["ok"], "● status: clean")]
    for k, t in enumerate(sorted(hit)):
        end = sorted(hit)[k + 1] if k + 1 < n else t_alert
        states.append((f"n{k}", t, end, g["red"], f"● infected: {k + 1}"))
    if n:
        states.append(("st1", t_alert, t_alert + 0.3 + MW_QUARANTINE + 0.4, g["red"],
                       "▲ IDS alert: quarantining"))
        states.append(("st2", t_alert + 0.3 + MW_QUARANTINE + 0.4, t_clean, g["blue"],
                       "● cleaning hosts..."))
    states.append(("st3", t_clean if n else MW_START, total, g["ok"], "● status: clean"))
    for name, t0, t1, _, _ in states:
        css.append(keyframes(name, total, [(0, "opacity:0"), (t0, "opacity:0"),
                                           (t0 + 0.001, "opacity:1"), (t1, "opacity:1"),
                                           (t1 + 0.001, "opacity:0"), (total, "opacity:0")]))
        css.append(f".{name}{{animation:{name} {total:.2f}s step-end infinite}}")
    css.append("rect.host,rect.box{transform-box:fill-box;transform-origin:center}")

    out = svg_open(width, height, "Malware spreads through the days with contributions; "
                   "an intrusion detection system quarantines and cleans them")
    out.append("<style>" + "".join(css) + "</style>")
    out.append(f'<text x="{MW_MARGIN_X}" y="{MW_MARGIN_Y - 9}" font-family="{MONO}" font-size="9" '
               f'fill="{g["text"]}"><tspan fill="{g["ok"]}">$</tspan> sudo ids --monitor '
               f'contributions</text>')
    for name, _, _, color, label in states:
        out.append(f'<text class="{name}" x="{width - MW_MARGIN_X}" y="{MW_MARGIN_Y - 9}" '
                   f'text-anchor="end" font-family="{MONO}" font-size="9" fill="{color}" '
                   f'opacity="0">{label}</text>')

    for (w, d, _) in days:
        out.append(f'<rect x="{MW_MARGIN_X + w * PITCH}" y="{MW_MARGIN_Y + d * PITCH}" '
                   f'width="{SQUARE}" height="{SQUARE}" rx="2" fill="{g["empty"]}"/>')
    for i in range(n):
        if parent[i] is not None:
            (x1, y1), (x2, y2) = points[parent[i]], points[i]
            out.append(f'<line class="s{i}" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" '
                       f'y2="{y2:.2f}" stroke="{g["red"]}" stroke-width="1.1" '
                       f'stroke-linecap="round" opacity="0"/>')
    for i, (w, d, lvl) in enumerate(hosts):
        out.append(f'<rect class="host h{i}" x="{MW_MARGIN_X + w * PITCH}" y="{MW_MARGIN_Y + d * PITCH}" '
                   f'width="{SQUARE}" height="{SQUARE}" rx="2" fill="{g["green"][lvl - 1]}"/>')
    for i, (w, d, lvl) in enumerate(hosts):
        out.append(f'<rect class="box q{i}" x="{MW_MARGIN_X + w * PITCH - 2}" '
                   f'y="{MW_MARGIN_Y + d * PITCH - 2}" width="{SQUARE + 4}" height="{SQUARE + 4}" '
                   f'rx="3.5" fill="none" stroke="{g["blue"]}" stroke-width="1.3" opacity="0"/>')
    out.append("</svg>")
    return "\n".join(out)


# -------------------------------------------------------------------- main --


STYLES = {"drone": render_drone, "firewall": render_firewall, "malware": render_malware}
ROTATION = ["drone", "firewall", "malware"]


def style_of_the_day(today=None):
    """The scene changes every day, in the order of ROTATION."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return ROTATION[today.toordinal() % len(ROTATION)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--user", help="GitHub username")
    source.add_argument("--input", help="offline JSON file with dates and levels")
    parser.add_argument("--style", choices=["auto"] + ROTATION, default="auto",
                        help="scene to draw (default: auto, one per day)")
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    args = parser.parse_args()

    if args.user:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("Set the GITHUB_TOKEN environment variable.")
        days = fetch_calendar(args.user, token)
        seed = args.user
    else:
        days = load_input(args.input)
        seed = os.path.basename(args.input)

    style = style_of_the_day() if args.style == "auto" else args.style
    render = STYLES[style]
    os.makedirs(args.out, exist_ok=True)
    for theme, name in (("light", "contributions.svg"), ("dark", "contributions-dark.svg")):
        path = os.path.join(args.out, name)
        with open(path, "w") as f:
            f.write(render(days, theme, seed))
        print(f"wrote {path} ({style})")


if __name__ == "__main__":
    main()
