# Homebase v3

Homebase is a local home inventory for your Mac (Windows and Linux work too). There's no server and no online account. Each person signs in to their own encrypted inventory.

## Start it

Double-click **Start_Homebase.command** (on Windows, **Start_Homebase.bat**). The first run creates a private Python environment in `.venv` and installs the packages it needs. That needs internet once.

To start it by hand:

```sh
cd ~/Downloads/Homebase_Mac
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

On first launch you create an account. If an old `data/homebase.sqlite3` exists, you can import it, and Homebase can then delete the unencrypted copy.

## What's in v3

| Feature | How to use it |
| --- | --- |
| **Accounts** | Each account gets its own private inventory. Create as many as you like from the sign-in screen. |
| **Encryption** | Your inventory, photos, settings, and AI keys are stored in one AES-256-GCM encrypted file (`data/vaults/…hbx`). The key is protected by your password through scrypt, which is slow on purpose. Your recovery phrase is a second way to unlock it, the same idea as a Brave Sync code. |
| **Two-factor sign-in** | Settings → Account → Turn on two-factor. Scan the QR code with an authenticator app, then enter its 6-digit code. If you lose your phone, your recovery phrase resets your password and turns 2FA off. |
| **Recovery phrase** | Write your own (12+ words) or let Homebase generate one. Write it on paper. It's the only way back in if you forget your password. |
| **Auto-lock** | Locks after 10 minutes of no use. You can change this in Settings. ⌘⇧L locks right away. |
| **Browser-style tabs** | Open locations, items, searches, Settings, and the Assistant as tabs. ⌘T opens a new tab, ⌘W closes one, ⌘⇧T reopens the last one you closed, Ctrl+Tab moves to the next tab, and ⌘1–9 jumps to a tab. Each tab has its own Back/Forward and filters. Tabs come back when you sign in. ⌘-click or middle-click opens something in a new tab. |
| **Photos** | Drag photos onto the item list, an item, or the item form. You can also click the photo box to pick a file, paste with ⌘V, or use **Add items from photos…** to add several at once. |
| **AI (ChatGPT or Claude)** | Settings → AI: paste your own API key. Then you get: **Fill in with AI** from a photo, plain-English questions in the Assistant tab, **Suggest tags and better places**, and a chat that can propose changes. Every change waits for your OK and counts as one undo step. |
| **Themes** | Modern, Retro, Blossom (pink and lavender), and Voltage (black and neon green). |
| **Customization** | Text font, heading font, text size (70–200%), accent color, spacing, sidebar left/right/hidden, details panel left/right/hidden, tab bar top/bottom, summary cards on/off, and what double-click does. |
| **Undo / Redo** | Covers everything, including AI changes, imports, and Reset. Use ⌘Z / ⌘⇧Z, the header buttons, or Edit menu. |
| **Backups** | **Back up…** saves an encrypted `.hbbackup` file. It opens only with your password or recovery phrase, on any computer (Sign-in screen → Restore an account from a backup file). |

## Security notes

- Nothing leaves your computer unless you use an AI feature. When you do, only the item list goes out (names, categories, quantities, locations, and tags; notes and values only if you allow them), plus the one photo you asked about.
- Your password is the main protection. Two-factor guards the sign-in screen, but someone who has your password and a copy of the file could still decrypt it, so use a strong password.
- Usernames and the list of accounts are not secret. Everything else is encrypted.
- Exporting to CSV writes plain text, and Homebase warns you first.
- Developer mode (`python app.py --db somefile.sqlite3`) opens an **unencrypted** database with no sign-in.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite has 68 tests: data, undo/redo, encryption and accounts, 2FA, AI (with a fake network), and real-window tests for every theme and layout. The window tests skip themselves when no display is available.

## Files

- `app.py`: the main window, browser tabs, pages, Settings, and Assistant
- `inventory.py`: data rules, SQLite, undo/redo, and batches
- `security.py`: accounts, encryption, two-factor, recovery, and backups
- `ai.py`: the ChatGPT and Claude connections (standard library only)
- `themes.py`: the four themes and your appearance choices
- `ui_auth.py`, `ui_dialogs.py`, `ui_common.py`: sign-in screens, forms, and shared pieces
- `backup_before_v2/`, `backup_before_v3/`: earlier versions of the code

Created by Gregory Edison

## License

Copyright (c) 2026 Gregory Edison. All rights reserved. The code is public for viewing only; see LICENSE.
