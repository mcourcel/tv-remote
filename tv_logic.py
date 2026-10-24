"""wx-free pieces of the TV remote: key tables, keyboard map, message builders, text reports."""
import base64
import re
import urllib.parse

CLIENT_NAME = "Accessible TV Remote"

# Every remote button the app offers, in the order shown in the "All buttons" list.
# (TV key code, spoken name)
KEYS = [
    ("KEY_POWER", "Power, short press (Art Mode on and off on a Frame TV)"),
    ("KEY_HOME", "Home (Smart Hub)"),
    ("KEY_RETURN", "Back"),
    ("KEY_EXIT", "Exit"),
    ("KEY_UP", "Up"),
    ("KEY_DOWN", "Down"),
    ("KEY_LEFT", "Left"),
    ("KEY_RIGHT", "Right"),
    ("KEY_ENTER", "OK / Select"),
    ("KEY_VOLUP", "Volume up"),
    ("KEY_VOLDOWN", "Volume down"),
    ("KEY_MUTE", "Mute"),
    ("KEY_CHUP", "Channel up"),
    ("KEY_CHDOWN", "Channel down"),
    ("KEY_PRECH", "Previous channel"),
    ("KEY_CH_LIST", "Channel list"),
    ("KEY_GUIDE", "Guide"),
    ("KEY_INFO", "Info"),
    ("KEY_MENU", "Settings menu"),
    ("KEY_TOOLS", "Tools"),
    ("KEY_SOURCE", "Source / Input"),
    ("KEY_HDMI", "HDMI"),
    ("KEY_PLAY", "Play"),
    ("KEY_PAUSE", "Pause"),
    ("KEY_STOP", "Stop"),
    ("KEY_REWIND", "Rewind"),
    ("KEY_FF", "Fast forward"),
    ("KEY_REC", "Record"),
    ("KEY_CAPTION", "Captions / Subtitles"),
    ("KEY_AD", "Audio description / Voice guide"),
    ("KEY_PICTURE_SIZE", "Picture size"),
    ("KEY_SLEEP", "Sleep timer"),
    ("KEY_RED", "Red button"),
    ("KEY_GREEN", "Green button"),
    ("KEY_YELLOW", "Yellow button"),
    ("KEY_CYAN", "Blue button"),
    ("KEY_MORE", "Number pad on screen (123)"),
] + [("KEY_%d" % n, "Number %d" % n) for n in range(10)]

KEY_NAMES = dict(KEYS)

# Keyboard shortcuts in the Remote keypad area: (key as the app names it, TV key).
# Names: a letter or digit, punctuation, or Up/Down/Left/Right/Enter/Back/Escape/PageUp/
# PageDown/Home/Space/F1-F12, optionally prefixed with "Shift+".
KEYPAD = [
    ("Up", "KEY_UP"), ("Down", "KEY_DOWN"), ("Left", "KEY_LEFT"), ("Right", "KEY_RIGHT"),
    ("Enter", "KEY_ENTER"),
    ("Back", "KEY_RETURN"), ("Escape", "KEY_RETURN"), ("Shift+Back", "KEY_EXIT"),
    ("Home", "KEY_HOME"), ("H", "KEY_HOME"),
    ("+", "KEY_VOLUP"), ("=", "KEY_VOLUP"), ("-", "KEY_VOLDOWN"), ("M", "KEY_MUTE"),
    ("PageUp", "KEY_CHUP"), ("PageDown", "KEY_CHDOWN"), ("L", "KEY_PRECH"),
    ("Space", "KEY_PAUSE"), ("K", "KEY_PAUSE"), ("P", "KEY_PLAY"), ("S", "KEY_STOP"),
    ("[", "KEY_REWIND"), ("]", "KEY_FF"), ("R", "KEY_REWIND"), ("F", "KEY_FF"),
    ("I", "KEY_INFO"), ("G", "KEY_GUIDE"), ("E", "KEY_MENU"), ("T", "KEY_TOOLS"),
    ("O", "KEY_SOURCE"), ("C", "KEY_CAPTION"), ("X", "KEY_EXIT"),
    ("F9", "KEY_RED"), ("F10", "KEY_GREEN"), ("F11", "KEY_YELLOW"), ("F12", "KEY_CYAN"),
] + [(str(n), "KEY_%d" % n) for n in range(10)]

KEYPAD_MAP = dict(KEYPAD)

# Keys whose effect you can't hear from the TV right away get a short spoken confirmation.
# Navigation keys stay silent so the TV's own Voice Guide isn't talked over.
SPOKEN_KEYS = {"KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_CHUP", "KEY_CHDOWN", "KEY_PRECH",
               "KEY_POWER", "KEY_PLAY", "KEY_PAUSE", "KEY_STOP", "KEY_REWIND", "KEY_FF",
               "KEY_RED", "KEY_GREEN", "KEY_YELLOW", "KEY_CYAN", "KEY_SOURCE", "KEY_CAPTION"}

