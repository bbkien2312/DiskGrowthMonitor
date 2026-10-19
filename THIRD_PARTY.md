# Third-party components

Dependencies are resolved and pinned in uv.lock. No third-party code is copied into the application source.

- Python: https://www.python.org/ — PSF license.
- uv / managed Python: https://github.com/astral-sh/uv and https://github.com/astral-sh/python-build-standalone — see their license notices.
- PySide6-Essentials / Shiboken6: https://doc.qt.io/qtforpython-6/ — LGPL/GPL/commercial alternatives; distribution must retain required notices and permit the applicable library replacement/relinking obligations.
- Typer: https://github.com/fastapi/typer — MIT.
- psutil: https://github.com/giampaolo/psutil — BSD.
- Send2Trash: https://github.com/arsenetar/send2trash — BSD.
- APScheduler: https://github.com/agronholm/apscheduler — MIT.
- ijson: https://github.com/ICRAR/ijson — see license included in the package; used for streaming large exports.
- DuckDB: https://github.com/duckdb/duckdb — MIT; Parquet reader/query engine in private memory, pinned in uv.lock.
- NumPy: https://github.com/numpy/numpy — BSD-3-Clause; dependency of the DuckDB Python UDF fallback. Retain its bundled library notices too.
- python-dateutil: https://github.com/dateutil/dateutil — dual BSD/Apache.
- pywin32: https://github.com/mhammond/pywin32 — see upstream license.
- PyInstaller: https://github.com/pyinstaller/pyinstaller — GPL with bootloader exception; inspect current distribution requirements.
- Optional WinSW: https://github.com/winsw/winsw — MIT; download and source/version/checksum are recorded when explicit service setup is requested.
- WizTree is proprietary and NOT redistributed by this source project. User-selected installed executable is invoked. Bundling/distribution requires applicable permission: https://diskanalyzer.com/distribution-license.
- Optional WinDirStat portable: https://github.com/windirstat/windirstat — GPL-3.0-or-later; retain license and source availability obligations when redistributed.
- Optional gdu: https://github.com/dundee/gdu — MIT.

This file does not replace the licenses included in the installed dependency packages. Release packaging must collect those licenses.

Release 1.0.0 keeps Python and dependency notices under `.venv/licenses`, including an INDEX.json identifying distribution versions. Qt/PySide libraries remain shared, replaceable files in the private runtime (not statically linked or encrypted). Corresponding upstream source: https://code.qt.io/ and https://code.qt.io/pyside/pyside-setup.git/ (match the shipped versions in INDEX.json). The public application source/build scripts allow rebuilding the bundle; this does not grant a different license to third-party libraries.

Installer builder only: Inno Setup 6.7.3, https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3. The compiler is not shipped as an application dependency. Its bootstrap SHA-256 is pinned in build-installer.ps1. Installer and application binaries in this release are not code-signed.

