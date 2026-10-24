"""Accessible remote control for a Samsung Smart TV (2016 and later).

Keyboard driven and speaks results through the running screen reader (JAWS, NVDA, ...)
using accessible_output2. Ctrl+1 to Ctrl+4 switch pages, F1 lists the keys.
"""
import json
import os
import queue
import sys
import threading
import time

import wx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tv_api
import tv_logic as logic

try:
    from accessible_output2.outputs.auto import Auto as _AutoSpeech
except Exception:
    _AutoSpeech = None

APP_NAME = "TV Remote"
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "settings.json")

HELP_TEXT = """Keys for TV Remote

Anywhere:
Ctrl+1 to Ctrl+4: go to Remote, Apps, Type text, or TV info.
Ctrl+Shift+P: power. Turns the TV off if it is on, or wakes it if it is off.
Ctrl+Up and Ctrl+Down: volume up and down.
Ctrl+M: mute.
Ctrl+H: Home (Smart Hub).
Ctrl+Shift+S: settings (TV address, pairing).
F5: refresh the current page.
F1: this help.
Alt+F4: quit.

Remote buttons with Alt, from anywhere in the window: hold Alt and press
the keypad key below. Alt+Left is Left, Alt+Enter is OK, Alt+Backspace is
Back, Alt+M is mute, Alt+5 is the number 5, and so on. Alt+K is Pause
(Alt+Space belongs to Windows).

On the Remote page, in the keypad (the first thing on the page),
the same keys work without Alt:
%s

Also on the Remote page: buttons for the common keys, and a list of
every remote button. Press Enter on one to send it.

Apps page: Enter launches the selected app. Delete closes it.

Type text page: when the TV shows its on-screen keyboard (a search box,
for example), type here and press Enter to send the text.

The first time this computer connects, the TV asks on screen whether to
allow "Accessible TV Remote". Accept it with the TV's own remote once;
after that the app is remembered.
""" % logic.keypad_help()


class Speech:
    """Speaks through the running screen reader. The Windows (SAPI) voice, which
    accessible_output2 falls back to when no screen reader runs, is used only if allowed."""

    def __init__(self, allow_system_voice=False):
        self.allow_system_voice = allow_system_voice
        self._out = None
        if _AutoSpeech:
            try:
                self._out = _AutoSpeech()
            except Exception:
                self._out = None

    def say(self, text, interrupt=True):
        if not self._out:
            return
        try:
            out = self._out.get_first_available_output()
            if out and (self.allow_system_voice or not out.is_system_output()):
                out.speak(text, interrupt=interrupt)
        except Exception:
            pass


def load_settings():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=1)
    except OSError:
        pass


