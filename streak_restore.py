#!/usr/bin/env python3
"""ABA Streak Restore - sends Snapchat streak restore requests for you.

Type your Snapchat username, email and phone number once, then the usernames
of the friends you lost streaks with. The app fills in Snapchat's official
"restore my Streak" support form for every friend:

    https://help.snapchat.com/hc/en-us/requests/new?co=true&ticket_form_id=149423

On a computer with Selenium installed it types everything into Chrome and
presses Submit for you ("Chrome autopilot"). On a phone it opens the form
already filled in, so all that's left is tapping Submit.

Runs anywhere Python 3.8+ does:
    Android   Termux or Pydroid 3
    iPhone    a-Shell, Pyto or Pythonista
    Computer  Windows, macOS or Linux (pip install selenium for autopilot)
"""
from __future__ import annotations

import argparse
import atexit
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

APP_NAME = "ABA Streak Restore"
VERSION = "2.0.0"

HELP_CENTER = "https://help.snapchat.com"
TICKET_FORM_ID = "149423"

# Ids of the fields on Snapchat's streak restore form. If Snapchat changes the
# form and fields stop filling in: open the form on a computer, right-click a
# field > Inspect, and copy the number from name="request[custom_fields][...]".
FIELD_IDS = {
    "username": "24281229",  # your Snapchat username
    "email": "24335325",  # email address on your account
    "phone": "24369716",  # phone number on your account
    "friend": "24369736",  # friend you lost the Streak with
}

METHODS = {
    "auto": (
        "Automatic",
        "Chrome autopilot on a computer with Selenium installed, pre-filled links everywhere else.",
    ),
    "chrome": (
        "Chrome autopilot",
        "Types everything into the form in a Chrome window and presses Submit for you. "
        "Computers only, needs Selenium (pip install selenium).",
    ),
    "link": (
        "Pre-filled link",
        "Opens the form in your browser with everything filled in, you tap Submit. Works on phones.",
    ),
}

DEFAULT_SETTINGS = {
    "method": "auto",
    "auto_submit": True,
    "headless": False,
    "delay": 10,
    "locale": "en-us",
    "colors": "auto",
    "animations": True,
}

DATA_FILE_NAME = "streak_restore_data.json"
DATA_VERSION = 2
HISTORY_LIMIT = 500

DONE_KEYS = ("", "y", "yes", "ok", "done", "sent")  # "I sent it"


class Quit(Exception):
    """The user pressed Ctrl+C / Ctrl+D."""


# --------------------------------------------------------------------------
# The form
# --------------------------------------------------------------------------

def field_values(profile: dict, friend: str) -> dict:
    return {
        "username": profile["username"],
        "email": profile["email"],
        "phone": profile["phone"],
        "friend": friend,
    }


def form_url(locale: str = "en-us", profile: dict | None = None, friend: str | None = None) -> str:
    """URL of the streak restore form, pre-filled when profile and friend are given.

    Zendesk help centers (which Snapchat Support runs on) fill a form field in
    from a ``tf_<field id>`` query parameter. That's what lets this work on a
    phone without any browser automation.
    """
    params = [("co", "true"), ("ticket_form_id", TICKET_FORM_ID)]
    if profile and friend:
        params += [(f"tf_{FIELD_IDS[key]}", value) for key, value in field_values(profile, friend).items()]
        params.append(("tf_anonymous_requester_email", profile["email"]))
    return f"{HELP_CENTER}/hc/{locale}/requests/new?{urlencode(params, quote_via=quote)}"


def field_name(key: str) -> str:
    return f"request[custom_fields][{FIELD_IDS[key]}]"


# --------------------------------------------------------------------------
# Checking what the user types
# --------------------------------------------------------------------------

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,29}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
SNAP_LINK_RE = re.compile(r"snapchat\.com/(?:add/|@)([A-Za-z0-9._-]+)", re.I)


def clean_username(raw: str) -> str:
    """Turn '@name', 'name,' or a snapchat.com/add/name link into 'name'."""
    text = raw.strip()
    link = SNAP_LINK_RE.search(text)
    if link:
        text = link.group(1)
    return text.lstrip("@").strip().strip(".,;:!?\"'")


def check_username(raw: str):
    name = clean_username(raw)
    if not USERNAME_RE.match(name):
        return name, "That doesn't look like a Snapchat username (3+ characters: letters, numbers, . _ -)."
    return name, None


def check_email(raw: str):
    email = raw.strip()
    if not EMAIL_RE.match(email):
        return email, "That doesn't look like an email address."
    return email, None


def check_phone(raw: str):
    phone = re.sub(r"\s+", " ", raw.strip())
    if re.search(r"[^\d+\-\s().]", phone):
        return phone, "Use digits only (spaces, dashes and a leading + are fine)."
    if not 7 <= len(re.sub(r"\D", "", phone)) <= 15:
        return phone, "That number looks too short or too long."
    return phone, None


def split_usernames(raw: str) -> list:
    return [token for token in re.split(r"[\s,;]+", raw) if token]


# --------------------------------------------------------------------------
# Terminal look and feel
# --------------------------------------------------------------------------

class Theme:
    color = False  # ANSI colors
    rich = False  # 256 colors, for gradients
    fancy = True  # Unicode blocks, lines and emoji
    motion = False  # animations
    cursor_hidden = False


T = Theme()
MARGIN = "  "

FIRE = (226, 220, 214, 208, 202, 196)  # yellow -> orange -> red

