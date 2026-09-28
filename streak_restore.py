#!/usr/bin/env python3
"""Snap Streak Restore - fills in Snapchat's streak restore form for you.

Type your Snapchat username, email and phone number once, then the usernames
of the friends you lost streaks with. For each friend the tool opens
Snapchat's official "restore my Streak" support form with every field already
filled in, so all that's left is tapping Submit:

    https://help.snapchat.com/hc/en-us/requests/new?co=true&ticket_form_id=149423

Runs anywhere Python 3.8+ does, phones included:
    Android   Termux or Pydroid 3
    iPhone    a-Shell, Pyto or Pythonista
    Computer  Windows, macOS or Linux

Only the standard library is needed. The optional "Chrome autopilot" fill
method (computers) also needs Selenium:  pip install selenium
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import unicodedata
import webbrowser
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

APP_NAME = "Snap Streak Restore"
VERSION = "1.0.0"

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
    "link": (
        "Pre-filled link",
        "Opens the form in your browser with everything already filled in. "
        "Works on phones and computers.",
    ),
    "chrome": (
        "Chrome autopilot",
        "Types everything into the form in a Chrome window. "
        "Computers only, needs Selenium (pip install selenium).",
    ),
}

DEFAULT_SETTINGS = {
    "method": "link",
    "auto_submit": False,
    "headless": False,
    "delay": 10,
    "locale": "en-us",
    "colors": "auto",
}

DATA_FILE_NAME = "streak_restore_data.json"
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
    fancy = True  # Unicode box lines and emoji


T = Theme()
MARGIN = "  "

_CODES = {
    "bold": "1",
    "red": "91",
    "green": "92",
    "yellow": "93",
    "cyan": "96",
    "gray": "90",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

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


def paint(text: str, *styles: str) -> str:
    if not T.color or not styles:
        return text
    return "\x1b[" + ";".join(_CODES[s] for s in styles) + "m" + text + "\x1b[0m"


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


def setup_terminal(colors: str = "auto") -> None:
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
    T.fancy = _stdout_can_encode("╭─╮│╰╯›·✓✗↷👻🔥")
    if colors in ("on", "off"):
        T.color = colors == "on"
    elif os.environ.get("NO_COLOR"):
        T.color = False
    elif os.environ.get("FORCE_COLOR"):
        T.color = True
    else:
        T.color = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def width() -> int:
    """Width to draw at: fits a phone held upright, doesn't sprawl on a PC."""
    columns = shutil.get_terminal_size((60, 24)).columns
    return max(30, min(columns - 3, 58))


def out(text: str = "") -> None:
    print(MARGIN + text if text else "")


def para(text: str, *styles: str) -> None:
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
        print("\x1b[2J\x1b[H", end="")


def box(rows, title: str = "", style: str = "yellow", center: bool = False) -> None:
    """Draw a rounded box. Rows are strings or (text, *styles) tuples."""
    w = width()
    inner = w - 4
    h, v, tl, tr, bl, br = ("─", "│", "╭", "╮", "╰", "╯") if T.fancy else ("-", "|", "+", "+", "+", "+")
    if title:
        label = f" {title} "
        out(paint(tl + h, style) + paint(label, style, "bold") + paint(h * max(0, w - 3 - dwidth(label)) + tr, style))
    else:
        out(paint(tl + h * (w - 2) + tr, style))
    for row in rows:
        text, styles = (row, ()) if isinstance(row, str) else (row[0], row[1:])
        bullet = re.match(r"\s*(?:\d+\.|-)\s+", text)
        hang = " " * len(bullet.group(0)) if bullet else ""
        for piece in textwrap.wrap(text, inner, subsequent_indent=hang) or [""]:
            gap = max(0, inner - dwidth(piece))
            left = gap // 2 if center else 0
            line = " " * left + paint(piece, *styles) + " " * (gap - left)
            out(paint(v, style) + " " + line + " " + paint(v, style))
    out(paint(bl + h * (w - 2) + br, style))


def banner() -> None:
    title = f"{icon('ghost')}  SNAP STREAK RESTORE  {icon('fire')}".strip()
    box(
        [
            (title, "yellow", "bold"),
            ("Snapchat's official restore form,", "gray"),
            ("filled in for you", "gray"),
        ],
        center=True,
    )


def section(title: str) -> None:
    h = "─" if T.fancy else "-"
    out()
    out(paint(h * 2, "yellow") + " " + paint(title, "bold") + " " + paint(h * max(0, width() - dwidth(title) - 4), "yellow"))