def labelled(parent, sizer, label, ctrl, proportion=0):
    """Put a static label right before its control, so screen readers associate them."""
    sizer.Add(wx.StaticText(parent, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
    sizer.Add(ctrl, proportion, wx.EXPAND | wx.ALL, 6)
    return ctrl


ACCEL_KEYS = {
    "Up": [wx.WXK_UP], "Down": [wx.WXK_DOWN], "Left": [wx.WXK_LEFT], "Right": [wx.WXK_RIGHT],
    "Enter": [wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER], "Back": [wx.WXK_BACK], "Home": [wx.WXK_HOME],
    "PageUp": [wx.WXK_PAGEUP], "PageDown": [wx.WXK_PAGEDOWN],
    "+": [wx.WXK_NUMPAD_ADD], "-": [ord("-"), wx.WXK_NUMPAD_SUBTRACT],
    # Windows itself uses Alt+Space (window menu) and Alt+Escape (switch windows).
    "Space": [], "Escape": [],
}
ACCEL_KEYS.update({"F%d" % n: [getattr(wx, "WXK_F%d" % n)] for n in range(1, 13)})
ACCEL_KEYS.update({str(n): [ord(str(n)), getattr(wx, "WXK_NUMPAD%d" % n)] for n in range(10)})


def alt_accels(spec):
    """Accelerator entries giving a keypad key its Alt version, e.g. 'Shift+Back' -> Alt+Shift+Backspace."""
    flags = wx.ACCEL_ALT
    if spec.startswith("Shift+"):
        flags |= wx.ACCEL_SHIFT
        spec = spec[len("Shift+"):]
    codes = ACCEL_KEYS[spec] if spec in ACCEL_KEYS else [ord(spec)]
    return [(flags, code) for code in codes]


NOT_FOUND_TIP = ("You can type the TV's address instead. To find it on the TV: Settings, General, "
                 "Network, Network Status, IP Settings. A guest Wi-Fi network usually can't reach the TV.")


# ---------------------------------------------------------------- dialogs

class TextDialog(wx.Dialog):
    """Read-only text to arrow through, then Escape or Close."""

    def __init__(self, parent, title, text):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        box = wx.TextCtrl(self, value=text, size=(520, 360),
                          style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        box.SetName(title)
        s.Add(box, 1, wx.EXPAND | wx.ALL, 6)
        close = wx.Button(self, wx.ID_CANCEL, "&Close")
        s.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 6)
        self.SetSizerAndFit(s)
        box.SetFocus()


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, host, mac, system_voice):
        super().__init__(parent, title="TV settings")
        self.forget = False
        s = wx.BoxSizer(wx.VERTICAL)
        self.host = labelled(self, s, "TV &address:", wx.TextCtrl(self, value=host))
        self.mac = labelled(self, s, "TV &MAC address (for turning it on):", wx.TextCtrl(self, value=mac))
        row = wx.BoxSizer(wx.HORIZONTAL)
        find = wx.Button(self, label="&Find TV on the network")
        forget = wx.Button(self, label="Forget &pairing")
        row.Add(find, 0, wx.ALL, 6)
        row.Add(forget, 0, wx.ALL, 6)
        s.Add(row)
        self.system_voice = wx.CheckBox(self, label="Speak with the &Windows voice when no screen reader is running")
        self.system_voice.SetValue(system_voice)
        s.Add(self.system_voice, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label="To turn the TV on from here, enable Power On with Mobile on the TV: "
                                        "Settings, General, Network, Expert Settings."), 0, wx.ALL, 6)
        s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 6)
        self.SetSizerAndFit(s)
        find.Bind(wx.EVT_BUTTON, self.on_find)
        forget.Bind(wx.EVT_BUTTON, self.on_forget)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.host.SetFocus()

    def on_find(self, evt):
        wx.BeginBusyCursor()
        try:
            found, searched = tv_api.find_tvs()
        finally:
            wx.EndBusyCursor()
        if not found:
            wx.MessageBox("No Samsung TV answered. Make sure it is on and on the same network. %s\n\n%s"
                          %(logic.searched_text(searched), NOT_FOUND_TIP), "Find TV", wx.ICON_INFORMATION, self)
            return
        labels = ["%s (%s) at %s" % ((info.get("device") or {}).get("name", "Samsung TV"),
                                     (info.get("device") or {}).get("modelName", ""), ip) for ip, info in found]
        with wx.SingleChoiceDialog(self, "Samsung TVs found:", "Find TV", labels) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            ip, info = found[dlg.GetSelection()]
        self.host.SetValue(ip)
        mac = logic.info_mac(info)
        if mac:
            self.mac.SetValue(mac)
        self.host.SetFocus()

    def on_forget(self, evt):
        self.forget = True
        wx.MessageBox("The pairing will be forgotten when you press OK. The TV will ask to allow this "
                      "computer again on the next connection.", "Forget pairing", wx.ICON_INFORMATION, self)

    def on_ok(self, evt):
        if self.mac.GetValue().strip():
            try:
                self.mac.SetValue(logic.normalize_mac(self.mac.GetValue()))
            except ValueError as e:
                wx.MessageBox(str(e), "Check the MAC address", wx.ICON_WARNING, self)
                self.mac.SetFocus()
                return
        if not self.host.GetValue().strip():
            wx.MessageBox("Enter the TV's address.", "Check the address", wx.ICON_WARNING, self)
            return
        evt.Skip()


# ---------------------------------------------------------------- keypad

class KeypadAccessible(wx.Accessible):
    """Gives the keypad a name and description screen readers announce on focus."""

    def GetName(self, childId):
        return wx.ACC_OK, "Remote keypad"

    def GetDescription(self, childId):
        return wx.ACC_OK, "Keys go to the TV. Arrows move, Enter is OK, Backspace is Back. F1 lists all keys."

    def GetRole(self, childId):
        return wx.ACC_OK, wx.ROLE_SYSTEM_CLIENT


