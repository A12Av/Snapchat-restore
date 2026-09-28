# 👻 Snap Streak Restore 🔥

A small Python app that fills in Snapchat's **official "Restore my Streak" support form**
for you:
<https://help.snapchat.com/hc/en-us/requests/new?co=true&ticket_form_id=149423>

1. **First run:** it asks for your Snapchat **username**, **email** and **phone number**, then saves them.
2. You type the **usernames of the friends** you lost streaks with.
3. For each friend it opens the form with **every box already filled in**. You check it and tap **Submit**.

It runs on your phone (Android or iPhone) and on computers. It only needs Python, with no extra installs.

```
  ╭───────────────────────────────────────╮
  │      👻  SNAP STREAK RESTORE  🔥      │
  │   Snapchat's official restore form,   │
  │           filled in for you           │
  ╰───────────────────────────────────────╯

  Hey my_snap 👋
  4 requests sent so far, 2 today.

   1  Restore streaks
   2  Quick restore  3 saved
   3  My details
   4  History
   5  Settings
   0  Exit
```

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
3. Next time you only need:
   ```sh
   cd Snapchat-restore && python streak_restore.py
   ```

*Optional:* to make the **copy name** button work, install the **Termux:API** app and run `pkg install termux-api`.

### Android: Pydroid 3

1. Install **Pydroid 3** from the Play Store.
2. Download `streak_restore.py` to your phone.
3. Open it in Pydroid 3 and press the yellow ▶ button.

### iPhone: a-Shell (free) or Pyto

1. Save `streak_restore.py` into the **Files** app (for example *On My iPhone → a-Shell*).
2. **a-Shell:** open it and type `python3 streak_restore.py`.
   **Pyto:** open the file in Pyto and press ▶.

> If the browser doesn't open by itself, the app prints the link. Tap it or copy it into Safari/Chrome.

## Run it on a computer

```sh
python streak_restore.py        # Windows: py streak_restore.py
```

### Chrome autopilot (computers only)

Instead of opening links, the app can type everything into a Chrome window and even press Submit for you:

```sh
pip install selenium
python streak_restore.py --method chrome
```

In **Settings** you can switch **Auto-submit** on. The app then presses Submit itself and waits a few seconds
between friends. If Snapchat shows a "prove you're human" check, the app stops and lets you finish it
in the window. It never tries to get around those checks.

---

## How to use it

**Typing usernames:** put one per line, or several separated by commas. `@name` and
`snapchat.com/add/name` links work too. Press **Enter on an empty line** when you're done.

**While restoring**, each friend shows these keys:

| Key     | What it does                                                 |
|---------|--------------------------------------------------------------|
| `Enter` | I submitted it, go to the next friend                        |
| `r`     | Reopen the form                                              |
| `c`     | Copy the friend's username                                   |
| `i`     | Show all the answers and the link (in case a box is empty)   |
| `s`     | Skip this friend                                             |
| `q`     | Stop                                                         |

**Quick restore** re-uses the list from last time, so you don't have to type the usernames
again. Remove friends from it with `d 2` (removes #2).

### Shortcuts

```sh
python streak_restore.py alex_23 sam.k jo_jo   # skip typing, go straight to these friends
python streak_restore.py --links alex_23       # just print the pre-filled link(s)
python streak_restore.py --no-color            # plain text
```

---

## Your data

Your details, saved list, history and settings are kept in `streak_restore_data.json`, next to the script,
**on your device only**. Nothing is sent anywhere except to Snapchat's form when you submit it.
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
