# TV Remote

An accessible remote control for Samsung Smart TVs (2016 and later, Tizen), built for
screen readers (JAWS, NVDA and others) and full keyboard control.

## What it does

- **Remote keypad**: arrow keys, OK, Back, Home, volume, channels, numbers, playback and the
  colour buttons, sent straight to the TV from the keyboard.
- **Alt shortcuts anywhere in the window**: Alt+Left is Left, Alt+Enter is OK, Alt+M is mute,
  and so on. Press F1 in the app for the full list.
- **Apps**: lists the TV's apps; Enter opens one.
- **Type text**: sends text to the TV's on-screen keyboard (search boxes and so on).
- **Power**: Ctrl+Shift+P turns the TV off, or wakes it with Wake-on-LAN. Frame TVs are turned
  fully off rather than into Art Mode.
- **TV info**: model, power state and network details.

Navigation keys stay silent so the TV's own Voice Guide isn't talked over; volume, channel,
power and errors are spoken through the running screen reader.

## Download (no Python needed)

1. Go to the [Releases page](https://github.com/mcourcel/tv-remote/releases/latest) and download
   `TV-Remote-windows.zip`.
2. Unzip the whole folder somewhere, such as Documents, and run `TV Remote.exe`. Keep the files
   together; the program needs them. Settings are kept in `settings.json` in the same folder.
3. Windows SmartScreen may warn that the program is from an unknown publisher. Choose
   "More info", then "Run anyway".

Needs Windows 10 or 11, 64-bit, with the TV and the computer on the same network.

## First run

The app looks for a Samsung TV on your network by itself; if it finds more than one, it asks
which. If it finds none, it opens Settings (Ctrl+Shift+S), where you can type the TV's
address or try **Find TV on the network** again.

The first time the app connects, the TV asks on screen to allow "Accessible TV Remote".
Accept it with the TV's remote once; after that the app is remembered.

If the TV refuses without asking, check on the TV: Settings, General, External Device Manager,
Device Connection Manager. Set Access Notification to "First Time Only", and remove or allow
the app in the Device List.

To turn the TV on from the app, enable **Power On with Mobile**: Settings, General, Network,
Expert Settings.

## Running from source

- Windows 10 or 11 with Python 3.10 or later
- `pip install wxPython accessible_output2`
- Run `Start TV Remote.bat`, or `pythonw tv_app.pyw`

To build the ready-to-run folder: `pip install pyinstaller pywin32`, then
`pyinstaller --noconfirm tv_app.spec`. The result is `dist\TV Remote`.

## Files

- `tv_app.pyw`: the window
- `tv_api.py`: talks to the TV (REST info, remote-control WebSocket, Wake-on-LAN); standard
  library only
- `tv_logic.py`: key tables, keyboard map and message formats, no GUI code
- `settings.json`: created by the app; holds the TV address and pairing token. Not committed.

## License

MIT. See [LICENSE](LICENSE).