class Keypad(wx.Panel):
    """A focusable area that turns key presses into TV remote buttons."""

    NAMED = {
        wx.WXK_UP: "Up", wx.WXK_DOWN: "Down", wx.WXK_LEFT: "Left", wx.WXK_RIGHT: "Right",
        wx.WXK_RETURN: "Enter", wx.WXK_NUMPAD_ENTER: "Enter", wx.WXK_BACK: "Back",
        wx.WXK_ESCAPE: "Escape", wx.WXK_PAGEUP: "PageUp", wx.WXK_PAGEDOWN: "PageDown",
        wx.WXK_HOME: "Home", wx.WXK_SPACE: "Space",
        wx.WXK_ADD: "+", wx.WXK_NUMPAD_ADD: "+", wx.WXK_SUBTRACT: "-", wx.WXK_NUMPAD_SUBTRACT: "-",
    }
    NAMED.update({getattr(wx, "WXK_F%d" % n): "F%d" % n for n in range(2, 13) if n != 5})
    NAMED.update({getattr(wx, "WXK_NUMPAD%d" % n): str(n) for n in range(10)})

    def __init__(self, parent, on_key):
        super().__init__(parent, style=wx.WANTS_CHARS | wx.BORDER_SIMPLE, size=(-1, 60))
        self.on_key = on_key
        self.SetName("Remote keypad")
        self.SetAccessible(KeypadAccessible(self))
        self.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        self.text = wx.StaticText(self, label="Keyboard control: click or tab here, then use the keyboard.\n"
                                              "F1 lists the keys.", pos=(10, 8))
        self.text.Bind(wx.EVT_LEFT_DOWN, lambda e: self.SetFocus())
        self.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.Bind(wx.EVT_CHAR, self.on_char)
        self.Bind(wx.EVT_LEFT_DOWN, lambda e: self.SetFocus())
        self.Bind(wx.EVT_SET_FOCUS, lambda e: (self.show_focus(True), e.Skip()))
        self.Bind(wx.EVT_KILL_FOCUS, lambda e: (self.show_focus(False), e.Skip()))

    def show_focus(self, focused):
        """A visible cue for sighted users: the area turns light blue while it has the keyboard."""
        colour = wx.Colour(210, 230, 255) if focused else wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        self.SetBackgroundColour(colour)
        self.text.SetBackgroundColour(colour)
        self.Refresh()

    def AcceptsFocus(self):
        return True

    def AcceptsFocusFromKeyboard(self):
        return True

    def on_key_down(self, evt):
        code = evt.GetKeyCode()
        mods = evt.GetModifiers()
        if code == wx.WXK_TAB:
            self.Navigate(wx.NavigationKeyEvent.IsBackward if evt.ShiftDown() else wx.NavigationKeyEvent.IsForward)
            return
        if mods & (wx.MOD_CONTROL | wx.MOD_ALT):
            evt.Skip()          # leave Ctrl/Alt shortcuts to the window
            return
        name = self.NAMED.get(code)
        if name:
            if mods == wx.MOD_SHIFT:
                name = "Shift+" + name
            key = logic.KEYPAD_MAP.get(name)
            if key:
                self.on_key(key)
                return
            if code in (wx.WXK_RETURN, wx.WXK_SPACE, wx.WXK_BACK, wx.WXK_ESCAPE) or name.startswith("F"):
                return
        evt.Skip()              # printable keys arrive in on_char with the layout applied

    def on_char(self, evt):
        code = evt.GetUnicodeKey()
        if code == wx.WXK_NONE or code < 32:
            evt.Skip()
            return
        key = logic.KEYPAD_MAP.get(chr(code).upper())
        if key:
            self.on_key(key)


# ---------------------------------------------------------------- pages

class Page(wx.Panel):
    title = ""

    def __init__(self, parent, frame):
        super().__init__(parent)
        self.frame = frame
        self.loaded = False

    def refresh(self):
        pass

    def first_control(self):
        return None


class ButtonAccessible(wx.Accessible):
    """Adds the keyboard shortcut as the button's description; everything else stays standard."""

    def __init__(self, win, description):
        super().__init__(win)
        self.description = description

    def GetDescription(self, childId):
        return wx.ACC_OK, self.description


