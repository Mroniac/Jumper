# Jumper

**English** · [Русский](README.ru.md)

Move code, text and files between your PC and your phone over home Wi-Fi. No cloud, no sign-up, no emailing things to yourself.

A small server runs on the PC, and the phone opens the app (or a page in any browser). Paste code on one device and a second later it's on the other, with syntax highlighting and a "Copy all" button.

## Features

- A feed of code snippets with syntax highlighting and line numbers
- "Copy all" and "Paste from clipboard" buttons
- File transfer up to 100 MB both ways; drag and drop on the PC
- The Android app finds your PC on the network by itself, even after its IP address changes
- "Share → Jumper" from any Android app sends text to the PC
- Light and dark themes
- English and Russian interface, picked from the system language
- Everything stays on your PC: the last 60 snippets and files

## Install

Ready-to-use files are in [Releases](../../releases):

| File | Where |
|---|---|
| `Jumper.exe` | Windows 10/11 PC |
| `Jumper.apk` | Android 7 or newer |

1. Run `Jumper.exe`. A window with the address opens, and the page opens in your browser. When Windows asks about network access, allow it for private networks.
2. Install `Jumper.apk` on your phone (allow installs from this source) and open it. The app finds the PC automatically.

The phone and the PC must be on the same Wi-Fi network. The app is optional: you can open the address shown in the Jumper window (`http://192.168.x.x:8765`) in any phone browser.

## Run from source

```
cd pc
python jumper.py
```

Requires Python 3.8+, no third-party packages. `pc/build.bat` builds `Jumper.exe` with PyInstaller.

The Android project is in `android/`: open it in Android Studio and choose Build → Build APK(s).

## How it works

- `pc/jumper.py` is an HTTP server on port 8765 with the page built in, plus a UDP discovery responder on port 8766. Data is stored next to the program in `jumper_clips.json` and the `jumper_files` folder.
- `android/` is a WebView app. It finds the PC with a `JUMPER_DISCOVER` broadcast and gives the page clipboard access, a file picker and saving to Downloads/Jumper.

Jumper is meant for a home network and has no password, so don't run it on public Wi-Fi.

## Builds

GitHub Actions builds the APK and EXE on every push (Actions tab → latest run → Artifacts). To attach them to a release, create one: Releases → Draft a new release → a tag such as `v1.0` → Publish.
