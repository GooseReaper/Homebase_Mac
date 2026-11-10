#!/bin/zsh
# Start Homebase from Finder or Terminal. The first run sets up a private Python
# environment in .venv and installs the packages in requirements.txt (needs internet once).
cd -- "${0:A:h}" || exit 1
fail() { print "$1"; read '?Press Return to close.'; exit 1; }
command -v python3 >/dev/null 2>&1 || fail 'Python 3 was not found. Install Python for macOS from https://www.python.org/downloads/macos/'
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || fail 'Homebase needs Python 3.10 or newer (3.11+ recommended). Install it from python.org.'
python3 -c 'import tkinter' 2>/dev/null || fail 'This Python has no Tkinter. Install Python for macOS from https://www.python.org/downloads/macos/'
if [[ ! -x .venv/bin/python ]]; then
    print 'Setting up Homebase (first run only)…'
    python3 -m venv .venv || fail 'Could not create the .venv folder.'
fi
if [[ ! -f .venv/.installed || requirements.txt -nt .venv/.installed ]]; then
    print 'Installing Homebase packages…'
    .venv/bin/python -m pip install --quiet --disable-pip-version-check 'cryptography>=42' 'Pillow>=10' certifi \
        || fail 'Could not install the required packages. Check your internet connection and try again.'
    .venv/bin/python -m pip install --quiet --disable-pip-version-check 'qrcode>=7' tkinterdnd2-universal \
        || print 'Optional extras (QR codes, drag and drop) did not install. Homebase still works without them.'
    touch .venv/.installed
fi
.venv/bin/python app.py
homebase_exit=$?
if (( homebase_exit != 0 )); then
    print ''
    print 'Homebase could not start. Copy the error above into our chat.'
    read '?Press Return to close.'
fi
exit "$homebase_exit"