def high_contrast():
    """True when Windows High Contrast is on: then the app keeps the system colours."""
    try:
        import ctypes

        class HIGHCONTRAST(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint), ("lpszDefaultScheme", ctypes.c_wchar_p)]
        hc = HIGHCONTRAST(ctypes.sizeof(HIGHCONTRAST), 0, None)
        ctypes.windll.user32.SystemParametersInfoW(0x0042, ctypes.sizeof(hc), ctypes.byref(hc), 0)
        return bool(hc.dwFlags & 1)
    except Exception:
        return False


class RemoteBody(wx.Panel):
    """The dark, rounded remote the buttons sit on."""

    BODY = wx.Colour(38, 38, 42)

    def __init__(self, parent, styled):
        super().__init__(parent)
        self.styled = styled
        if styled:
            self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
            self.SetBackgroundColour(self.BODY)
            self.Bind(wx.EVT_PAINT, self.on_paint)
            self.Bind(wx.EVT_SIZE, lambda e: (self.Refresh(), e.Skip()))

    def on_paint(self, evt):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
        dc.Clear()
        w, h = self.GetClientSize()
        dc.SetPen(wx.Pen(wx.Colour(90, 90, 96), 2))
        dc.SetBrush(wx.Brush(self.BODY))
        dc.DrawRoundedRectangle(1, 1, w - 2, h - 2, 28)


