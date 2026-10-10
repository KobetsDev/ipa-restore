# ipa-restore

**Restore an offloaded iPhone app from its App Store ID.**

One Python file that finds an app, downloads its IPA through your Apple Account, installs it over USB, and verifies the installed version. No Python packages, jailbreak, developer signing, or Bmrng installation required for the standard workflow.

```sh
python3 ipa_restore.py setup
python3 ipa_restore.py login
python3 ipa_restore.py restore 771700826
```

Built after successfully restoring **Russian Post 7.8.6** to a real iPhone using an existing Bmrng Apple session. The optional Bmrng adapter retains that tested path; the default backend is the independently maintained, open-source [ipatool](https://github.com/majd/ipatool).

## Quick start

Requirements: **macOS**, Python **3.10+**, an Apple Account that owns the app, and a USB-connected iPhone. Unlock the phone and accept **Trust This Computer** if prompted. Use the account that originally obtained the app.

```sh
git clone https://github.com/KobetsDev/ipa-restore.git
cd ipa-restore
python3 ipa_restore.py setup
python3 ipa_restore.py login

# Find the ID by name; search does not need Apple login.
python3 ipa_restore.py search "Почта России" --country ru

# Restore by numeric ID, App Store link, or a listed bundle identifier.
python3 ipa_restore.py restore 771700826
python3 ipa_restore.py restore 'https://apps.apple.com/ru/app/id771700826'
python3 ipa_restore.py restore ru.russianpost.Russian-Post --country ru
```

`login` delegates the interactive Apple Account, password and 2FA prompts to ipatool. Passwords are not command-line arguments. ipatool manages encrypted session storage and may ask for a keychain passphrase. Do not use `sudo` for Apple login.

`setup` downloads pinned **ipatool 2.7.0** and **go-ios 1.3.2** releases into `.ipa-restore/bin`, verifies their SHA-256 hashes, and extracts only the executables. It does not install global packages. Dependencies retain their own [ipatool MIT](https://github.com/majd/ipatool/blob/main/LICENSE) and [go-ios MIT](https://github.com/danielpaulus/go-ios/blob/main/LICENSE) licenses; binaries are not included in this repository.

## Other workflows

```sh
# Download without installing; a phone is not needed.
python3 ipa_restore.py restore 771700826 --download-only --output ./RussianPost.ipa

# Inspect an IPA, or install one you already downloaded.
python3 ipa_restore.py inspect ./RussianPost.ipa
python3 ipa_restore.py install ./RussianPost.ipa

# Several phones connected? Select one explicitly.
python3 ipa_restore.py devices
python3 ipa_restore.py restore 771700826 --udid YOUR_DEVICE_ID

# Public app information, including its bundle ID and minimum iOS version.
python3 ipa_restore.py lookup 771700826 --country ru

# A specific external version ID, if Apple still serves it.
python3 ipa_restore.py restore 771700826 --external-version-id VERSION_ID --output ./older.ipa
```

When exactly one phone is connected, it is selected automatically. With several phones, the tool refuses to guess. It checks the connected device before starting a download. Cached IPA files are checked against the requested App Store ID before reuse. Interrupted downloads keep the backend's partial file; retries for recognized transient network failures are bounded by `--retries 0..3` (default 2). License and login failures are not automatically repeated by the wrapper.

Data commands print JSON to stdout; download/install progress goes to stderr. Login keeps ipatool's interactive prompts. `inspect_ipa`, `download_app`, `resolve_app_id`, and `install_app` can be imported into another Python project:

```python
from pathlib import Path
from ipa_restore import inspect_ipa, install_app

report = inspect_ipa(Path("RussianPost.ipa"))
result = install_app(Path("RussianPost.ipa"), ios="/path/to/ios")
```

Global options go **before** the command:

```sh
python3 ipa_restore.py --home ./my-local-state restore 771700826
python3 ipa_restore.py --ipatool /path/to/ipatool --ios /path/to/ios restore 771700826
```

`IPA_RESTORE_HOME` also sets the working directory. Otherwise tools, downloads and private state stay beside the script in `.ipa-restore/`. Standard ipatool uses `XDG_STATE_HOME` for session files; it can migrate an existing legacy `~/.ipatool` session there. Its macOS keychain entries remain managed by ipatool.

## Existing Bmrng users

If Bmrng is already installed and logged into the Apple Account that owns the app:

```sh
python3 ipa_restore.py setup
python3 ipa_restore.py --bmrng restore 771700826
```

This explicit opt-in uses Bmrng's **installed** ipatool-cpp 1.0.9-bmrng.1, copies its encrypted session and cookies to `.ipa-restore/bmrng-state`, and reads the session key from macOS Keychain into process memory. macOS may ask you to allow access. The key is never printed or written to disk by this script. Bmrng updates may change the tool path; use `--ipatool` to override it. This adapter is version-specific and Bmrng is not required for the standard backend.

## What restoration means

- The tool downloads an App Store package using an existing license. It does not buy apps or automatically obtain new licenses.
- Installation runs over the app's bundle identifier; the tool never uninstalls an app or erases its container. Application updates may migrate data, so normal device backups remain useful.
- Success means the iPhone reports the expected bundle ID and version with `IsDemotedApp: false`. It does not verify login or data inside the app.
- An offloaded icon does not guarantee that Apple still provides the IPA. Region, account ownership, app availability and supported iOS version still matter. No entitlement or DRM bypass is performed.
- A normal restore asks Apple for the available version, which can differ from the version that was previously installed.
- These tools use unofficial App Store/device interfaces. A future Apple change can break downloading or installation.

If you see **license required**, log into the original purchasing account. If a bundle lookup returns no results, try a numeric ID or old App Store link; numeric IDs do not require public lookup to succeed. Search by name can only find currently listed apps.

If USB access fails, unlock the phone, check the cable and trust pairing. Some go-ios operations on iOS 17+ require a tunnel; follow the [upstream instructions](https://github.com/danielpaulus/go-ios). This script does not start privileged daemons or change phone settings automatically. In our original restore, listing and installation worked without a tunnel.

Keep `.ipa-restore/`, downloaded IPAs, cookies and account files private. The repository contains only source, documentation and synthetic tests.

## Development

```sh
python3 -m unittest -v
python3 ipa_restore.py --help
```

The tests run without Apple credentials or a real phone. They cover identity validation, corrupted/mismatched packages, device selection and installation verification. The original Bmrng-backed download and USB installation were tested on a real iPhone; the standard backend's CLI/setup are checked separately, not claimed as an authenticated end-to-end test.

MIT license. Contributions welcome; keep the runtime in one file and avoid committing accounts or packages.
