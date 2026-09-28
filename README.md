# ABA Streak Restore 🔥

```
 █████╗ ██████╗  █████╗
██╔══██╗██╔══██╗██╔══██╗
███████║██████╔╝███████║
██╔══██║██╔══██╗██╔══██║
██║  ██║██████╔╝██║  ██║
╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝
 S T R E A K   R E S T O R E
```

A Python app that sends your requests through Snapchat's **official "Restore my Streak" support form**:
<https://help.snapchat.com/hc/en-us/requests/new?co=true&ticket_form_id=149423>

<p align="center"><img src="docs/demo.gif" width="420" alt="ABA Streak Restore sending three requests on its own"></p>
<p align="center"><sub>Preview on a computer with Chrome autopilot, recorded against a local test copy of the form.
Emoji don't show in this recording, but they do in a real terminal.</sub></p>

1. **First run:** it asks for your Snapchat **username**, **email** and **phone number**, then saves them.
2. You type the **usernames of the friends** you lost streaks with.
3. The app fills in the form for every friend:
   - **On a computer** (with Selenium): it types everything into Chrome **and presses Submit for you**,
     one friend after another.
   - **On a phone**: it opens the form with every box already filled in. You tap **Submit**.

It has an animated logo, loading spinners, progress bars and countdowns. Colors and animations
turn themselves off where they aren't supported.

---

## Why can't it send by itself on a phone?

Snapchat protects this form with a **captcha** (a "prove you're human" check). They added it to
stop apps from sending requests on their own. This app **doesn't try to get around it**:

- **Computer:** the app uses a real Chrome window. Usually it goes straight through. If Snapchat
  shows a check, the app pauses, you solve it in the window, and it continues.
- **Phone:** phones can't run that Chrome autopilot, so the app does everything up to the Submit button
  and you tap it.

---

## Run it on your phone

### Android: Termux (recommended)

1. Install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) or
   [GitHub](https://github.com/termux/termux-app/releases). The Play Store version is outdated.
2. Open Termux and run:
   ```sh
   pkg install python git
   git clone https://github.com/A12Av/Snapchat-restore
   cd Snapchat-restore
   python streak_restore.py
   ```
3. Next time you only need `cd Snapchat-restore && python streak_restore.py`.

*Optional:* to make the **copy name** button work, install the **Termux:API** app and run `pkg install termux-api`.

### Android: Pydroid 3

Download `streak_restore.py`, open it in **Pydroid 3** and press ▶.

### iPhone: a-Shell (free) or Pyto

Save `streak_restore.py` in the **Files** app. In **a-Shell**, type `python3 streak_restore.py`.
In **Pyto**, open the file and press ▶.

> If the browser doesn't open by itself, the app prints the link. Tap it or copy it into Safari/Chrome.

## Run it on a computer (fully automatic)

You need Google Chrome installed. Then:

```sh
pip install selenium
python streak_restore.py        # Windows: py streak_restore.py
```

When Selenium is installed, the app switches to **Chrome autopilot** by itself. It fills in each
form, presses Submit, then waits a few seconds before the next friend so Snapchat isn't flooded.
You can change this in **Settings**:

| Setting              | What it does                                                        |
|----------------------|---------------------------------------------------------------------|
| Fill method          | Automatic / Chrome autopilot / Pre-filled link                      |
| Auto-submit          | Let the app press Submit (on by default)                            |
| Wait between sends   | Seconds between friends (default 10)                                |
| Hidden Chrome window | Run Chrome out of sight (you can't solve captchas this way)         |
| Animations / Colors  | Turn the effects off if your screen shows odd symbols               |

---

## How to use it

**Typing usernames:** put one per line, or several separated by commas. `@name` and
`snapchat.com/add/name` links work too. Press **Enter on an empty line** when you're done.

**Quick restore** re-uses the list from last time, so you don't have to type it again. Remove someone
with `d 2` (removes #2).

On a phone, each friend shows these keys:

| Key     | What it does                                               |
|---------|------------------------------------------------------------|
| `Enter` | I submitted it, go to the next friend                      |
| `r`     | Reopen the form                                            |
| `c`     | Copy the friend's username                                 |
| `i`     | Show all the answers and the link (in case a box is empty) |
| `s`     | Skip this friend                                           |
| `q`     | Stop                                                       |

### Shortcuts

```sh
python streak_restore.py alex_23 sam.k jo_jo   # skip typing, go straight to these friends
python streak_restore.py --links alex_23       # just print the pre-filled link(s)
python streak_restore.py --method chrome       # choose the fill method
python streak_restore.py --no-anim --no-color  # plain and quick
```

---

## Your data

Your details, saved list, history and settings are kept in `streak_restore_data.json`, next to the script,
**on your device only**. Nothing is sent anywhere except to Snapchat's form. When the app starts,
it also does a quick check that help.snapchat.com is reachable.
The file is listed in `.gitignore`, so it won't be uploaded to GitHub by accident.
To remove it, go to **Settings → Delete all my saved data**.

## Good to know

- Snapchat decides whether to restore a Streak. This app only makes asking quicker.
- Send **one request per lost Streak**. Sending the same one many times won't speed it up.
- This is an unofficial helper and isn't made by or connected to Snap Inc.

## If the form changes

The app fills four boxes on Snapchat's form by their field numbers. These are set at the top of
`streak_restore.py`:

```python
FIELD_IDS = {
    "username": "24281229",
    "email": "24335325",
    "phone": "24369716",
    "friend": "24369736",
}
```

If boxes start coming up empty, Snapchat may have changed the form. To find the new numbers, open the form on a computer,
right-click a box, choose **Inspect**, and copy the number from `name="request[custom_fields][NUMBER]"`.

## Tests

```sh
python -m unittest discover -s tests
```