class RemotePage(Page):
    title = "Remote"
    # Colours: (normal background, text). Focus turns any button blue so it's easy to see.
    GREY = (wx.Colour(70, 70, 76), wx.WHITE)
    DARK = (wx.Colour(55, 55, 60), wx.WHITE)
    POWER = (wx.Colour(190, 40, 40), wx.WHITE)
    OK = (wx.Colour(105, 105, 112), wx.WHITE)
    COLOURS = {"KEY_RED": (wx.Colour(200, 40, 40), wx.WHITE), "KEY_GREEN": (wx.Colour(40, 150, 60), wx.WHITE),
               "KEY_YELLOW": (wx.Colour(225, 195, 40), wx.BLACK), "KEY_CYAN": (wx.Colour(40, 90, 200), wx.WHITE)}
    FOCUS = (wx.Colour(30, 136, 229), wx.WHITE)

    # Rows of the remote, top to bottom; this is also the Tab order. None leaves a gap.
    # (label, TV key or "power", shortcut)
    ROWS = [
        [("Power", "power", "Ctrl+Shift+P"), None, ("Source", "KEY_SOURCE", "Alt+O")],
        [("1", "KEY_1", "Alt+1"), ("2", "KEY_2", "Alt+2"), ("3", "KEY_3", "Alt+3")],
        [("4", "KEY_4", "Alt+4"), ("5", "KEY_5", "Alt+5"), ("6", "KEY_6", "Alt+6")],
        [("7", "KEY_7", "Alt+7"), ("8", "KEY_8", "Alt+8"), ("9", "KEY_9", "Alt+9")],
        [("Info", "KEY_INFO", "Alt+I"), ("0", "KEY_0", "Alt+0"), ("Guide", "KEY_GUIDE", "Alt+G")],
        "gap",
        [("Volume up", "KEY_VOLUP", "Alt+="), ("Home", "KEY_HOME", "Alt+H"), ("Channel up", "KEY_CHUP", "Alt+Page Up")],
        [("Mute", "KEY_MUTE", "Alt+M"), ("Settings", "KEY_MENU", "Alt+E"), ("Previous channel", "KEY_PRECH", "Alt+L")],
        [("Volume down", "KEY_VOLDOWN", "Alt+-"), None, ("Channel down", "KEY_CHDOWN", "Alt+Page Down")],
        "gap",
        [None, ("Up", "KEY_UP", "Alt+Up"), None],
        [("Left", "KEY_LEFT", "Alt+Left"), ("OK", "KEY_ENTER", "Alt+Enter"), ("Right", "KEY_RIGHT", "Alt+Right")],
        [None, ("Down", "KEY_DOWN", "Alt+Down"), None],
        [("Back", "KEY_RETURN", "Alt+Backspace"), ("Tools", "KEY_TOOLS", "Alt+T"), ("Exit", "KEY_EXIT", "Alt+X")],
        "gap",
        [("Rewind", "KEY_REWIND", "Alt+R"), ("Play", "KEY_PLAY", "Alt+P"), ("Pause", "KEY_PAUSE", "Alt+K"),
         ("Stop", "KEY_STOP", "Alt+S"), ("Fast forward", "KEY_FF", "Alt+F")],
        [("Red", "KEY_RED", "Alt+F9"), ("Green", "KEY_GREEN", "Alt+F10"), ("Yellow", "KEY_YELLOW", "Alt+F11"),
         ("Blue", "KEY_CYAN", "Alt+F12"), ("Captions", "KEY_CAPTION", "Alt+C")],
    ]

    def __init__(self, parent, frame):
        super().__init__(parent, frame)
        self.styled = not high_contrast()
        outer = wx.BoxSizer(wx.HORIZONTAL)
        body = RemoteBody(self, self.styled)
        bs = wx.BoxSizer(wx.VERTICAL)
        # Created first so it comes first in the Tab order: the remote's "touch area".
        self.keypad = Keypad(body, frame.send_key)
        bs.Add(self.keypad, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 18)
        for row in self.ROWS:
            if row == "gap":
                bs.AddSpacer(8)
                continue
            grid = wx.GridSizer(1, len(row), 0, 6)
            for cell in row:
                if cell is None:
                    grid.AddSpacer(1)
                else:
                    grid.Add(self.make_button(body, *cell), 0, wx.EXPAND)
            bs.AddSpacer(6)
            bs.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 18)
        bs.AddSpacer(22)
        body.SetSizer(bs)
        outer.Add(body, 0, wx.ALL, 8)

        side = wx.BoxSizer(wx.VERTICAL)
        self.all_keys = labelled(self, side, "&All remote buttons (Enter sends):",
                                 wx.ListBox(self, choices=[name for _, name in logic.KEYS]), 1)
        self.all_keys.SetSelection(0)
        send = wx.Button(self, label="Sen&d selected button")
        side.Add(send, 0, wx.ALL, 6)
        outer.Add(side, 1, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(outer)
        send.Bind(wx.EVT_BUTTON, lambda e: self.send_selected())
        self.all_keys.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.send_selected())
        self.all_keys.Bind(wx.EVT_KEY_DOWN, self.on_list_key)

    def make_button(self, parent, label, key, shortcut):
        b = wx.Button(parent, label=label, size=(-1, 28))
        b.SetToolTip("%s (%s)" % (label, shortcut))
        b.SetAccessible(ButtonAccessible(b, shortcut))
        if key == "power":
            b.Bind(wx.EVT_BUTTON, lambda e: self.frame.power())
        else:
            b.Bind(wx.EVT_BUTTON, lambda e, k=key: self.frame.send_key(k))
        if self.styled:
            colours = (self.POWER if key == "power" else self.OK if key == "KEY_ENTER"
                       else self.COLOURS.get(key) or (self.GREY if label[0].isdigit() else self.DARK))
            self.paint(b, colours)
            b.Bind(wx.EVT_SET_FOCUS, lambda e, b=b: (self.paint(b, self.FOCUS), e.Skip()))
            b.Bind(wx.EVT_KILL_FOCUS, lambda e, b=b, c=colours: (self.paint(b, c), e.Skip()))
        return b

    @staticmethod
    def paint(button, colours):
        button.SetBackgroundColour(colours[0])
        button.SetForegroundColour(colours[1])
        button.Refresh()

    def on_list_key(self, evt):
        if evt.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not evt.HasAnyModifiers():
            self.send_selected()
        else:
            evt.Skip()

    def send_selected(self):
        i = self.all_keys.GetSelection()
        if i != wx.NOT_FOUND:
            self.frame.send_key(logic.KEYS[i][0], speak=True)

    def first_control(self):
        return self.keypad