# Fallback when the TV won't list its apps (many 2020+ models): probed one by one.
KNOWN_APPS = [
    ("Netflix", "3201907018807"),
    ("YouTube", "111299001912"),
    ("Prime Video", "3201910019365"),
    ("Disney+", "3201901017640"),
    ("Hulu", "3201601007625"),
    ("Apple TV", "3201807016597"),
    ("Spotify", "3201606009684"),
    ("Plex", "3201512006963"),
    ("Max", "3202301029760"),
    ("Gallery", "3201710015037"),
    ("Peacock", "3202006020991"),
    ("Tubi", "3201504001965"),
    ("Pluto TV", "3201808016802"),
    ("YouTube TV", "3201707014489"),
    ("Samsung TV Plus", "3201710015067"),
    ("Internet browser", "org.tizen.browser"),
    ("Netflix (older TVs)", "11101200001"),
    ("Prime Video (older TVs)", "3201512006785"),
]


def remote_path(token=""):
    name = base64.b64encode(CLIENT_NAME.encode()).decode()
    path = "/api/v2/channels/samsung.remote.control?name=" + urllib.parse.quote(name, safe="")
    if token:
        path += "&token=" + urllib.parse.quote(str(token), safe="")
    return path


def key_payload(key, cmd="Click"):
    return {"method": "ms.remote.control",
            "params": {"Cmd": cmd, "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}


def text_payload(text):
    return {"method": "ms.remote.control",
            "params": {"Cmd": base64.b64encode(text.encode("utf-8")).decode(), "DataOfCmd": "base64",
                       "TypeOfRemote": "SendInputString"}}


def emit_payload(event, data=""):
    return {"method": "ms.channel.emit", "params": {"event": event, "to": "host", "data": data}}


def launch_payload(app_id, app_type=2, meta=""):
    # app_type 2 = web app (deep link), 4 = native; anything else launches natively too.
    action = "DEEP_LINK" if app_type == 2 else "NATIVE_LAUNCH"
    return emit_payload("ed.apps.launch", {"appId": app_id, "action_type": action, "metaTag": meta})


def parse_installed_apps(event):
    """ed.installedApp.get reply -> [(name, app_id, app_type)] sorted by name."""
    data = (event or {}).get("data") or {}
    rows = data.get("data") if isinstance(data, dict) else data
    apps, seen = [], set()
    for a in rows or []:
        if isinstance(a, dict) and a.get("appId") and a["appId"] not in seen:
            seen.add(a["appId"])
            apps.append((a.get("name") or a["appId"], a["appId"], a.get("app_type", 2)))
    return sorted(apps, key=lambda a: a[0].lower())


def normalize_mac(text):
    digits = re.sub(r"[^0-9a-fA-F]", "", text or "")
    if len(digits) != 12:
        raise ValueError("A MAC address has 12 hex digits, like 10:2b:41:6f:81:38.")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)).lower()


def magic_packet(mac):
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac).replace(":", "")) * 16


def broadcast_for(ip):
    parts = ip.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:3] + ["255"])
    return "255.255.255.255"


def power_state(info):
    """'on', 'standby', or 'off' (no answer)."""
    if not info:
        return "off"
    state = str((info.get("device") or {}).get("PowerState", "on")).lower()
    return "standby" if state == "standby" else "on"


def is_frame(info):
    return str(((info or {}).get("device") or {}).get("FrameTVSupport", "")).lower() == "true"


def info_mac(info):
    dev = (info or {}).get("device") or {}
    try:
        return normalize_mac(dev.get("wifiMac", ""))
    except ValueError:
        return ""


def info_report(info, host):
    if not info:
        return ("The TV at %s is not answering. It is off, or the address is wrong.\n"
                "Use Power on (Ctrl+Shift+P) to wake it, or Settings to change the address." % host)
    dev = info.get("device") or {}
    state = power_state(info)
    lines = [
        "Name: %s" % (dev.get("name") or info.get("name") or "unknown"),
        "Power: %s" % {"on": "On", "standby": "Standby (screen off)"}[state],
        "Model: %s" % (dev.get("modelName") or info.get("model") or "unknown"),
    ]
    for label, key in [("Model code", "model"), ("Type", "type"), ("Operating system", "OS"),
                       ("Firmware", "firmwareVersion"), ("Screen resolution", "resolution"),
                       ("Network", "networkType"), ("Wi-Fi network", "ssid"),
                       ("IP address", "ip"), ("MAC address", "wifiMac"), ("Country", "countryCode"),
                       ("Language", "Language"), ("Frame TV", "FrameTVSupport"),
                       ("Voice support", "VoiceSupport"), ("Secure connection (token)", "TokenAuthSupport")]:
        val = dev.get(key)
        if val not in (None, ""):
            lines.append("%s: %s" % (label, val))
    if not dev.get("ip"):
        lines.append("IP address: %s" % host)
    return "\n".join(lines)


def describe_key(key):
    return KEY_NAMES.get(key, key.replace("KEY_", "").replace("_", " ").title())


def keypad_help():
    groups = {}
    for spec, key in KEYPAD:
        groups.setdefault(key, []).append(spec)
    seen, lines = set(), []
    for spec, key in KEYPAD:
        if key in seen or key.startswith("KEY_") and key[4:].isdigit():
            continue
        seen.add(key)
        names = [n.replace("Back", "Backspace") for n in groups[key]]
        lines.append("%s: %s" % (" or ".join(names), describe_key(key)))
    lines.append("0 to 9: number keys")
    return "\n".join(lines)