def read(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise Quit from None


def prompt_arrow() -> str:
    return MARGIN + paint(icon("arrow"), "yellow", "bold") + " "


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
    choices = "Y/n" if default else "y/N"
    while True:
        raw = read(f"{MARGIN}{paint(question, 'bold')} {paint('[' + choices + ']', 'gray')} ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def key_hints(pairs) -> None:
    """Print 'Enter sent it · r reopen · ...', wrapped to the screen width."""
    sep = f" {paint(icon('dot'), 'gray')} "
    lines, line = [], ""
    for key, label in pairs:
        part = f"{paint(key, 'yellow', 'bold')} {label}"
        candidate = line + sep + part if line else part
        if line and dwidth(candidate) > width():
            lines.append(line)
            candidate = part
        line = candidate
    lines.append(line)
    for text in lines:
        out(text)


def choose(pairs) -> str:
    out()
    key_hints(pairs)
    return read(prompt_arrow()).strip().lower()


def menu(options) -> str:
    """options: (key, label, note) tuples. Returns what the user typed."""
    out()
    for key, label, note in options:
        extra = f"  {paint(note, 'gray')}" if note else ""
        out(f"{paint(key.rjust(2), 'yellow', 'bold')}  {label}{extra}")
    out()
    return read(prompt_arrow()).strip().lower()


def pause(text: str = "Press Enter to go back") -> None:
    out()
    read(MARGIN + paint(text, "gray") + " ")


def countdown(seconds: int) -> None:
    for left in range(seconds, 0, -1):
        print(f"\r{MARGIN}{paint(f'Next one in {left}s...', 'gray')}   ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * (width() + len(MARGIN)) + "\r", end="", flush=True)


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
        if self.settings["method"] not in METHODS:
            self.settings["method"] = DEFAULT_SETTINGS["method"]
        self.saved = [str(name) for name in data.get("saved_friends") or []]
        self.history = [entry for entry in data.get("history") or [] if isinstance(entry, dict)]
        return None

    def save(self) -> None:
        payload = {
            "app": APP_NAME,
            "version": 1,
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
        if open_in_browser(url):
            ok("Opened the form with everything filled in.")
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
    """Drives a real Chrome window with Selenium and types into the form."""

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
        para("Starting Chrome...", "gray")
        try:
            service = sel.Service(executable_path=driver_path) if driver_path else sel.Service()
            self.driver = sel.webdriver.Chrome(options=options, service=service)
        except Exception as exc:
            fail(f"Chrome didn't start: {_short_error(exc)}")
            para("Make sure Google Chrome is installed and Selenium is up to date (pip install -U selenium).", "gray")
            return False
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
                ok("Sent!")
                return "submitted"
            warn("The form is still open. Snapchat may want a quick check (like a captcha) or something needs fixing.")
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
                    ok("Sent!")
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
        try:
            driver.get(form_url(self.store.settings["locale"], self.store.profile, friend))
            sel.Wait(driver, 25).until(
                lambda d: d.find_elements(sel.By.NAME, field_name("friend"))
                or d.find_elements(sel.By.CSS_SELECTOR, "form#new_request")
            )
        except sel.Timeout:
            fail("The form didn't load in time. Check your internet connection.")
            return "error"
        except sel.DriverError as exc:
            fail(f"Chrome had a problem: {_short_error(exc)}")
            return "error"
        self._accept_cookies()

        missing = []
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
            except Exception:
                pass
        if missing:
            warn(f"Couldn't fill: {', '.join(missing)}. Snapchat may have changed the form.")
            para("Fill those in the Chrome window, and see 'If the form changes' in the README.", "gray")
            return "partial"
        ok("Filled in the form.")
        return "ok"

    def _submit(self) -> bool:
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
    return text.splitlines()[0][:160]


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

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
    store.save()
    ok("Saved.")


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
        for number, name in enumerate(friends, 1):
            out(f"{paint(str(number).rjust(2), 'gray')}  {icon('fire')} {name}")
        if not friends:
            para("The list is empty.", "gray")
        out()
        para(f"Fill method: {METHODS[store.settings['method']][0]}", "gray")
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
    runner = ChromeRunner(store) if store.settings["method"] == "chrome" else LinkRunner(store)
    if not runner.start():
        if not confirm("Use the pre-filled link method instead?"):
            return
        runner = LinkRunner(store)

    results = {"submitted": [], "skipped": [], "failed": []}
    left_over = []
    try:
        for number, friend in enumerate(friends, 1):
            section(f"Streak {number} of {len(friends)}")
            out(f"{icon('fire')} {paint(friend, 'yellow', 'bold')}")
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
    rows = []
    for status, names in results.items():
        if names:
            symbol, color, word = STATUS_LOOK[status]
            rows.append((f"{icon(symbol)} {len(names)} {word}: {', '.join(names)}", color))
    if left_over:
        rows.append((f"{icon('warn')} {len(left_over)} not done: {', '.join(left_over)}", "yellow"))
    box(rows or ["Nothing was sent."], title="Summary")
    if results["submitted"]:
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
    para(f"{total} requests sent, {today} today.", "gray")
    out()
    name_width = min(18, max(dwidth(str(e.get("friend", ""))) for e in store.history[-15:]))
    for entry in reversed(store.history[-15:]):
        symbol, color, word = STATUS_LOOK.get(entry.get("status"), ("warn", "yellow", "?"))
        friend = str(entry.get("friend", "?"))
        out(f"{paint(icon(symbol), color)} {friend.ljust(name_width)} {paint(when(entry.get('at', '')), 'gray')}")
    pause()


def settings_screen(store: Store) -> None:
    while True:
        s = store.settings
        section("Settings")
        on_off = {True: "on", False: "off"}
        options = [
            ("1", "Fill method:", METHODS[s["method"]][0]),
            ("2", "Wait between auto-sends:", f"{s['delay']}s"),
            ("3", "Help center language:", s["locale"]),
            ("4", "Colors:", s["colors"]),
        ]
        if s["method"] == "chrome":
            options += [
                ("5", "Auto-submit:", on_off[s["auto_submit"]]),
                ("6", "Hidden Chrome window:", on_off[s["headless"]]),
            ]
        options += [("9", "Delete all my saved data", ""), ("0", "Back", "")]
        key = menu(options)
        if key in ("0", "b", "q", ""):
            return
        if key == "1":
            pick_method(store)
        elif key == "2":
            seconds = ask("Seconds to wait between automatic sends", str(s["delay"]), validate=_check_delay)
            s["delay"] = int(seconds)
        elif key == "3":
            s["locale"] = ask(
                "Help center language",
                s["locale"],
                hint="The part after /hc/ in Snapchat help links, e.g. en-us, en-gb, fr-fr, de-de.",
                validate=_check_locale,
            )
        elif key == "4":
            s["colors"] = {"auto": "on", "on": "off", "off": "auto"}[s["colors"]]
            setup_terminal(s["colors"])
        elif key == "5" and s["method"] == "chrome":
            s["auto_submit"] = not s["auto_submit"]
            if s["auto_submit"]:
                para("Chrome will press Submit for you, waiting between friends so Snapchat isn't flooded.", "gray")
        elif key == "6" and s["method"] == "chrome":
            s["headless"] = not s["headless"]
            if s["headless"]:
                para("Chrome will run out of sight and always auto-submit. If Snapchat asks for a captcha you won't see it, so turn this off if requests fail.", "gray")
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
    for number, key in enumerate(keys, 1):
        label, about = METHODS[key]
        current = "  (current)" if key == store.settings["method"] else ""
        out(f"{paint(str(number).rjust(2), 'yellow', 'bold')}  {paint(label, 'bold')}{paint(current, 'gray')}")
        for line in textwrap.wrap(about, width() - 4):
            out("    " + paint(line, "gray"))
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
    clear()
    banner()
    out()
    box([
        "Get your lost Snapchat Streaks back through Snapchat's own support form:",
        "",
        "1. Enter your details (only the first time)",
        "2. Type the usernames of the friends you lost streaks with",
        "3. The form opens already filled in. Tap Submit, done!",
    ], title="How it works", style="cyan")
    setup_profile(store)


def main_menu(store: Store) -> None:
    while True:
        if store.profile is None:
            welcome(store)
            restore_flow(store)
            continue
        clear()
        banner()
        out()
        greeting = f"Hey {paint(store.profile['username'], 'yellow', 'bold')}"
        out(f"{greeting} {icon('wave')}".rstrip())
        total, today = store.sent_counts()
        if total:
            para(f"{total} requests sent so far, {today} today.", "gray")
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
        description="Fills in Snapchat's streak restore support form for you.",
    )
    parser.add_argument("friends", nargs="*", metavar="USERNAME", help="friends to restore streaks with (skips typing them in)")
    parser.add_argument("--links", action="store_true", help="just print a pre-filled form link for each friend and exit")
    parser.add_argument("--method", choices=sorted(METHODS), help="fill method to use (remembered for next time)")
    parser.add_argument("--data", type=Path, help=f"where to keep your saved data (default: {DATA_FILE_NAME} next to this script)")
    parser.add_argument("--no-color", action="store_true", help="plain text, no colors")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    store = Store(args.data or default_data_path())
    problem = store.load()
    setup_terminal("off" if args.no_color else store.settings["colors"])
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

        first_time = store.profile is None
        if first_time:
            welcome(store)

        if args.links:
            print_links(store, friends or collect_friends(store) or [])
            return 0
        if friends:
            restore_flow(store, friends)
        elif first_time:
            restore_flow(store)
        main_menu(store)
    except (Quit, KeyboardInterrupt):
        print()
        out(paint(f"Bye! Good luck with your streaks {icon('fire')}".strip(), "yellow"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