class AppsPage(Page):
    title = "Apps"

    def __init__(self, parent, frame):
        super().__init__(parent, frame)
        self.apps = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.list = labelled(self, s, "&Apps on the TV (Enter launches):", wx.ListBox(self), 1)
        row = wx.BoxSizer(wx.HORIZONTAL)
        launch = wx.Button(self, label="La&unch")
        close = wx.Button(self, label="&Quit app")
        refresh = wx.Button(self, label="Refresh list (F5)")
        for b in (launch, close, refresh):
            row.Add(b, 0, wx.ALL, 6)
        s.Add(row)
        self.SetSizer(s)
        launch.Bind(wx.EVT_BUTTON, lambda e: self.launch())
        close.Bind(wx.EVT_BUTTON, lambda e: self.close_app())
        refresh.Bind(wx.EVT_BUTTON, lambda e: self.refresh())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.launch())
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)

    def on_key(self, evt):
        code = evt.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not evt.HasAnyModifiers():
            self.launch()
        elif code == wx.WXK_DELETE:
            self.close_app()
        else:
            evt.Skip()

    def current(self):
        i = self.list.GetSelection()
        return self.apps[i] if 0 <= i < len(self.apps) else None

    def refresh(self):
        def done(apps):
            keep = self.current()
            self.apps = apps
            self.list.Set([a[0] for a in apps])
            if apps:
                ids = [a[1] for a in apps]
                self.list.SetSelection(ids.index(keep[1]) if keep and keep[1] in ids else 0)
                self.frame.announce("%d apps" % len(apps))
            else:
                self.frame.announce("The TV didn't list any apps")
        self.frame.run(self.frame.tv.installed_apps, done, "Reading apps from the TV")

    def launch(self):
        app = self.current()
        if not app:
            return
        self.frame.run(lambda: self.frame.tv.launch_app(app[1], app[2]),
                       lambda r: self.frame.announce("Opening %s" % app[0]), "Opening %s" % app[0])

    def close_app(self):
        app = self.current()
        if not app:
            return

        def done(ok):
            self.frame.announce(("Closed %s" if ok else "%s could not be closed from here") % app[0])
        self.frame.run(lambda: self.frame.tv.close_app(app[1]), done, "Closing %s" % app[0])

    def first_control(self):
        return self.list


class TextPage(Page):
    title = "Type text"

    def __init__(self, parent, frame):
        super().__init__(parent, frame)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label="Open a search box or other text field on the TV first, so its "
                                        "on-screen keyboard is showing."), 0, wx.ALL, 6)
        self.text = labelled(self, s, "Text to se&nd (Enter sends):",
                             wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER))
        self.done_key = wx.CheckBox(self, label="Press OK on the TV &after sending (to run the search)")
        s.Add(self.done_key, 0, wx.ALL, 6)
        row = wx.BoxSizer(wx.HORIZONTAL)
        send = wx.Button(self, label="Sen&d text")
        clear = wx.Button(self, label="Clear")
        row.Add(send, 0, wx.ALL, 6)
        row.Add(clear, 0, wx.ALL, 6)
        s.Add(row)
        self.SetSizer(s)
        send.Bind(wx.EVT_BUTTON, lambda e: self.send())
        clear.Bind(wx.EVT_BUTTON, lambda e: (self.text.Clear(), self.text.SetFocus()))
        self.text.Bind(wx.EVT_TEXT_ENTER, lambda e: self.send())

    def send(self):
        text = self.text.GetValue()
        if not text:
            self.frame.announce("Nothing to send")
            return
        press_ok = self.done_key.GetValue()

        def work():
            self.frame.tv.send_text(text)
            if press_ok:
                time.sleep(0.3)
                self.frame.tv.send_key("KEY_ENTER")
        self.frame.run(work, lambda r: self.frame.announce("Sent"), "Sending text")

    def first_control(self):
        return self.text


class InfoPage(Page):
    title = "TV info"

    def __init__(self, parent, frame):
        super().__init__(parent, frame)
        s = wx.BoxSizer(wx.VERTICAL)
        self.text = labelled(self, s, "TV i&nformation:", wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP), 1)
        row = wx.BoxSizer(wx.HORIZONTAL)
        refresh = wx.Button(self, label="Refresh (F5)")
        settings = wx.Button(self, label="Settings (Ctrl+Shift+S)...")
        row.Add(refresh, 0, wx.ALL, 6)
        row.Add(settings, 0, wx.ALL, 6)
        s.Add(row)
        self.SetSizer(s)
        refresh.Bind(wx.EVT_BUTTON, lambda e: self.refresh())
        settings.Bind(wx.EVT_BUTTON, lambda e: self.frame.edit_settings())

    def refresh(self, quiet=False):
        def done(info):
            self.text.SetValue(logic.info_report(info, self.frame.tv.host))
            self.frame.got_info(info, quiet)
        self.frame.run(self.frame.tv.info, done, "Checking the TV")

    def first_control(self):
        return self.text


# ---------------------------------------------------------------- main window