_BASIC = {
    "bold": "1",
    "red": "91",
    "green": "92",
    "yellow": "93",
    "cyan": "96",
    "gray": "90",
    "faint": "90",
    "pill": "30;103",
}
_RICH = {
    "bold": "1",
    "red": "38;5;203",
    "green": "38;5;84",
    "yellow": "38;5;226",
    "cyan": "38;5;87",
    "gray": "38;5;245",
    "faint": "38;5;238",
    "pill": "38;5;16;48;5;226",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_ICONS = {  # name: (fancy, plain)
    "ghost": ("👻", ""),
    "fire": ("🔥", "*"),
    "wave": ("👋", ""),
    "ok": ("✓", "+"),
    "no": ("✗", "x"),
    "skip": ("↷", "-"),
    "warn": ("!", "!"),
    "arrow": ("›", ">"),
    "dot": ("·", "|"),
}

STATUS_LOOK = {  # status: (icon, color, word)
    "submitted": ("ok", "green", "sent"),
    "skipped": ("skip", "gray", "skipped"),
    "failed": ("no", "red", "failed"),
}

LOGO = (
    " █████╗ ██████╗  █████╗ ",
    "██╔══██╗██╔══██╗██╔══██╗",
    "███████║██████╔╝███████║",
    "██╔══██║██╔══██╗██╔══██║",
    "██║  ██║██████╔╝██║  ██║",
    "╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝",
)
LOGO_PLAIN = (
    "    _    ____    _    ",
    "   / \\  | __ )  / \\   ",
    "  / _ \\ |  _ \\ / _ \\  ",
    " / ___ \\| |_) / ___ \\ ",
    "/_/   \\_\\____/_/   \\_\\",
)
SUBTITLE = "S T R E A K   R E S T O R E"
SPINNER_FRAMES = (("●∙∙", "∙●∙", "∙∙●", "∙●∙"), ("|", "/", "-", "\\"))


def paint(text: str, *styles) -> str:
    """Color text. Styles are names from the tables above or 256-color numbers."""
    if not T.color or not styles:
        return text
    table = _RICH if T.rich else _BASIC
    codes = []
    for style in styles:
        if isinstance(style, int):
            codes.append(f"38;5;{style}" if T.rich else _BASIC["yellow"])
        else:
            codes.append(table[style])
    return "\x1b[" + ";".join(codes) + "m" + text + "\x1b[0m"


def _runs(pairs, *extra) -> str:
    """[(color, char), ...] -> string, with one color code per run of equal colors."""
    pieces, run, current = [], [], None
    for color, ch in pairs:
        if run and color != current:
            pieces.append(paint("".join(run), current, *extra))
            run = []
        current = color
        run.append(ch)
    if run:
        pieces.append(paint("".join(run), current, *extra))
    return "".join(pieces)


def gradient(text: str, span: int = 0, colors=FIRE, *extra) -> str:
    """Color text left to right through colors (over `span` columns)."""
    if not T.color:
        return text
    if not T.rich:
        return paint(text, "yellow", *extra)
    span = max(1, span or len(text))
    return _runs([(colors[min(len(colors) - 1, i * len(colors) // span)], ch) for i, ch in enumerate(text)], *extra)


def icon(name: str) -> str:
    fancy, plain = _ICONS[name]
    return fancy if T.fancy else plain


def dwidth(text: str) -> int:
    """How many terminal columns text takes up (emoji are two wide)."""
    columns = 0
    for ch in _ANSI_RE.sub("", text):
        code = ord(ch)
        if unicodedata.combining(ch) or code == 0x200D or 0xFE00 <= code <= 0xFE0F:
            continue
        columns += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return columns


def _stdout_can_encode(text: str) -> bool:
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def setup_terminal(colors: str = "auto", animations: bool = True) -> None:
    if os.name == "nt":
        os.system("")  # switches on ANSI escape handling in the Windows console
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        sys.stdout.reconfigure(errors="replace")  # never crash on an odd character
    except (AttributeError, ValueError):
        pass
    T.fancy = _stdout_can_encode("╭─╮│╰╯━█╗╔═║›·✓✗↷●∙👻🔥")
    tty = sys.stdout.isatty()
    if colors in ("on", "off"):
        T.color = colors == "on"
    elif os.environ.get("NO_COLOR"):
        T.color = False
    elif os.environ.get("FORCE_COLOR"):
        T.color = True
    else:
        T.color = tty and os.environ.get("TERM") != "dumb"
    term = os.environ.get("TERM", "")
    T.rich = T.color and (
        "256" in term
        or os.environ.get("COLORTERM") in ("truecolor", "24bit")
        or os.name == "nt"
        or is_termux()
    )
    T.motion = T.color and animations and tty and shutil.get_terminal_size((60, 24)).columns >= 32


def hide_cursor() -> None:
    if T.motion and not T.cursor_hidden:
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        T.cursor_hidden = True


def show_cursor() -> None:
    if T.cursor_hidden:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        T.cursor_hidden = False


def linger(seconds: float) -> None:
    """A short dramatic pause, only when animations are on."""
    if T.motion:
        time.sleep(seconds)


def width() -> int:
    """Width to draw at: fits a phone held upright, doesn't sprawl on a PC."""
    columns = shutil.get_terminal_size((60, 24)).columns
    return max(30, min(columns - 3, 58))


def centered(text: str) -> str:
    return " " * max(0, (width() - dwidth(text)) // 2) + text


def out(text: str = "") -> None:
    print(MARGIN + text if text else "", flush=True)


def reveal(lines) -> None:
    """Print lines, sliding them in one after another when animations are on."""
    for line in lines:
        out(line)
        linger(0.012)


def para(text: str, *styles) -> None:
    for line in textwrap.wrap(text, width()):
        out(paint(line, *styles))


def ok(text: str) -> None:
    para(f"{icon('ok')} {text}", "green")


def warn(text: str) -> None:
    para(f"{icon('warn')} {text}", "yellow")


def fail(text: str) -> None:
    para(f"{icon('no')} {text}", "red")


def clear() -> None:
    if T.color:
        print("\x1b[2J\x1b[H", end="", flush=True)


def typewrite(prefix: str, text: str, *styles, animate: bool | None = None) -> None:
    """Print text one letter at a time."""
    if not (T.motion if animate is None else animate and T.motion):
        print(prefix + paint(text, *styles), flush=True)
        return
    sys.stdout.write(prefix)
    for ch in text:
        sys.stdout.write(paint(ch, *styles))
        sys.stdout.flush()
        if ch != " ":
            time.sleep(0.012)
    sys.stdout.write("\n")
    sys.stdout.flush()


def rule(animate: bool = False) -> None:
    """A full-width fire-gradient line, drawn left to right when animated."""
    w = width()
    line = ("━" if T.fancy else "=") * w
    if animate and T.motion:
        for step in range(1, 13):
            sys.stdout.write("\r" + MARGIN + gradient(line[: w * step // 12], w))
            sys.stdout.flush()
            time.sleep(0.015)
        sys.stdout.write("\n")
    else:
        out(gradient(line, w))


def _logo_rows(shine: int | None = None) -> list:
    """The ABA logo, top-to-bottom fire colors, with a white diagonal glint at `shine`."""
    rows = []
    for r, line in enumerate(LOGO if T.fancy else LOGO_PLAIN):
        if not T.rich:
            rows.append(paint(line, "yellow", "bold"))
            continue
        base = FIRE[min(r, len(FIRE) - 1)]
        pairs = []
        for c, ch in enumerate(line):
            d = c + r - shine if shine is not None else -9
            pairs.append((231 if 0 <= d <= 2 else 230 if d in (-1, 3) else base, ch))
        rows.append(_runs(pairs, "bold"))
    return rows


def header(intro: bool = False) -> None:
    """ABA logo + title. With intro=True it drops in, shines and types itself out."""
    logo_width = dwidth((LOGO if T.fancy else LOGO_PLAIN)[0])
    pad = " " * max(0, (width() - logo_width) // 2)
    animate = intro and T.motion
    rows = _logo_rows()
    if animate:
        hide_cursor()
        for row in rows:
            out(pad + row)
            time.sleep(0.04)
        if T.rich:
            for shine in list(range(-len(rows) - 3, logo_width + len(rows) + 3, 2)) + [None]:
                sys.stdout.write(f"\x1b[{len(rows)}A")
                for row in _logo_rows(shine):
                    sys.stdout.write(f"\r{MARGIN}{pad}{row}\x1b[K\n")
                sys.stdout.flush()
                time.sleep(0.018)
    else:
        for row in rows:
            out(pad + row)
    typewrite(MARGIN + " " * max(0, (width() - len(SUBTITLE)) // 2), SUBTITLE, "bold", animate=animate)
    rule(animate)
    tag = f"{icon('ghost')} for Snapchat Support  {icon('dot')}  v{VERSION}".strip()
    out(paint(centered(tag), "gray"))


def box(rows, title: str = "", style="yellow") -> None:
    """Draw a rounded box. Rows are strings or (text, *styles) tuples."""
    w = width()
    inner = w - 4
    h, v, tl, tr, bl, br = ("─", "│", "╭", "╮", "╰", "╯") if T.fancy else ("-", "|", "+", "+", "+", "+")
    lines = []
    if title:
        label = f" {title} "
        lines.append(paint(tl + h, style) + paint(label, style, "bold") + paint(h * max(0, w - 3 - dwidth(label)) + tr, style))
    else:
        lines.append(paint(tl + h * (w - 2) + tr, style))
    for row in rows:
        text, styles = (row, ()) if isinstance(row, str) else (row[0], row[1:])
        bullet = re.match(r"\s*(?:\d+\.|-)\s+", text)
        hang = " " * len(bullet.group(0)) if bullet else ""
        for piece in textwrap.wrap(text, inner, subsequent_indent=hang) or [""]:
            gap = max(0, inner - dwidth(piece))
            lines.append(paint(v, style) + " " + paint(piece, *styles) + " " * gap + " " + paint(v, style))
    lines.append(paint(bl + h * (w - 2) + br, style))
    reveal(lines)


def section(title: str) -> None:
    thick, thin = ("━", "─") if T.fancy else ("=", "-")
    out()
    out(gradient(thick * 2) + " " + paint(title, "bold") + " " + paint(thin * max(0, width() - dwidth(title) - 4), "faint"))


def bar(fraction: float, size: int) -> str:
    full = max(0, min(size, round(size * fraction)))
    on, off = ("━", "─") if T.fancy else ("#", "-")
    return gradient(on * full, size) + paint(off * (size - full), "faint")


def progress(done: int, total: int, previous: int | None = None) -> None:
    """A 'done/total' fire bar that fills up smoothly from `previous`."""
    size = max(10, width() - 8)
    label = paint(f" {done}/{total}", "gray")
    if T.motion and previous is not None and previous != done:
        hide_cursor()
        for k in range(1, 11):
            value = previous + (done - previous) * k / 10
            sys.stdout.write(f"\r{MARGIN}{bar(value / total, size)}{label}\x1b[K")
            sys.stdout.flush()
            time.sleep(0.02)
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        out(bar(done / total, size) + label)


def countdown(seconds: int) -> None:
    """Wait between automatic sends, with a bar that drains."""
    if seconds <= 0:
        return
    if not T.motion:
        para(f"Waiting {seconds}s before the next one...", "gray")
        time.sleep(seconds)
        return
    hide_cursor()
    size = max(10, width() - 16)
    end = time.time() + seconds
    left = seconds
    while left > 0:
        sys.stdout.write(f"\r{MARGIN}{paint('Next in', 'gray')} {bar(left / seconds, size)} {paint(f'{math.ceil(left)}s', 'gray')}\x1b[K")
        sys.stdout.flush()
        time.sleep(0.05)
        left = end - time.time()
    sys.stdout.write("\r\x1b[K")
    sys.stdout.flush()


class Spinner:
    """An animated 'working on it...' line.

        with Spinner("Starting Chrome", announce=True) as spin:
            ...
            spin.ok("Chrome is ready")

    Without animations it stays silent, unless `announce` asks for a plain
    'Starting Chrome...' line (for things slow enough to need one).
    """

    def __init__(self, text: str, announce: bool = False):
        self.text = text
        self.announce = announce
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if T.motion:
            hide_cursor()
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        elif self.announce:
            para(f"{self.text}...", "gray")
        return self

    def _spin(self) -> None:
        frames = SPINNER_FRAMES[0 if T.fancy else 1]
        tick = 0
        while not self._stop.is_set():
            frame = paint(frames[tick // 2 % len(frames)], FIRE[tick // 3 % len(FIRE)] if T.rich else "yellow")
            sys.stdout.write(f"\r{MARGIN}{frame} {self.text}\x1b[K")
            sys.stdout.flush()
            tick += 1
            self._stop.wait(0.08)

    def _halt(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()

    def ok(self, text: str = "") -> None:
        self._halt()
        ok(text or self.text)

    def warn(self, text: str) -> None:
        self._halt()
        warn(text)

    def fail(self, text: str) -> None:
        self._halt()
        fail(text)

    def __exit__(self, *exc) -> bool:
        self._halt()
        return False


def read(prompt: str = "") -> str:
    show_cursor()
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise Quit from None


def prompt_arrow() -> str:
    return MARGIN + paint(icon("arrow"), "yellow", "bold") + " "


def pill(key: str) -> str:
    return paint(f" {key} ", "pill") if T.color else f"[{key}]"


def ask(label: str, default: str = "", hint: str = "", validate=None) -> str:
    out()
    out(paint(label, "bold"))
    if hint:
        para(hint, "gray")
    if default:
        para(f"Press Enter to keep: {default}", "gray")
    while True:
        raw = read(prompt_arrow()).strip() or default
        if not raw:
            fail("This one's needed.")
            continue
        if validate is None:
            return raw
        value, problem = validate(raw)
        if not problem:
            return value
        fail(problem)


def confirm(question: str, default: bool = True) -> bool:
    choices = paint("[Y/n]" if default else "[y/N]", "gray")
    *first, last = textwrap.wrap(question, width() - 7) or [""]
    while True:
        for line in first:
            out(paint(line, "bold"))
        raw = read(f"{MARGIN}{paint(last, 'bold')} {choices} ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def key_hints(pairs) -> None:
    """Print ' Enter  I sent it   r  reopen ...', wrapped to the screen width."""
    lines, line = [], ""
    for key, label in pairs:
        part = f"{pill(key)} {paint(label, 'gray')}"
        candidate = f"{line}  {part}" if line else part
        if line and dwidth(candidate) > width():
            lines.append(line)
            candidate = part
        line = candidate
    lines.append(line)
    reveal(lines)


def choose(pairs) -> str:
    out()
    key_hints(pairs)
    return read(prompt_arrow()).strip().lower()


def menu(options) -> str:
    """options: (key, label, note) tuples. Returns what the user typed."""
    out()
    reveal(f"{pill(key)}  {label}" + (f"  {paint(note, 'gray')}" if note else "") for key, label, note in options)
    out()
    return read(prompt_arrow()).strip().lower()


def pause(text: str = "Press Enter to go back") -> None:
    out()
    read(MARGIN + paint(text, "gray") + " ")


def when(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%b %d, %H:%M")
    except (TypeError, ValueError):
        return stamp or ""


# --------------------------------------------------------------------------
# Phone / computer helpers
# --------------------------------------------------------------------------

def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "")


def on_phone() -> bool:
    return (
        is_termux()
        or hasattr(sys, "getandroidapilevel")
        or sys.platform == "ios"
        or platform.machine().startswith(("iPhone", "iPad"))
    )


def selenium_available() -> bool:
    try:
        return importlib.util.find_spec("selenium") is not None
    except (ImportError, ValueError):
        return False


def resolve_method(method: str) -> str:
    """Turn the 'auto' setting into 'chrome' or 'link' for this device."""
    if method != "auto":
        return method
    return "chrome" if not on_phone() and selenium_available() else "link"


def snapchat_reachable(locale: str, timeout: float = 4) -> bool:
    request = urllib.request.Request(
        f"{HELP_CENTER}/hc/{locale}",
        method="HEAD",
        headers={"User-Agent": f"Mozilla/5.0 ({APP_NAME}/{VERSION})"},
    )
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True  # it answered, even if not with a 200
    except Exception:
        return False


def open_in_browser(url: str) -> bool:
    if is_termux() and shutil.which("termux-open-url"):
        result = subprocess.run(["termux-open-url", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    if sys.platform in ("darwin", "ios") and shutil.which("open"):  # a-Shell on iPhone
        return subprocess.run(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    return False


def copy_to_clipboard(text: str) -> bool:
    for module, func in (("pasteboard", "set_string"), ("clipboard", "set")):  # Pyto, Pythonista
        try:
            getattr(__import__(module), func)(text)
            return True
        except Exception:
            pass
    commands = []
    if is_termux():
        commands.append(["termux-clipboard-set"])  # needs the Termux:API app
    if sys.platform == "darwin":
        commands.append(["pbcopy"])
    elif os.name == "nt":
        commands.append(["clip"])
    else:
        commands += [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(
                command,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=True,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# --------------------------------------------------------------------------
# Saved data
# --------------------------------------------------------------------------

def default_data_path() -> Path:
    here = Path(__file__).resolve().parent
    folder = here if os.access(here, os.W_OK) else Path.home()
    return folder / DATA_FILE_NAME


class Store:
    """Your details, settings, saved streak list and history, in one JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self._reset()

    def _reset(self) -> None:
        self.profile = None
        self.settings = dict(DEFAULT_SETTINGS)
        self.saved = []
        self.history = []

    def load(self) -> str | None:
        """Load the file. Returns a warning to show if it couldn't be read."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(self.path.name + ".broken")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return f"Couldn't read your saved data ({exc}). Starting fresh; the old file is now {backup.name}."
        profile = data.get("profile")
        if isinstance(profile, dict) and all(profile.get(k) for k in ("username", "email", "phone")):
            self.profile = {k: str(profile[k]) for k in ("username", "email", "phone")}
        for key, value in (data.get("settings") or {}).items():
            if key in DEFAULT_SETTINGS and type(value) is type(DEFAULT_SETTINGS[key]):
                self.settings[key] = value
        if data.get("version", 1) < 2:  # 2.0 made auto-submit the default
            if self.settings["method"] == "link":
                self.settings["method"] = "auto"
            self.settings["auto_submit"] = True
        if self.settings["method"] not in METHODS:
            self.settings["method"] = DEFAULT_SETTINGS["method"]
        self.saved = [str(name) for name in data.get("saved_friends") or []]
        self.history = [entry for entry in data.get("history") or [] if isinstance(entry, dict)]
        return None

    def save(self) -> None:
        payload = {
            "app": APP_NAME,
            "version": DATA_VERSION,
            "profile": self.profile,
            "settings": self.settings,
            "saved_friends": self.saved,
            "history": self.history[-HISTORY_LIMIT:],
        }
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as exc:
            warn(f"Couldn't save to {self.path} ({exc}).")
            return
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._reset()

    def remember(self, friends) -> None:
        known = {name.lower() for name in self.saved}
        self.saved += [name for name in friends if name.lower() not in known]

    def log(self, friend: str, status: str, method: str) -> None:
        self.history.append({
            "friend": friend,
            "status": status,
            "method": method,
            "at": datetime.now().isoformat(timespec="minutes"),
        })
        del self.history[:-HISTORY_LIMIT]

    def sent_counts(self):
        sent = [e for e in self.history if e.get("status") == "submitted"]
        today = date.today().isoformat()
        return len(sent), sum(1 for e in sent if str(e.get("at", "")).startswith(today))


def engine_label(store: Store) -> str:
    method = resolve_method(store.settings["method"])
    label = METHODS[method][0]
    if method == "chrome":
        label += ", auto-submit " + ("on" if store.settings["auto_submit"] else "off")
    return label


# --------------------------------------------------------------------------
# Fill methods
# --------------------------------------------------------------------------

class LinkRunner:
    """Opens a pre-filled copy of the form in the default browser."""

    name = "link"
    auto = False

    def __init__(self, store: Store):
        self.store = store

    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def restore(self, friend: str) -> str:
        url = form_url(self.store.settings["locale"], self.store.profile, friend)
        self._open(url)
        while True:
            key = choose([
                ("Enter", "I sent it"),
                ("r", "reopen"),
                ("c", "copy name"),
                ("i", "info"),
                ("s", "skip"),
                ("q", "stop"),
            ])
            if key in DONE_KEYS:
                return "submitted"
            if key == "r":
                self._open(url)
            elif key == "c":
                if copy_to_clipboard(friend):
                    ok(f"Copied {friend}")
                else:
                    warn(f"Couldn't reach the clipboard here. The username is: {friend}")
            elif key == "i":
                self._info(friend, url)
            elif key == "s":
                return "skipped"
            elif key == "q":
                return "stop"

    def _open(self, url: str) -> None:
        with Spinner("Opening the form") as spin:
            opened = open_in_browser(url)
            linger(0.3)
            if opened:
                spin.ok("Opened the form with everything filled in.")
        if opened:
            para("Check it, tap Submit, then come back here.", "gray")
        else:
            warn("Couldn't open your browser automatically. Open this link:")
            print(url)

    def _info(self, friend: str, url: str) -> None:
        p = self.store.profile
        out()
        para("If some boxes on the form are empty, type these in:", "gray")
        box([
            f"Your username: {p['username']}",
            f"Email: {p['email']}",
            f"Phone: {p['phone']}",
            f"Friend's username: {friend}",
        ], title="Form answers", style="cyan")
        para("Link to the filled-in form:", "gray")
        print(url)


class ChromeRunner:
    """Drives a real Chrome window with Selenium, types into the form and submits it."""

    name = "chrome"

    def __init__(self, store: Store):
        self.store = store
        self.driver = None
        self.sel = None
        self.headless = store.settings["headless"] or is_termux()
        self.auto = store.settings["auto_submit"] or self.headless

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        try:
            self.sel = _load_selenium()
        except ImportError:
            fail("Chrome autopilot needs Selenium.")
            para("Install it with:  pip install selenium", "gray")
            return False
        return self._launch()

    def _launch(self) -> bool:
        sel = self.sel
        options = sel.webdriver.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        driver_path = None
        if is_termux():  # Termux: pkg install x11-repo && pkg install chromium
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            browser = shutil.which("chromium-browser") or shutil.which("chromium")
            if browser:
                options.binary_location = browser
            driver_path = shutil.which("chromedriver")
        with Spinner("Starting Chrome", announce=True) as spin:
            try:
                service = sel.Service(executable_path=driver_path) if driver_path else sel.Service()
                self.driver = sel.webdriver.Chrome(options=options, service=service)
            except Exception as exc:
                spin.fail(f"Chrome didn't start: {_short_error(exc)}")
                para("Make sure Google Chrome is installed and Selenium is up to date (pip install -U selenium).", "gray")
                return False
            spin.ok("Chrome is ready")
        return True

    def _alive(self) -> bool:
        try:
            self.driver.current_url
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # -- one friend --------------------------------------------------------

    def restore(self, friend: str) -> str:
        filled = self._fill(friend)
        if filled == "ok" and self.auto:
            if self._submit():
                ok("Sent to Snapchat Support!")
                return "submitted"
            warn("Snapchat didn't take it automatically. It may want a quick check (like a captcha) or something needs fixing.")
        if self.headless:
            para("Chrome is hidden, so this one can't be finished by hand. Turn off 'Hidden Chrome window' in Settings to see it.", "gray")
            return "failed"
        if filled != "error":
            para("Check the Chrome window, click Submit, then come back here.", "gray")
        return self._wait_for_user(friend)

    def _wait_for_user(self, friend: str) -> str:
        while True:
            key = choose([("Enter", "I sent it"), ("r", "refill"), ("s", "skip"), ("q", "stop")])
            if key in DONE_KEYS:
                if self._form_still_open() and not confirm("The form still looks unsent. Count it as sent anyway?", default=False):
                    continue
                return "submitted"
            if key == "r" and self._fill(friend) == "ok" and self.auto:
                if self._submit():
                    ok("Sent to Snapchat Support!")
                    return "submitted"
                warn("Still not sent. Finish it in the Chrome window.")
            elif key == "s":
                return "skipped"
            elif key == "q":
                return "stop"

    def _fill(self, friend: str) -> str:
        """Load the form and type everything in: 'ok', 'partial' or 'error'."""
        sel = self.sel
        if not self._alive() and not self._launch():
            return "error"
        driver = self.driver
        with Spinner("Opening the form", announce=True) as spin:
            try:
                driver.get(form_url(self.store.settings["locale"], self.store.profile, friend))
                sel.Wait(driver, 25).until(
                    lambda d: d.find_elements(sel.By.NAME, field_name("friend"))
                    or d.find_elements(sel.By.CSS_SELECTOR, "form#new_request")
                )
            except sel.Timeout:
                spin.fail("The form didn't load in time. Check your internet connection.")
                return "error"
            except sel.DriverError as exc:
                spin.fail(f"Chrome had a problem: {_short_error(exc)}")
                return "error"
        self._accept_cookies()

        missing = []
        with Spinner("Typing in your details"):
            for key, value in field_values(self.store.profile, friend).items():
                element = self._find(field_name(key))
                if element is None:
                    missing.append(key)
                    continue
                try:
                    element.clear()
                    element.send_keys(value)
                except sel.DriverError:
                    missing.append(key)
            for element in driver.find_elements(sel.By.NAME, "request[anonymous_requester_email]"):
                try:  # Zendesk's built-in "your email" box, if this form shows one
                    if element.is_displayed() and not element.get_attribute("value"):
                        element.send_keys(self.store.profile["email"])
                except sel.DriverError:
                    pass
        if missing:
            warn(f"Couldn't fill: {', '.join(missing)}. Snapchat may have changed the form.")
            para("Fill those in the Chrome window, and see 'If the form changes' in the README.", "gray")
            return "partial"
        ok("Filled in the form.")
        return "ok"

    def _submit(self) -> bool:
        with Spinner("Sending to Snapchat Support", announce=True):
            return self._click_submit_and_wait()

    def _click_submit_and_wait(self) -> bool:
        sel = self.sel
        driver = self.driver
        field = self._find(field_name("friend"))
        button = self._submit_button()
        if field is None or button is None:
            return False
        try:
            button.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", button)
            except Exception:
                return False
        try:
            sel.Wait(driver, 25).until(sel.EC.staleness_of(field))
        except sel.Timeout:
            return "success" in driver.current_url
        except sel.DriverError:
            return False
        # Left the page. If the form comes straight back, Snapchat bounced it.
        try:
            sel.Wait(driver, 3).until(lambda d: d.find_elements(sel.By.NAME, field_name("friend")))
            return "success" in driver.current_url
        except sel.Timeout:
            return True
        except sel.DriverError:
            return False

    # -- page helpers --------------------------------------------------------

    def _find(self, name: str):
        try:
            found = self.driver.find_elements(self.sel.By.NAME, name)
        except Exception:
            return None
        return found[0] if found else None

    def _submit_button(self):
        for css in ('form#new_request footer [type="submit"]', 'form#new_request [type="submit"]', 'form [type="submit"]'):
            for element in self.driver.find_elements(self.sel.By.CSS_SELECTOR, css):
                try:
                    if element.is_displayed():
                        return element
                except Exception:
                    continue
        return None

    def _accept_cookies(self) -> None:
        for element in self.driver.find_elements(self.sel.By.ID, "onetrust-accept-btn-handler"):
            try:
                if element.is_displayed():
                    element.click()
            except Exception:
                pass

    def _form_still_open(self) -> bool:
        if not self._alive():
            return False
        return self._find(field_name("friend")) is not None and "success" not in self.driver.current_url


def _load_selenium():
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait

    return SimpleNamespace(
        webdriver=webdriver,
        Service=Service,
        By=By,
        EC=expected_conditions,
        Wait=WebDriverWait,
        Timeout=TimeoutException,
        DriverError=WebDriverException,
    )


def _short_error(exc: Exception) -> str:
    text = (getattr(exc, "msg", None) or str(exc) or type(exc).__name__).strip()
    return text.splitlines()[0].split("; For documentation")[0][:160]


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def boot(store: Store) -> None:
    """Animated intro plus a quick check that everything's ready."""
    online = []  # checked in the background while the logo animates
    check = threading.Thread(target=lambda: online.append(snapchat_reachable(store.settings["locale"])), daemon=True)
    check.start()
    clear()
    header(intro=True)
    out()
    with Spinner("Loading your profile") as spin:
        linger(0.35)
        spin.ok(f"Profile: {store.profile['username']}" if store.profile else "New here? Let's set you up.")
    with Spinner("Connecting to Snapchat Support", announce=True) as spin:
        check.join()
        if online and online[0]:
            spin.ok("Snapchat Support is online")
        else:
            spin.warn("Can't reach help.snapchat.com right now. Check your internet.")
    with Spinner("Starting the fill engine") as spin:
        linger(0.35)
        spin.ok(f"Fill engine: {engine_label(store)}")
    linger(0.6)


def setup_profile(store: Store) -> None:
    old = store.profile or {}
    section("Your details")
    para("Snapchat uses these to find your account. They're saved on this device only, so you only type them once.", "gray")
    while True:
        username = ask("Your Snapchat username", old.get("username", ""), validate=check_username)
        email = ask("Email address on your Snapchat account", old.get("email", ""), validate=check_email)
        phone = ask(
            "Phone number on your Snapchat account",
            old.get("phone", ""),
            hint="Include your country code, e.g. +1 555 123 4567",
            validate=check_phone,
        )
        profile = {"username": username, "email": email, "phone": phone}
        out()
        box([f"Username: {username}", f"Email: {email}", f"Phone: {phone}"], title="Your details", style="cyan")
        if confirm("All correct?"):
            break
        old = profile
    store.profile = profile
    with Spinner("Saving") as spin:
        store.save()
        linger(0.3)
        spin.ok("Saved.")


def collect_friends(store: Store, existing=()):
    """Ask for the friends' usernames. Returns the list, or None to go back."""
    friends = list(existing)
    own = store.profile["username"].lower()
    section("Lost streaks")
    para("Type the username of each friend you lost a Streak with. One per line, or several separated by commas.")
    para("Press Enter on an empty line when you're done.", "gray")
    out()
    while True:
        raw = read(MARGIN + paint(f"{len(friends) + 1:>2} {icon('arrow')}", "yellow", "bold") + " ")
        if not raw.strip():
            if friends:
                return friends
            para("Add at least one username, or type b to go back.", "gray")
            continue
        if raw.strip().lower() == "b" and not friends:
            return None
        tokens = split_usernames(raw)
        for token in tokens:
            name, problem = check_username(token)
            if problem:
                fail(f"{token}: {problem}")
            elif name.lower() == own:
                warn("That's your own username. Type your friend's.")
            elif name.lower() in {f.lower() for f in friends}:
                para(f"{name} is already on the list.", "gray")
            else:
                friends.append(name)
                if len(tokens) > 1 or name != token.strip():
                    ok(f"Added {name}")


def review(store: Store, friends: list, from_saved: bool = False):
    """Show the list before starting. Returns the final list, or None."""
    friends = list(friends)
    while True:
        section("Ready to restore")
        reveal(f"{paint(str(n).rjust(2), 'gray')}  {icon('fire')} {name}" for n, name in enumerate(friends, 1))
        if not friends:
            para("The list is empty.", "gray")
        out()
        para(f"Fill method: {engine_label(store)}", "gray")
        if resolve_method(store.settings["method"]) == "link" and not on_phone() and not selenium_available():
            para("Tip: run 'pip install selenium' and the app will press Submit for you.", "gray")
        pairs = [("Enter", "start"), ("a", "add"), ("d 2", "remove #2"), ("b", "back")]
        if not friends:
            pairs = pairs[1:2] + pairs[3:]
        key = choose(pairs)
        if key == "" and friends:
            return friends
        if key == "a":
            more = collect_friends(store, friends)
            if more is not None:
                friends = more
        elif key in ("b", "q"):
            return None
        else:
            removal = re.fullmatch(r"d\s*(\d+)", key)
            if removal and 1 <= int(removal.group(1)) <= len(friends):
                gone = friends.pop(int(removal.group(1)) - 1)
                ok(f"Removed {gone}")
                if from_saved:
                    store.saved = [n for n in store.saved if n.lower() != gone.lower()]
                    store.save()


def run_restore(store: Store, friends: list) -> None:
    method = resolve_method(store.settings["method"])
    runner = ChromeRunner(store) if method == "chrome" else LinkRunner(store)
    if not runner.start():
        if not confirm("Use the pre-filled link method instead?"):
            return
        runner = LinkRunner(store)

    results = {"submitted": [], "skipped": [], "failed": []}
    left_over = []
    shown = None
    try:
        for number, friend in enumerate(friends, 1):
            section(f"Streak {number} of {len(friends)}")
            progress(number - 1, len(friends), shown)
            shown = number - 1
            typewrite(MARGIN, f"{icon('fire')} {friend}", "yellow", "bold")
            status = runner.restore(friend)
            if status == "stop":
                left_over = friends[number - 1:]
                break
            results[status].append(friend)
            store.log(friend, status, runner.name)
            store.save()
            if status == "submitted" and runner.auto and number < len(friends):
                countdown(store.settings["delay"])
    finally:
        runner.close()

    section("Done" if not left_over else "Stopped")
    progress(len(friends) - len(left_over), len(friends), shown)
    rows = []
    for status, names in results.items():
        if names:
            symbol, color, word = STATUS_LOOK[status]
            rows.append((f"{icon(symbol)} {len(names)} {word}: {', '.join(names)}", color))
    if left_over:
        rows.append((f"{icon('warn')} {len(left_over)} not done: {', '.join(left_over)}", "yellow"))
    box(rows or ["Nothing was sent."], title="Summary")
    sent = len(results["submitted"])
    if sent:
        out()
        typewrite(MARGIN, f"{icon('fire')} {sent} streak request{'s' if sent != 1 else ''} sent!", "yellow", "bold")
        para("Snapchat Support replies by email. Your list is saved, so you can send it again any time with Quick restore.", "gray")
    pause("Press Enter for the menu")


def restore_flow(store: Store, friends=None, from_saved: bool = False) -> None:
    if friends is None:
        friends = collect_friends(store)
        if friends is None:
            return
    friends = review(store, friends, from_saved)
    if not friends:
        return
    if from_saved:
        store.saved = list(friends)
    else:
        store.remember(friends)
    store.save()
    run_restore(store, friends)


def history_screen(store: Store) -> None:
    section("History")
    if not store.history:
        para("Nothing sent yet.", "gray")
        pause()
        return
    total, today = store.sent_counts()
    para(f"{total} request{'s' if total != 1 else ''} sent, {today} today.", "gray")
    out()
    recent = store.history[-15:]
    name_width = min(18, max(dwidth(str(e.get("friend", ""))) for e in recent))
    lines = []
    for entry in reversed(recent):
        symbol, color, _ = STATUS_LOOK.get(entry.get("status"), ("warn", "yellow", "?"))
        friend = str(entry.get("friend", "?"))
        lines.append(f"{paint(icon(symbol), color)} {friend.ljust(name_width)} {paint(when(entry.get('at', '')), 'gray')}")
    reveal(lines)
    pause()


def settings_screen(store: Store) -> None:
    while True:
        s = store.settings
        chrome = resolve_method(s["method"]) == "chrome"
        on_off = {True: "on", False: "off"}
        section("Settings")
        options = [("1", "Fill method:", METHODS[s["method"]][0])]
        if chrome:
            options += [
                ("2", "Auto-submit:", on_off[s["auto_submit"]]),
                ("3", "Wait between sends:", f"{s['delay']}s"),
                ("4", "Hidden Chrome window:", on_off[s["headless"]]),
            ]
        options += [
            ("5", "Help center language:", s["locale"]),
            ("6", "Colors:", s["colors"]),
            ("7", "Animations:", on_off[s["animations"]]),
            ("9", "Delete all my saved data", ""),
            ("0", "Back", ""),
        ]
        key = menu(options)
        if key in ("0", "b", "q", ""):
            return
        if key == "1":
            pick_method(store)
        elif key == "2" and chrome:
            s["auto_submit"] = not s["auto_submit"]
            if s["auto_submit"]:
                para("Chrome will press Submit for you, waiting between friends so Snapchat isn't flooded.", "gray")
        elif key == "3" and chrome:
            s["delay"] = int(ask("Seconds to wait between automatic sends", str(s["delay"]), validate=_check_delay))
        elif key == "4" and chrome:
            s["headless"] = not s["headless"]
            if s["headless"]:
                para("Chrome will run out of sight and always auto-submit. If Snapchat asks for a captcha you won't see it, so turn this off if requests fail.", "gray")
        elif key == "5":
            s["locale"] = ask(
                "Help center language",
                s["locale"],
                hint="The part after /hc/ in Snapchat help links, e.g. en-us, en-gb, fr-fr, de-de.",
                validate=_check_locale,
            )
        elif key == "6":
            s["colors"] = {"auto": "on", "on": "off", "off": "auto"}[s["colors"]]
            setup_terminal(s["colors"], s["animations"])
        elif key == "7":
            s["animations"] = not s["animations"]
            setup_terminal(s["colors"], s["animations"])
        elif key == "9":
            out()
            para("This deletes your details, saved list, history and settings from this device.", "yellow")
            if read(MARGIN + paint("Type DELETE to confirm:", "bold") + " ").strip() == "DELETE":
                store.delete()
                ok("Everything's deleted.")
                pause("Press Enter to start over")
                return
            para("Nothing deleted.", "gray")
            continue
        store.save()


def pick_method(store: Store) -> None:
    section("Fill method")
    keys = list(METHODS)
    lines = []
    for number, key in enumerate(keys, 1):
        label, about = METHODS[key]
        current = "  (current)" if key == store.settings["method"] else ""
        lines.append(f"{pill(str(number))}  {paint(label, 'bold')}{paint(current, 'gray')}")
        lines += ["     " + paint(line, "gray") for line in textwrap.wrap(about, width() - 5)]
    reveal(lines)
    out()
    choice = read(prompt_arrow()).strip()
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        store.settings["method"] = keys[int(choice) - 1]
        ok(f"Using {METHODS[store.settings['method']][0]}.")


def _check_delay(raw: str):
    if raw.isdigit() and 0 <= int(raw) <= 300:
        return raw, None
    return raw, "Pick a number of seconds from 0 to 300."


def _check_locale(raw: str):
    locale = raw.strip().lower()
    if re.fullmatch(r"[a-z]{2}(-[a-z0-9]{2,4})?", locale):
        return locale, None
    return locale, "Use a code like en-us or fr-fr."


def welcome(store: Store) -> None:
    out()
    box([
        "Get your lost Snapchat Streaks back through Snapchat's own support form:",
        "",
        "1. Enter your details (only the first time)",
        "2. Type the usernames of the friends you lost streaks with",
        "3. The app fills in the form for every friend and sends it",
    ], title="How it works", style="cyan")
    setup_profile(store)


def main_menu(store: Store) -> None:
    while True:
        if store.profile is None:
            clear()
            header()
            welcome(store)
            restore_flow(store)
            continue
        clear()
        header()
        out()
        out(f"Hey {paint(store.profile['username'], 'yellow', 'bold')} {icon('wave')}".rstrip())
        total, today = store.sent_counts()
        if total:
            para(f"{total} request{'s' if total != 1 else ''} sent so far, {today} today.", "gray")
        saved_note = f"{len(store.saved)} saved" if store.saved else "none saved yet"
        key = menu([
            ("1", "Restore streaks", ""),
            ("2", "Quick restore", saved_note),
            ("3", "My details", ""),
            ("4", "History", ""),
            ("5", "Settings", ""),
            ("0", "Exit", ""),
        ])
        if key == "1":
            restore_flow(store)
        elif key == "2":
            if store.saved:
                restore_flow(store, store.saved, from_saved=True)
            else:
                para("No saved list yet. Use Restore streaks first and it'll be remembered.", "gray")
                pause()
        elif key == "3":
            setup_profile(store)
        elif key == "4":
            history_screen(store)
        elif key == "5":
            settings_screen(store)
        elif key in ("0", "q", "exit"):
            raise Quit


def print_links(store: Store, friends: list) -> None:
    for friend in friends:
        print(f"{friend}:")
        print(form_url(store.settings["locale"], store.profile, friend))
        print()


# --------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Sends Snapchat streak restore requests for you through Snapchat's support form.",
    )
    parser.add_argument("friends", nargs="*", metavar="USERNAME", help="friends to restore streaks with (skips typing them in)")
    parser.add_argument("--links", action="store_true", help="just print a pre-filled form link for each friend and exit")
    parser.add_argument("--method", choices=sorted(METHODS), help="fill method to use (remembered for next time)")
    parser.add_argument("--data", type=Path, help=f"where to keep your saved data (default: {DATA_FILE_NAME} next to this script)")
    parser.add_argument("--no-color", action="store_true", help="plain text, no colors")
    parser.add_argument("--no-anim", action="store_true", help="no animations")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    store = Store(args.data or default_data_path())
    problem = store.load()
    setup_terminal("off" if args.no_color else store.settings["colors"], store.settings["animations"] and not args.no_anim)
    atexit.register(show_cursor)
    if problem:
        warn(problem)
    if args.method:
        store.settings["method"] = args.method
        store.save()
    try:
        friends = []
        for token in args.friends:
            for piece in split_usernames(token):
                name, bad = check_username(piece)
                if bad:
                    fail(f"{piece}: {bad}")
                elif name.lower() not in {f.lower() for f in friends}:
                    friends.append(name)

        if args.links:
            if store.profile is None:
                welcome(store)
            print_links(store, friends or collect_friends(store) or [])
            return 0

        first_time = store.profile is None
        boot(store)
        if first_time:
            welcome(store)
        if friends:
            restore_flow(store, friends)
        elif first_time:
            restore_flow(store)
        main_menu(store)
    except (Quit, KeyboardInterrupt):
        show_cursor()
        print()
        out(paint(f"Bye! Good luck with your streaks {icon('fire')}".strip(), "yellow"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