class MainFrame(wx.Frame):
    PAGES = [RemotePage, AppsPage, TextPage, InfoPage]

    def __init__(self, settings):
        super().__init__(None, title=APP_NAME, size=(820, 860))
        self.settings = settings
        self.tv = tv_api.SamsungTV(settings.get("host", ""), settings.get("mac", ""),
                                   settings.get("token", ""), on_token=self.got_token)
        self.speech = Speech(settings.get("system_voice", False))
        self.jobs = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        panel = wx.Panel(self)
        self.book = wx.Notebook(panel)
        self.pages = [cls(self.book, self) for cls in self.PAGES]
        for p in self.pages:
            self.book.AddPage(p, p.title)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.book, 1, wx.EXPAND)
        panel.SetSizer(s)
        self.CreateStatusBar()

        ids = []
        for i in range(len(self.pages)):
            ident = wx.NewIdRef()
            self.Bind(wx.EVT_MENU, lambda e, i=i: self.go(i), id=ident)
            ids.append((wx.ACCEL_CTRL, ord(str(i + 1)), ident))

        def accel(flags, key, handler):
            ident = wx.NewIdRef()
            self.Bind(wx.EVT_MENU, lambda e: handler(), id=ident)
            ids.append((flags, key, ident))
        accel(wx.ACCEL_NORMAL, wx.WXK_F5, lambda: self.current().refresh())
        accel(wx.ACCEL_NORMAL, wx.WXK_F1, self.show_help)
        accel(wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("P"), self.power)
        accel(wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("S"), self.edit_settings)
        accel(wx.ACCEL_CTRL, wx.WXK_UP, lambda: self.send_key("KEY_VOLUP"))
        accel(wx.ACCEL_CTRL, wx.WXK_DOWN, lambda: self.send_key("KEY_VOLDOWN"))
        accel(wx.ACCEL_CTRL, ord("M"), lambda: self.send_key("KEY_MUTE"))
        accel(wx.ACCEL_CTRL, ord("H"), lambda: self.send_key("KEY_HOME"))
        for spec, key in logic.KEYPAD:
            for flags, code in alt_accels(spec):
                accel(flags, code, lambda key=key: self.send_key(key))
        self.SetAcceleratorTable(wx.AcceleratorTable(ids))
        self.book.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_page)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.pages[3].loaded = True
        if self.tv.host:
            self.pages[3].refresh(quiet=True)
        else:
            wx.CallAfter(self.first_run)
        wx.CallAfter(self.pages[0].keypad.SetFocus)

    # ---- background work, one job at a time so key presses stay in order

    def _worker(self):
        while True:
            work, done = self.jobs.get()
            try:
                result, error = work(), None
            except Exception as e:
                result, error = None, e
            wx.CallAfter(self._finish, done, result, error)

    def _finish(self, done, result, error):
        if error is None:
            self.SetStatusText("Ready")
            if done:
                done(result)
        elif isinstance(error, tv_api.TVError):
            self.SetStatusText(str(error))
            self.speech.say(str(error))
            if self.jobs.empty():
                wx.MessageBox(str(error), APP_NAME, wx.ICON_ERROR, self)
        else:
            self.SetStatusText("Error: %s" % error)
            wx.MessageBox("Something went wrong: %s" % error, APP_NAME, wx.ICON_ERROR, self)

    def run(self, work, done, busy):
        self.SetStatusText(busy + "...")
        if not self.tv.connected and not self.tv.token and work != self.tv.info:
            self.announce("Connecting. If the TV asks to allow Accessible TV Remote, accept it with the TV remote.")
        self.jobs.put((work, done))

    def send_key(self, key, speak=None):
        if self.jobs.qsize() > 8:
            return          # a held-down key outran the TV; drop the extras
        name = logic.describe_key(key)
        if speak is None:
            speak = key in logic.SPOKEN_KEYS
        self.run(lambda: self.tv.send_key(key), (lambda r: self.announce(name)) if speak else None, name)

    # ---- power

    def power(self):
        tv = self.tv

        def work():
            info = tv.info(timeout=2)
            state = logic.power_state(info)
            if state == "on":
                if logic.is_frame(info):
                    # A short press only toggles Art Mode on a Frame; holding Power turns it off.
                    tv.hold_key("KEY_POWER", 3)
                else:
                    tv.send_key("KEY_POWER")
                return "Turning the TV off"
            wx.CallAfter(self.announce, "Waking the TV")
            tv.wake()
            if state == "standby":
                try:
                    tv.send_key("KEY_POWER")
                except tv_api.TVError:
                    pass
            for _ in range(20):
                time.sleep(1)
                if logic.power_state(tv.info(timeout=1)) == "on":
                    return "The TV is on"
            return ("Sent the wake-up signal but the TV hasn't answered yet. If it stays off, turn on "
                    "Power On with Mobile in the TV's Settings, General, Network, Expert Settings.")

        def done(message):
            self.announce(message)
            self.pages[3].refresh(quiet=True)
        self.SetStatusText("Power...")
        self.jobs.put((work, done))

    # ---- settings and info

    def got_token(self, token):
        self.settings["token"] = token
        wx.CallAfter(save_settings, self.settings)
        wx.CallAfter(self.announce, "Paired with the TV")

    def got_info(self, info, quiet):
        state = logic.power_state(info)
        name = ((info or {}).get("device") or {}).get("name") or "Samsung TV"
        self.SetTitle("%s - %s (%s)" % (APP_NAME, name, {"on": "on", "standby": "standby",
                                                         "off": "not answering"}[state]))
        mac = logic.info_mac(info)
        if mac and mac != self.settings.get("mac"):
            self.settings["mac"] = self.tv.mac = mac
            save_settings(self.settings)
        if state == "off":
            self.announce("The TV is not answering. Ctrl+Shift+P tries to wake it.")
        elif not quiet:
            self.announce("TV is %s" % state)

    def first_run(self):
        """No TV chosen yet: look for one on the network."""
        def done(result):
            found, searched = result
            if len(found) == 1:
                choice = found[0]
            elif found:
                labels = ["%s (%s) at %s" % ((info.get("device") or {}).get("name", "Samsung TV"),
                                             (info.get("device") or {}).get("modelName", ""), ip)
                          for ip, info in found]
                with wx.SingleChoiceDialog(self, "Choose your TV:", "Samsung TVs found", labels) as dlg:
                    if dlg.ShowModal() != wx.ID_OK:
                        return
                    choice = found[dlg.GetSelection()]
            else:
                wx.MessageBox("No Samsung TV answered on the network. Make sure the TV is on and on the "
                              "same network as this computer. %s\n\n%s"
                              % (logic.searched_text(searched), NOT_FOUND_TIP),
                              APP_NAME, wx.ICON_INFORMATION, self)
                self.edit_settings()
                return
            ip, info = choice
            self.tv.host = self.settings["host"] = ip
            mac = logic.info_mac(info)
            if mac:
                self.tv.mac = self.settings["mac"] = mac
            save_settings(self.settings)
            name = (info.get("device") or {}).get("name") or "Samsung TV"
            self.announce("Found %s at %s. The TV will ask to allow this app the first time you "
                          "press a key: accept it with the TV remote." % (name, ip))
            self.pages[3].refresh(quiet=True)
        self.announce("Looking for your Samsung TV on the network")
        self.jobs.put((tv_api.find_tvs, done))

    def edit_settings(self):
        with SettingsDialog(self, self.tv.host, self.tv.mac, self.speech.allow_system_voice) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            host, mac = dlg.host.GetValue().strip(), dlg.mac.GetValue().strip()
            forget = dlg.forget
            self.speech.allow_system_voice = self.settings["system_voice"] = dlg.system_voice.GetValue()
        changed = host != self.tv.host
        self.tv.disconnect()
        self.tv.host, self.tv.mac = host, mac
        if forget or changed:
            self.tv.token = ""
            self.settings.pop("token", None)
        self.settings.update(host=host, mac=mac)
        save_settings(self.settings)
        self.announce("Settings saved")
        self.pages[3].refresh(quiet=True)

    # ---- navigation

    def current(self):
        return self.pages[self.book.GetSelection()]

    def go(self, i):
        self.book.SetSelection(i)
        ctrl = self.pages[i].first_control()
        if ctrl:
            ctrl.SetFocus()

    def on_page(self, evt):
        page = self.current()
        if not page.loaded:
            page.loaded = True
            page.refresh()
        evt.Skip()

    def announce(self, text):
        self.SetStatusText(text)
        self.speech.say(text)

    def show_help(self):
        with TextDialog(self, "Keys", HELP_TEXT) as dlg:
            dlg.ShowModal()

    def on_close(self, evt):
        self.tv.disconnect()
        evt.Skip()


def main():
    app = wx.App()
    settings = load_settings()
    frame = MainFrame(settings)
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
