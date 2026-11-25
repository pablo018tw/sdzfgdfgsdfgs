"""
axiom.cc v2.11 - macro helper (box fixes)
Python 3.9+ / tkinter (stdlib only). Macros need Windows; on other OSes the UI
opens in preview mode (nothing gets sent to any game).
Run:  python axiom.py          (it asks for admin rights on start: accept the prompt)
      python axiom.py --no-admin   (skip the admin relaunch)

What changed in v2.11 (Box):
  - Camera turns are now sent as many SMALL mouse events (<= BOX_STEP_PX counts
    each) instead of 4 big jumps, which games and anti-cheat filter much less.
  - The floor is placed LOOKING UP (the box ceiling): the macro tilts the camera
    up, places the floor, then tilts back down. Before, it tried to place the
    floor while aiming straight ahead, where there is no free slot.
  - Floor and roof use the same slot, so BOX_ROOF is off by default.
  - 40 ms pause after selecting a piece so the first click isn't eaten.
  - Info page shows how many camera moves were sent (Box moves).

What changed in v2.10:
  - KEYBINDS PAGE: every key is rebindable from the UI. Click a key button, then
    press the new key (Esc cancels, right-click resets that one to its default).
    Works with keyboard keys, F-keys, numpad, shift/ctrl/alt and the mouse side
    buttons (MB4 / MB5). Saved in axiom_config.json.
  - Two kinds of keys:
      TRIGGERS    the keys YOU press to start a macro (build keys, auto edit,
                  edit click, retake, box, pause). Two triggers can't share a
                  key: picking one that is taken swaps the two.
      GAME BINDS  the keys the macro PRESSES for you (pickaxe, wall, floor,
                  roof, edit, edit confirm). Set them to match Fortnite.
  - Extended keys (arrows, ins/del, right ctrl/alt...) are sent correctly.

What changed in v2.9:
  - NEW BOX MACRO: tap the Box key (default B) and it builds a box around you by itself:
    wall -> turn 90 -> wall -> turn 90 -> wall -> turn 90 -> wall -> turn 90
    (a full 360, so the camera ends where it started), then floor and roof.
    It needs relative mouse movement, so you must calibrate "Box Turn" (Timings
    page): the number of mouse counts for ONE 90 degree turn at your sensitivity.
    Calibrate: tap B and watch the crosshair. If it ends rotated, adjust Box Turn
    until it comes back to the same spot after the whole box.
  - Options are constants near the top: BOX_FLOOR, BOX_ROOF, BOX_TURN_DIR,
    BOX_GAP_MS, BOX_SETTLE_MS. The keys are on the Keybinds page.

What changed in v2.8 (Wall Retake):
  - Pickaxe can swing several times per cycle (RETAKE_SWINGS). One hit was often
    not enough to break the enemy wall.
  - The swing click is held RETAKE_HIT_MS (35 ms) instead of 12 ms, so the game
    never drops it (a frame at 60 fps is ~16 ms).
  - Longer default wait for the hit to land (230 ms). Switching to the wall
    before the hit lands cancels the swing and the wall never breaks.
  - After the wall key there is a 30 ms pause before the first place click, so
    the click is not eaten while build mode is still coming up.
  - Key taps in the retake are never shorter than 25 ms.
  - NOTE: if you have an old axiom_config.json, delete it or press "Reset all"
    so the new default timings are used.

v2.7: Wall retake as a non-blocking state machine (PICKAXE -> SWING -> WALL ->
spam place window -> rest). Releasing R stops it instantly.
v2.5: BUILD BOT does key + left click (AUTO PLACE), ref-counted LMB.
v2.4: Fortnite detection fallback, auto admin relaunch, hook fallback fix,
64-bit safe handles, Info page admin status + test mode.
"""
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk

IS_WIN = os.name == "nt"
if IS_WIN:
    user32   = ctypes.WinDLL("user32",   use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:
    user32 = kernel32 = None

VK_E        = 0x45
VK_C        = 0x43
VK_V        = 0x56
VK_Z        = 0x5A
VK_G        = 0x47
VK_P        = 0x50
VK_F8       = 0x77      # global pause / resume
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_R        = 0x52
VK_1        = 0x31
VK_B        = 0x42
VK_X        = 0x58
VK_ESC      = 0x1B

# Keybinds. These are only the DEFAULTS: change them from the Keybinds page
# (saved in axiom_config.json). `keys` is what the macros actually read.
KEY_DEFAULTS = {
    # triggers - keys you press
    "build_1":    VK_C,         "build_2": VK_V,         "build_3": VK_Z,
    "build_4":    VK_XBUTTON1,  "build_5": VK_XBUTTON2,
    "auto_edit":  VK_G,         "edit_click": VK_E,      "retake": VK_R,
    "box":        VK_B,         "pause": VK_F8,
    # game binds - keys the macros press (match your Fortnite keybinds)
    "pickaxe":    VK_1,         "wall": VK_Z,            "floor": VK_X,
    "roof":       VK_V,         "edit_game": VK_E,       "confirm": VK_P,
}
keys = dict(KEY_DEFAULTS)

RETAKE_SWINGS = 2       # pickaxe hits per cycle (1-4). Raise it if the wall survives
RETAKE_HIT_MS = 35      # how long the swing click is held (must be > 1 frame)

# Box macro options (the keys themselves are on the Keybinds page)
BOX_FLOOR     = True    # place a floor (ceiling) after the 4 walls
BOX_ROOF      = False   # place a roof instead/too. Same slot as the floor, so
                        # normally use ONE of them
BOX_TURN_DIR  = 1       # 1 = turn right between walls, -1 = turn left
BOX_LOOK_DIR  = -1      # -1 = look UP before floor/roof, 1 = look down, 0 = don't tilt
BOX_LOOK_RATIO = 0.6    # tilt size as a fraction of one 90 degree turn (0.6 ~ 54 deg)
BOX_STEP_PX   = 40      # max mouse counts per injected move event
BOX_GAP_MS    = 15      # pause after every box action (tap / click)
BOX_SELECT_MS = 40      # pause after selecting a piece, before the first click
BOX_SETTLE_MS = 25      # extra pause after each camera move

MOUSEEVENTF_MOVE     = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004
MOUSEEVENTF_XDOWN    = 0x0080
MOUSEEVENTF_XUP      = 0x0100
KEYEVENTF_KEYUP      = 0x0002
KEYEVENTF_SCANCODE   = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,   # pgup..down
                0x2D, 0x2E, 0xA3, 0xA5, 0x6F, 0x90}               # ins del rctrl ralt num/ numlock

MAGIC = 0x41584D        # tag on every event WE inject, so the hook can ignore only those

DEFAULTS = {
    "build_enabled":       True,
    "build_click_enabled": True,
    "auto_edit_enabled":   True,
    "edit_click_enabled":  True,
    "wall_retake_enabled": True,
    "box_enabled":         True,
    "build_tap_ms":        13,
    "build_click_ms":      12,
    "retake_equip_ms":     90,
    "retake_swing_ms":     230,
    "retake_place_ms":     180,
    "retake_interval_ms":  30,
    "box_turn_px":         1000,
    "edit_interval_ms":    50,
    "edit_open_ms":        12,
    "edit_wait_ms":        15,
    "edit_click_ms":       10,
    "edit_confirm_ms":     12,
}
cfg        = dict(DEFAULTS)
cfg_lock   = threading.Lock()
# "force" = test mode (ignore the Fortnite focus check). Never saved to disk.
state      = {"focused": False, "active": True, "mode": "IDLE", "force": False}
state_lock = threading.Lock()
stats      = {"taps": 0, "err": 0, "last_err": 0, "moves": 0}

# (key, label, min, max, description, group)
TIMINGS = [
    ("build_tap_ms",       "Build Tap",       5,  50,  "How long a build key is held per tap.",   "BUILD"),
    ("build_click_ms",     "Build Click",     5,  50,  "How long each left click is held.",       "BUILD"),
    ("retake_equip_ms",    "Retake Equip",    20, 200, "Wait for the pickaxe to come out.",       "RETAKE"),
    ("retake_swing_ms",    "Retake Swing",    40, 400, "Time for the pickaxe hit to land.",       "RETAKE"),
    ("retake_place_ms",    "Retake Place",    40, 400, "How long it spams wall placement.",       "RETAKE"),
    ("retake_interval_ms", "Retake Rest",     10, 300, "Pause between retake cycles (hold R).",   "RETAKE"),
    ("box_turn_px",        "Box Turn",       100, 3000, "Mouse counts for one 90 degree turn.",   "BOX"),
    ("edit_interval_ms",   "Edit Interval",   20, 200, "Time between edit cycles (hold G).",      "AUTO EDIT"),
    ("edit_open_ms",       "Edit Open",       5,  50,  "How long E is tapped to open edit.",      "AUTO EDIT"),
    ("edit_wait_ms",       "Edit Wait",       5,  50,  "Delay after opening edit, before click.", "AUTO EDIT"),
    ("edit_click_ms",      "Edit Click",      5,  50,  "Duration of the confirmation click.",     "AUTO EDIT"),
    ("edit_confirm_ms",    "Edit Confirm",    5,  50,  "Duration of the P key confirm tap.",      "AUTO EDIT"),
]
RANGES = {k: (lo, hi) for k, _, lo, hi, _, _ in TIMINGS}
UNITS  = {"box_turn_px": "px"}          # everything else is ms

PRESETS = {
    "Fast":     {"build_tap_ms": 8,  "build_click_ms": 8,
                 "retake_equip_ms": 60, "retake_swing_ms": 180, "retake_place_ms": 120,
                 "retake_interval_ms": 15,
                 "edit_interval_ms": 30, "edit_open_ms": 8, "edit_wait_ms": 8,
                 "edit_click_ms": 7, "edit_confirm_ms": 8},
    "Balanced": {k: DEFAULTS[k] for k in RANGES if k != "box_turn_px"},
    "Relaxed":  {"build_tap_ms": 20, "build_click_ms": 18,
                 "retake_equip_ms": 120, "retake_swing_ms": 300, "retake_place_ms": 240,
                 "retake_interval_ms": 70,
                 "edit_interval_ms": 90, "edit_open_ms": 18, "edit_wait_ms": 22,
                 "edit_click_ms": 15, "edit_confirm_ms": 18},
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "axiom_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for k, d in DEFAULTS.items():
        v = data.get(k)
        if isinstance(d, bool):
            if isinstance(v, bool):
                cfg[k] = v
        elif isinstance(v, int) and not isinstance(v, bool):
            lo, hi = RANGES[k]
            cfg[k] = max(lo, min(hi, v))
    kd = data.get("keys")
    if isinstance(kd, dict):
        for k in KEY_DEFAULTS:
            v = kd.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 254:
                keys[k] = v


def save_config():
    with cfg_lock:
        data = dict(cfg)
    data["keys"] = dict(keys)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False


# ── Low-level helpers ─────────────────────────────────────────────────────────
physical = set()      # keys / buttons PHYSICALLY held (our own tagged events are ignored)
HOOK_OK  = False
_scan    = {}
def _build_names():
    n = {}
    for c in range(0x30, 0x3A):
        n[c] = chr(c)
    for c in range(0x41, 0x5B):
        n[c] = chr(c)
    for i in range(24):
        n[0x70 + i] = "F%d" % (i + 1)
    for i in range(10):
        n[0x60 + i] = "Num %d" % i
    n.update({
        5: "MB4", 6: "MB5", 0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter",
        0x14: "Caps", 0x1B: "Esc", 0x20: "Space", 0x21: "PgUp", 0x22: "PgDn",
        0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right",
        0x28: "Down", 0x2C: "PrtSc", 0x2D: "Ins", 0x2E: "Del",
        0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -", 0x6E: "Num .", 0x6F: "Num /",
        0x90: "NumLk", 0x91: "ScrLk",
        0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl",
        0xA4: "LAlt", 0xA5: "RAlt",
        0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
        0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0xE2: "<",
    })
    return n


_NAMES = _build_names()
CAPTURE_VKS = list(_NAMES)          # every key the rebind capture listens for


def vk_name(vk):
    return _NAMES.get(vk, "0x%02X" % vk)


def kn(name):
    """Display name of the key currently bound to `name`."""
    return vk_name(keys[name])


TRIGGER_ROWS = [
    ("build_1",    "Build key 1",  "tap-spams this build key + click"),
    ("build_2",    "Build key 2",  "tap-spams this build key + click"),
    ("build_3",    "Build key 3",  "tap-spams this build key + click"),
    ("build_4",    "Build key 4",  "tap-spams this build key + click"),
    ("build_5",    "Build key 5",  "tap-spams this build key + click"),
    ("auto_edit",  "Auto Edit",    "hold - runs the edit loop"),
    ("edit_click", "Edit Click",   "hold - holds left click"),
    ("retake",     "Wall Retake",  "hold - pickaxe hit, then wall"),
    ("box",        "Box",          "tap - builds a box around you"),
    ("pause",      "Pause",        "pause / resume everything"),
]
GAME_ROWS = [
    ("pickaxe",    "Pickaxe",      "your Harvesting Tool key"),
    ("wall",       "Wall",         "your Wall build key"),
    ("floor",      "Floor",        "your Floor build key"),
    ("roof",       "Roof / Cone",  "your Roof build key"),
    ("edit_game",  "Edit",         "pressed by Auto Edit to open edit"),
    ("confirm",    "Edit confirm", "pressed by Auto Edit to confirm"),
]
TRIGGER_NAMES = [r[0] for r in TRIGGER_ROWS]
LABELS = {r[0]: r[1] for r in TRIGGER_ROWS + GAME_ROWS}

if IS_WIN:
    from ctypes import wintypes
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUT_U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_U)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.argtypes    = [ctypes.c_void_p, ctypes.c_int,
                                         wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype     = ctypes.c_ssize_t
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                         ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype  = ctypes.c_void_p
    user32.SendInput.argtypes         = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype          = wintypes.UINT
    # 64-bit safe signatures (default int restype truncates handles / garbles SHORT)
    user32.GetForegroundWindow.restype       = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype  = wintypes.DWORD
    user32.GetWindowTextW.argtypes           = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
    user32.GetAsyncKeyState.argtypes         = [ctypes.c_int]
    user32.GetAsyncKeyState.restype          = ctypes.c_short
    user32.MapVirtualKeyW.argtypes           = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype            = wintypes.UINT
    user32.GetMessageW.argtypes              = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
                                                wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype               = ctypes.c_int

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype  = ctypes.c_void_p
    kernel32.OpenProcess.argtypes      = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype       = ctypes.c_void_p
    kernel32.CloseHandle.argtypes      = [ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                    wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype  = wintypes.BOOL
    kernel32.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                                wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateWaitableTimerExW.restype  = ctypes.c_void_p
    kernel32.SetWaitableTimer.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong),
                                          ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.BOOL]
    kernel32.SetWaitableTimer.restype  = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype  = wintypes.DWORD

    def is_admin():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def relaunch_as_admin():
        """Re-run this script elevated (UAC prompt). True if Windows accepted it."""
        try:
            shell = ctypes.windll.shell32
            shell.ShellExecuteW.restype = ctypes.c_void_p
            script = os.path.abspath(__file__)
            rest = " ".join('"%s"' % a for a in sys.argv[1:])
            params = ('"%s" %s' % (script, rest)).strip()
            r = shell.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
            return (r or 0) > 32
        except Exception:
            return False

    def input_hook_thread():
        """Tracks PHYSICAL key state. Events tagged with MAGIC (our own taps) are
        ignored, so the macro tapping C never cancels you holding C."""
        global HOOK_OK

        def _kb(n, w, l):
            try:
                if n >= 0:
                    k = ctypes.cast(l, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if k.dwExtraInfo != MAGIC:
                        if w == 0x100 or w == 0x104:
                            physical.add(k.vkCode)
                        elif w == 0x101 or w == 0x105:
                            physical.discard(k.vkCode)
            except Exception:
                pass
            return user32.CallNextHookEx(None, n, w, l)

        def _ms(n, w, l):
            try:
                if n >= 0 and (w == 0x20B or w == 0x20C):       # XBUTTON down / up
                    m = ctypes.cast(l, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if m.dwExtraInfo != MAGIC:
                        which = (m.mouseData >> 16) & 0xFFFF
                        vk = VK_XBUTTON1 if which == 1 else VK_XBUTTON2
                        if w == 0x20B:
                            physical.add(vk)
                        else:
                            physical.discard(vk)
            except Exception:
                pass
            return user32.CallNextHookEx(None, n, w, l)

        kb_proc, ms_proc = HOOKPROC(_kb), HOOKPROC(_ms)     # keep references alive
        hmod = kernel32.GetModuleHandleW(None)
        h1 = user32.SetWindowsHookExW(13, kb_proc, hmod, 0)
        h2 = user32.SetWindowsHookExW(14, ms_proc, hmod, 0)
        HOOK_OK = bool(h1 and h2)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _submit(i):
        if user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) != 1:
            stats["err"] += 1
            stats["last_err"] = ctypes.get_last_error()

    def _send_key(vk, up):
        """Scancode input - games that ignore plain VK events accept this."""
        scan = _scan.get(vk)
        if scan is None:
            scan = _scan[vk] = user32.MapVirtualKeyW(vk, 0)
        i = INPUT(type=1)
        flags = KEYEVENTF_SCANCODE
        if up:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        i.ki = KEYBDINPUT(0, scan, flags, 0, MAGIC)
        _submit(i)

    def _send_mouse(flags, data=0):
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(0, 0, data, flags, 0, MAGIC)
        _submit(i)

    def _send_move(dx, dy=0):
        """Relative mouse movement (camera turn)."""
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, MAGIC)
        _submit(i)

    def init_timing():
        """Ask Windows for 1 ms timer resolution (fallback when no hi-res timer)."""
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
else:
    def is_admin():
        return False

    def relaunch_as_admin():
        return False

    def _send_key(vk, up):
        pass

    def _send_mouse(flags, data=0):
        pass

    def _send_move(dx, dy=0):
        pass

    def init_timing():
        pass

ADMIN = is_admin()


def send_vk(vk, up):
    """Press / release a bound input: MB4 / MB5 go out as mouse buttons, the rest
    as keyboard scancodes."""
    if vk == VK_XBUTTON1:
        _send_mouse(MOUSEEVENTF_XUP if up else MOUSEEVENTF_XDOWN, 1)
    elif vk == VK_XBUTTON2:
        _send_mouse(MOUSEEVENTF_XUP if up else MOUSEEVENTF_XDOWN, 2)
    else:
        _send_key(vk, up)


# ── Precise sleep ─────────────────────────────────────────────────────────────
# time.sleep() on Windows rounds to ~15.6 ms, which turned 13 ms taps into long
# overlapping holds. A high-resolution waitable timer (per thread) fixes that.
_tls = threading.local()


def _timer_handle():
    h = getattr(_tls, "h", None)
    if h is None:
        h = kernel32.CreateWaitableTimerExW(None, None, 0x2, 0x1F0003) or 0
        _tls.h = h
    return h


def sleep_s(sec):
    if sec <= 0:
        return
    if IS_WIN:
        h = _timer_handle()
        if h:
            due = ctypes.c_longlong(-int(sec * 1e7))
            if kernel32.SetWaitableTimer(h, ctypes.byref(due), 0, None, None, False):
                kernel32.WaitForSingleObject(h, 0xFFFFFFFF)
                return
    time.sleep(sec)


# ── Input state / actions ─────────────────────────────────────────────────────
def key_down(vk):
    if not IS_WIN:
        return False
    if HOOK_OK:
        return vk in physical
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def resync_physical():
    """Re-read real key state (called when the game gains focus, macro is idle)."""
    if not (IS_WIN and HOOK_OK):
        return
    for vk in set(keys.values()):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            physical.add(vk)
        else:
            physical.discard(vk)


def held_names():
    try:
        down = tuple(physical)
    except RuntimeError:
        return []
    bound = set(keys.values())
    return sorted(vk_name(k) for k in down if k in bound)


def is_fortnite_focused():
    if not IS_WIN:
        return False
    if state["force"]:                      # test mode from the Info page
        return True
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        buf  = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value).lower()
        kernel32.CloseHandle(handle)
    if exe:
        return exe.startswith("fortniteclient")
    # process info hidden (anticheat / elevation) -> fall back to the window title
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    return title.value.strip().lower().startswith("fortnite")


def tap_key(vk, dur_ms):
    send_vk(vk, False)
    sleep_s(dur_ms / 1000)
    send_vk(vk, True)


def left_click(dur_ms):
    _send_mouse(MOUSEEVENTF_LEFTDOWN)
    sleep_s(dur_ms / 1000)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def lmb_down():
    _send_mouse(MOUSEEVENTF_LEFTDOWN)


def lmb_up():
    _send_mouse(MOUSEEVENTF_LEFTUP)


# ── Build engine ──────────────────────────────────────────────────────────────
GAP_S = 0.002          # pause between a release and the next press

# Several build keys can be held at once and they all share the left mouse
# button, so the click is reference-counted: LMB goes down when the first
# tapper needs it and only goes up when the last one is done.
_click_refs = 0


def _click_acquire():
    global _click_refs
    if _click_refs == 0:
        lmb_down()
    _click_refs += 1


def _click_release():
    global _click_refs
    if _click_refs > 0:
        _click_refs -= 1
        if _click_refs == 0:
            lmb_up()


class Tapper:
    """One build input (key or mouse side button) driven by a tiny state machine,
    so taps never overlap and nothing ever blocks the logic loop.
    Cycle: key down -> key up -> left click down -> left click up -> repeat.
    It is bound to a name in `keys`, so rebinding takes effect immediately."""
    __slots__ = ("name", "cur", "down", "clk", "armed", "next_t", "up_t", "clk_up")

    def __init__(self, name):
        self.name = name
        self.cur = 0            # vk of the press currently in flight
        self.down = False       # is our injected "key down" currently active
        self.clk = False        # is our injected left click currently active
        self.armed = False      # have we seen this physical press yet
        self.next_t = 0.0
        self.up_t = 0.0
        self.clk_up = 0.0

    def vk(self):
        return keys[self.name]

    def press(self):
        self.cur = self.vk()
        send_vk(self.cur, False)

    def release(self):
        send_vk(self.cur or self.vk(), True)


TAPPERS = [Tapper("build_%d" % i) for i in range(1, 6)]


def held_now(t, now):
    """Is this build input physically held by the user?"""
    vk = t.vk()
    if HOOK_OK:
        return vk in physical
    # Fallback (hook unavailable): our own injected release looks like the user
    # letting go, so bridge that short gap (including the click phase) instead
    # of stopping after one tap.
    real = bool(user32.GetAsyncKeyState(vk) & 0x8000) if IS_WIN else False
    if not real and t.armed and not t.down and now < t.next_t + 0.03:
        return True
    return real


def build_step(enabled, tap_s, click_on, click_s):
    """Advance every tapper. Returns True while any build input is being spammed."""
    now = time.perf_counter()
    busy = False
    for t in TAPPERS:
        if enabled and held_now(t, now):
            busy = True
            if not t.armed:
                # first frame of a physical press: the real key-down already
                # reached the game, so just schedule the release of that press
                t.armed = True
                t.cur = t.vk()
                t.down = True
                t.up_t = now + tap_s
            elif t.down:
                if now >= t.up_t:
                    t.release()
                    t.down = False
                    if click_on:
                        # piece is selected -> click to place it
                        _click_acquire()
                        t.clk = True
                        t.clk_up = now + click_s
                        t.next_t = now + click_s + GAP_S
                    else:
                        t.next_t = now + GAP_S
            elif t.clk:
                if now >= t.clk_up:
                    _click_release()
                    t.clk = False
                    t.next_t = now + GAP_S
            elif now >= t.next_t:
                t.press()
                t.down = True
                t.up_t = now + tap_s
                stats["taps"] += 1
        else:
            if t.down:
                t.release()
                t.down = False
            if t.clk:
                _click_release()
                t.clk = False
            t.armed = False
    return busy


def release_all_build():
    for t in TAPPERS:
        if t.down:
            t.release()
            t.down = False
        if t.clk:
            _click_release()
            t.clk = False
        t.armed = False


# ── Wall Retake engine ────────────────────────────────────────────────────────
class Retaker:
    """Non-blocking wall retake while the Retake trigger key is held.

    Cycle:
      PICK      press pickaxe key
      PICK_UP   release it, wait `equip` so the pickaxe is really in hand
      EQUIP     hold left click RETAKE_HIT_MS (swing)
      SWING_UP  release the click, wait `swing` so the hit actually lands
                (switching away too early cancels the swing = wall never breaks);
                repeat EQUIP / SWING_UP RETAKE_SWINGS times
      SWING     press wall key
      WALL_UP   release it, give build mode 30 ms to come up, open the place window
      PLACE     click to place the wall, repeatedly, until the window closes:
                the enemy wall breaks at some random moment inside the window
                and the next click drops yours into the gap
      PLACE_UP  release that click, short gap, back to PLACE
      ...then REST for `rest`, and the whole thing starts over.
    Nothing ever sleeps, so letting go of R stops it on the very next loop."""

    MIN_TAP = 0.025         # never tap a key shorter than ~1.5 frames

    def __init__(self):
        self.running = False
        self.phase = "PICK"
        self.t = 0.0
        self.key = 0            # vk we are currently holding down (0 = none)
        self.clk = False        # are we holding a ref on the left mouse button
        self.place_end = 0.0
        self.swings = 0

    def _key_down(self, vk, now, dur):
        send_vk(vk, False)
        self.key = vk
        self.t = now + max(dur, self.MIN_TAP)

    def _key_up(self):
        if self.key:
            send_vk(self.key, True)
            self.key = 0

    def _click_down(self, now, dur):
        _click_acquire()
        self.clk = True
        self.t = now + dur

    def _click_up(self):
        if self.clk:
            _click_release()
            self.clk = False

    def step(self, now, tap_s, equip_s, click_s, swing_s, place_s, rest_s):
        if not self.running:
            self.running = True
            self.phase = "PICK"
            self.t = now
        if now < self.t:
            return
        p = self.phase
        if p == "PICK":
            self.swings = 0
            self._key_down(keys["pickaxe"], now, tap_s)
            self.phase = "PICK_UP"
        elif p == "PICK_UP":
            self._key_up()
            self.t = now + equip_s
            self.phase = "EQUIP"
        elif p == "EQUIP":
            self._click_down(now, RETAKE_HIT_MS / 1000)
            self.phase = "SWING_UP"
        elif p == "SWING_UP":
            self._click_up()
            self.swings += 1
            self.t = now + swing_s              # let the hit land before anything else
            self.phase = "EQUIP" if self.swings < RETAKE_SWINGS else "SWING"
        elif p == "SWING":
            self._key_down(keys["wall"], now, tap_s)
            self.phase = "WALL_UP"
        elif p == "WALL_UP":
            self._key_up()
            self.t = now + 0.03                 # build mode needs a moment to come up
            self.place_end = self.t + place_s
            self.phase = "PLACE"
        elif p == "PLACE":
            if now >= self.place_end:
                self.t = now + rest_s
                self.phase = "PICK"
            else:
                self._click_down(now, max(click_s, 0.020))
                self.phase = "PLACE_UP"
        elif p == "PLACE_UP":
            self._click_up()
            self.t = now + max(click_s, 0.015)
            self.phase = "PLACE"

    def abort(self):
        """Stop right now and let go of everything we were holding."""
        if self.running or self.key or self.clk:
            self._key_up()
            self._click_up()
            self.running = False


retaker = Retaker()


# ── Box engine ────────────────────────────────────────────────────────────────
def _move_steps(dx, dy):
    """Split a camera move into many small relative mouse events."""
    n = max(4, -(-max(abs(dx), abs(dy)) // BOX_STEP_PX))        # ceil division
    out = []
    for j in range(n):
        mx = round(dx * (j + 1) / n) - round(dx * j / n)
        my = round(dy * (j + 1) / n) - round(dy * j / n)
        out.append(("move", (mx, my)))
        out.append(("wait", 0.0015))
    out.append(("wait", BOX_SETTLE_MS / 1000))
    return out


def build_box_sequence(turn_px):
    """Steps for one box. Walls snap to the grid edge you are aiming at, so the
    camera turns 90 degrees between walls (4 turns = full circle = back to start).
    The floor / roof go on top, so the camera tilts up for them and back after.
    Step kinds: ("tap", vk) / ("click", None) / ("move", (dx, dy)) / ("wait", sec)."""
    sel = BOX_SELECT_MS / 1000
    seq = [("tap", keys["wall"]), ("wait", sel)]
    for _ in range(4):
        seq.append(("click", None))
        if turn_px > 0:
            seq += _move_steps(turn_px * BOX_TURN_DIR, 0)
    if BOX_FLOOR or BOX_ROOF:
        look = round(turn_px * BOX_LOOK_RATIO) * BOX_LOOK_DIR if turn_px > 0 else 0
        if look:
            seq += _move_steps(0, look)
        if BOX_FLOOR:
            seq += [("tap", keys["floor"]), ("wait", sel), ("click", None)]
        if BOX_ROOF:
            seq += [("tap", keys["roof"]), ("wait", sel), ("click", None)]
        if look:
            seq += _move_steps(0, -look)
    return seq


class Boxer:
    """Runs the box sequence once per tap of the Box key. Non-blocking, like Retaker:
    every call to step() advances at most one thing and never sleeps."""
    MIN_TAP = 0.025

    def __init__(self):
        self.running = False
        self.steps = []
        self.i = 0
        self.sub = 0            # 0 = press phase, 1 = release phase
        self.t = 0.0
        self.key = 0
        self.clk = False

    def start(self, steps, now):
        self.steps = steps
        self.i = 0
        self.sub = 0
        self.t = now
        self.running = True

    def _release(self):
        if self.key:
            send_vk(self.key, True)
            self.key = 0
        if self.clk:
            _click_release()
            self.clk = False

    def step(self, now, tap_s, click_s):
        if not self.running or now < self.t:
            return
        if self.i >= len(self.steps):
            self._release()
            self.running = False
            return
        gap = BOX_GAP_MS / 1000
        kind, arg = self.steps[self.i]
        if kind == "tap":
            if self.sub == 0:
                send_vk(arg, False)
                self.key = arg
                self.t = now + max(tap_s, self.MIN_TAP)
                self.sub = 1
            else:
                self._release()
                self.t = now + gap
                self.sub = 0
                self.i += 1
        elif kind == "click":
            if self.sub == 0:
                _click_acquire()
                self.clk = True
                self.t = now + max(click_s, 0.020)
                self.sub = 1
            else:
                self._release()
                self.t = now + gap
                self.sub = 0
                self.i += 1
        elif kind == "move":
            _send_move(arg[0], arg[1])
            stats["moves"] += 1
            self.t = now
            self.i += 1
        elif kind == "wait":
            self.t = now + arg
            self.i += 1
        else:
            self.i += 1

    def abort(self):
        if self.running or self.key or self.clk:
            self._release()
        self.running = False


boxer = Boxer()


# ── Logic thread ──────────────────────────────────────────────────────────────
def logic_thread():
    in_fortnite    = False
    was_focused    = False
    check_timer    = 0.0
    last_edit_tick = 0.0
    clicking       = False
    box_latched    = False

    while True:
        now = time.perf_counter()
        with state_lock:
            is_active = state["active"]

        if now - check_timer > 0.1:
            in_fortnite = is_fortnite_focused()
            check_timer = now
            with state_lock:
                state["focused"] = in_fortnite

        if not in_fortnite or not is_active:
            release_all_build()
            retaker.abort()
            boxer.abort()
            if clicking:
                lmb_up()
                clicking = False
            was_focused = False
            with state_lock:
                state["mode"] = "IDLE"
            sleep_s(0.01)
            continue

        if not was_focused:
            resync_physical()
            was_focused = True

        with cfg_lock:
            build_en     = cfg["build_enabled"]
            build_cl_en  = cfg["build_click_enabled"]
            retake_en    = cfg["wall_retake_enabled"]
            auto_edit_en = cfg["auto_edit_enabled"]
            edit_cl_en   = cfg["edit_click_enabled"]
            tap_ms       = cfg["build_tap_ms"]
            bc_ms        = cfg["build_click_ms"]
            re_ms        = cfg["retake_equip_ms"]
            rs_ms        = cfg["retake_swing_ms"]
            rp_ms        = cfg["retake_place_ms"]
            rt_ms        = cfg["retake_interval_ms"]
            ei_ms        = cfg["edit_interval_ms"]
            eo_ms        = cfg["edit_open_ms"]
            ew_ms        = cfg["edit_wait_ms"]
            ec_ms        = cfg["edit_click_ms"]
            ecf_ms       = cfg["edit_confirm_ms"]
            box_en       = cfg["box_enabled"]
            bt_px        = cfg["box_turn_px"]

        mode   = "ACTIVE"
        g_held = key_down(keys["auto_edit"])
        r_held = retake_en and key_down(keys["retake"])

        # BOX: one run per tap of the Box key (re-arms once the key is released)
        if box_en and key_down(keys["box"]):
            if (not box_latched and not boxer.running
                    and not (g_held and auto_edit_en) and not r_held):
                boxer.start(build_box_sequence(bt_px), now)
            box_latched = True
        else:
            box_latched = False

        if boxer.running:
            mode = "BOX"
            if clicking:                    # don't fight EDIT CLICK for the mouse
                lmb_up()
                clicking = False
            boxer.step(now, tap_ms / 1000, bc_ms / 1000)
        elif g_held and auto_edit_en:
            mode = "AUTO-EDIT"
            if now - last_edit_tick >= ei_ms / 1000:
                tap_key(keys["edit_game"], eo_ms)
                sleep_s(ew_ms / 1000)
                left_click(ec_ms)
                sleep_s(ecf_ms / 1000)
                tap_key(keys["confirm"], 10)
                last_edit_tick = now
        elif r_held:
            mode = "RETAKE"
            if clicking:                    # don't fight EDIT CLICK for the mouse
                lmb_up()
                clicking = False
            retaker.step(now, tap_ms / 1000, re_ms / 1000, bc_ms / 1000,
                         rs_ms / 1000, rp_ms / 1000, rt_ms / 1000)
        else:
            if edit_cl_en:
                e_held = key_down(keys["edit_click"])
                if e_held and not clicking:
                    lmb_up()
                    sleep_s(0.005)
                    lmb_down()
                    clicking = True
                elif not e_held and clicking:
                    lmb_up()
                    clicking = False
            elif clicking:
                lmb_up()
                clicking = False
            if clicking:
                mode = "EDIT-CLICK"

        if mode != "RETAKE":
            retaker.abort()                 # R released (or another mode took over)

        # auto place steps aside while EDIT CLICK is holding the mouse button;
        # the build bot is fully paused while retake owns the keys and the mouse
        click_on = build_cl_en and not clicking
        busy = build_step(build_en and mode not in ("RETAKE", "BOX"), tap_ms / 1000, click_on, bc_ms / 1000)
        if busy and mode == "ACTIVE":
            mode = "BUILD"

        with state_lock:
            state["mode"] = mode

        sleep_s(0.0005)


# ── Palette / fonts ───────────────────────────────────────────────────────────
BG       = "#0b0b12"
SIDE     = "#08080d"
SIDE_HOV = "#0f0f18"
SIDE_ACT = "#14141f"
CARD     = "#12121c"
CARD2    = "#191927"
HOVER    = "#222238"
ACCENT   = "#c850f0"
ACCENT_D = "#9a2fc4"
FG       = "#ececfa"
FG_MID   = "#9494b8"
FG_DIM   = "#55556f"
BORDER   = "#1f1f33"
BORDER2  = "#2c2c47"
GREEN    = "#00e5a0"
RED      = "#ff3366"
YELLOW   = "#ffcc00"
TROUGH   = "#1c1c2c"

MODE_COL = {
    "IDLE":       FG_DIM,
    "ACTIVE":     GREEN,
    "BUILD":      YELLOW,
    "AUTO-EDIT":  ACCENT,
    "EDIT-CLICK": "#7aa2ff",
    "RETAKE":     "#ff9f43",
    "BOX":        "#4dd0e1",
}

FONT = "Segoe UI"


def fnt(size=9, bold=False):
    return (FONT, size, "bold") if bold else (FONT, size)


def mix(a, b, t):
    """Blend two #rrggbb colours (t=0 -> a, t=1 -> b)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
           x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
           x1, y2, x1, y2-r, x1, y1+r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def watch(var, widget, fn):
    """Call fn() when var changes; auto-detach when the widget is destroyed."""
    def cb(*_):
        try:
            fn()
        except tk.TclError:
            pass
    tid = var.trace_add("write", cb)

    def gone(e):
        if e.widget is widget:
            try:
                var.trace_remove("write", tid)
            except Exception:
                pass
    widget.bind("<Destroy>", gone, add="+")


def card(parent, **kw):
    return tk.Frame(parent, bg=CARD, highlightthickness=1,
                    highlightbackground=BORDER, **kw)


def make_button(parent, text, command, primary=False):
    base  = ACCENT if primary else CARD2
    hover = ACCENT_D if primary else HOVER
    fg    = "#ffffff" if primary else FG_MID
    b = tk.Label(parent, text=text, bg=base, fg=fg, font=fnt(8, True),
                 padx=14, pady=6, cursor="hand2")
    b.bind("<Enter>",    lambda e: b.config(bg=hover, fg="#ffffff"))
    b.bind("<Leave>",    lambda e: b.config(bg=base, fg=fg))
    b.bind("<Button-1>", lambda e: command())
    return b


def chip(parent, text, bg=CARD2, fg=ACCENT):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=fnt(8, True),
                    padx=8, pady=3)


# ── Widgets ───────────────────────────────────────────────────────────────────
class Toggle(tk.Canvas):
    W, H = 46, 24

    def __init__(self, parent, var, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.var  = var
        self.pos  = 1.0 if var.get() else 0.0
        self._job = None
        self.bind("<Button-1>", lambda e: var.set(not var.get()))
        self.bind("<Destroy>",  self._cancel, add="+")
        watch(var, self, self._animate)
        self._draw()

    def _cancel(self, _=None):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _animate(self):
        self._cancel()
        target = 1.0 if self.var.get() else 0.0
        self.pos += (target - self.pos) * 0.4
        if abs(target - self.pos) < 0.03:
            self.pos = target
        else:
            self._job = self.after(16, self._animate)
        self._draw()

    def _draw(self):
        self.delete("all")
        t = self.pos
        rrect(self, 1, 3, self.W - 1, self.H - 3, 9,
              fill=mix(BORDER2, ACCENT, t), outline="")
        x = 13 + t * (self.W - 26)
        cy = self.H / 2
        self.create_oval(x - 8, cy - 8, x + 8, cy + 8,
                         fill=mix(FG_DIM, "#ffffff", t), outline="")


class Slider(tk.Canvas):
    H = 30

    def __init__(self, parent, lo, hi, var, default, bg=CARD):
        super().__init__(parent, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.lo, self.hi, self.var, self.default = lo, hi, var, default
        self.w   = 200
        self.hot = False
        self.bind("<Configure>",       self._resize)
        self.bind("<ButtonPress-1>",   self._set)
        self.bind("<B1-Motion>",       self._set)
        self.bind("<Enter>",           lambda e: self._hover(True))
        self.bind("<Leave>",           lambda e: self._hover(False))
        self.bind("<MouseWheel>",      self._wheel)
        self.bind("<Double-Button-1>", lambda e: var.set(self.default))
        watch(var, self, self._draw)

    def _resize(self, e):
        self.w = e.width
        self._draw()

    def _hover(self, on):
        self.hot = on
        self._draw()

    def _clamp(self, v):
        return max(self.lo, min(self.hi, v))

    def _wheel(self, e):
        self.var.set(self._clamp(self.var.get() + (1 if e.delta > 0 else -1)))

    def _set(self, e):
        frac = (e.x - 12) / max(1, self.w - 24)
        frac = max(0.0, min(1.0, frac))
        self.var.set(round(self.lo + frac * (self.hi - self.lo)))

    def _draw(self):
        self.delete("all")
        cy, x0, x1 = self.H // 2, 12, self.w - 12
        if x1 - x0 < 20:
            return
        frac = (self.var.get() - self.lo) / max(1, self.hi - self.lo)
        px = x0 + max(0.0, min(1.0, frac)) * (x1 - x0)
        rrect(self, x0, cy - 3, x1, cy + 3, 3, fill=TROUGH, outline="")
        if px > x0 + 6:
            rrect(self, x0, cy - 3, px, cy + 3, 3, fill=ACCENT, outline="")
        if self.hot:
            g = mix(self.cget("bg"), ACCENT, 0.22)
            self.create_oval(px - 12, cy - 12, px + 12, cy + 12, fill=g, outline="")
        self.create_oval(px - 7, cy - 7, px + 7, cy + 7, fill=ACCENT, outline="")
        self.create_oval(px - 3, cy - 3, px + 3, cy + 3, fill="#ffffff", outline="")


class PowerButton(tk.Canvas):
    S = 112

    def __init__(self, parent, var, bg=CARD):
        super().__init__(parent, width=self.S, height=self.S, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.var    = var
        self.accent = ACCENT
        self.hot    = False
        self.bind("<Button-1>", lambda e: var.set(not var.get()))
        self.bind("<Enter>",    lambda e: self._hover(True))
        self.bind("<Leave>",    lambda e: self._hover(False))
        watch(var, self, self.draw)
        self.draw()

    def _hover(self, on):
        self.hot = on
        self.draw()

    def set_color(self, c):
        if c != self.accent:
            self.accent = c
            self.draw()

    def draw(self):
        self.delete("all")
        on = self.var.get()
        c  = self.accent if on else FG_DIM
        cx = cy = self.S / 2
        if on:
            self.create_oval(cx - 52, cy - 52, cx + 52, cy + 52,
                             outline=mix(CARD, c, 0.18), width=6)
        self.create_oval(cx - 44, cy - 44, cx + 44, cy + 44,
                         fill=CARD2 if not self.hot else HOVER,
                         outline=c, width=4 if self.hot else 3)
        self.create_arc(cx - 16, cy - 13, cx + 16, cy + 19, start=125, extent=290,
                        style="arc", outline=c, width=4)
        self.create_line(cx, cy - 18, cx, cy + 2, fill=c, width=4, capstyle="round")


# ── Main App ──────────────────────────────────────────────────────────────────
class App(tk.Tk):
    W, H = 800, 720
    SIDE = 176
    PAGES = ("Dashboard", "Timings", "Keybinds", "Info")
    SUBS = {
        "Dashboard": "controls & presets",
        "Timings":   "fine tuning, applied live",
        "Keybinds":  "click a key, then press the new one",
        "Info":      "status, keybinds, window",
    }

    def __init__(self):
        super().__init__()
        self.title("axiom.cc")
        if IS_WIN:
            self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{max(0, (sh - self.H) // 2)}")
        self.resizable(False, False)

        self._drag_x = self._drag_y = 0
        self._live = []
        self._page = "Dashboard"
        self._save_job = None
        self._toast_job = None
        self._prev_f8 = False
        self._cap = None
        self._cap_job = None
        self._cap_armed = False

        self._make_vars()
        self._build_shell()
        self.show_page("Dashboard")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()
        self._poll_hotkey()
        if IS_WIN:
            self.after(80, self._fix_taskbar)

    # ── Variables (single source of truth, applied live) ──────────────────────
    def _make_vars(self):
        self.vars, self.vstr = {}, {}
        for k, d in DEFAULTS.items():
            is_bool = isinstance(d, bool)
            var = tk.BooleanVar(value=cfg[k]) if is_bool else tk.IntVar(value=cfg[k])
            self.vars[k] = var
            self.vstr[k] = tk.StringVar(value="" if is_bool else f"{cfg[k]} {UNITS.get(k, 'ms')}")
            var.trace_add("write", lambda *_, k=k: self._on_cfg_var(k))

        self.active_var = tk.BooleanVar(value=True)
        self.active_var.trace_add("write", self._on_active)

        self.force_var = tk.BooleanVar(value=False)     # test mode, never saved
        self.force_var.trace_add("write", self._on_force)

        self.topmost_var = tk.BooleanVar(value=True)
        self.topmost_var.trace_add("write", lambda *_: self.attributes(
            "-topmost", bool(self.topmost_var.get())))

        self.opacity_var = tk.IntVar(value=100)
        self.opacity_str = tk.StringVar(value="100%")
        self.opacity_var.trace_add("write", self._on_opacity)

    def _on_cfg_var(self, k):
        var = self.vars[k]
        try:
            v = var.get()
        except tk.TclError:
            return
        is_bool = isinstance(DEFAULTS[k], bool)
        with cfg_lock:
            cfg[k] = bool(v) if is_bool else int(v)
        if not is_bool:
            self.vstr[k].set(f"{int(v)} {UNITS.get(k, 'ms')}")
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._autosave)

    def _autosave(self):
        self._save_job = None
        ok = save_config()
        self.toast("saved" if ok else "couldn't save config", GREEN if ok else RED)

    def _on_active(self, *_):
        on = bool(self.active_var.get())
        with state_lock:
            state["active"] = on

    def _on_force(self, *_):
        on = bool(self.force_var.get())
        with state_lock:
            state["force"] = on
        self.toast("test mode ON - macros fire in ANY window" if on else "test mode off",
                   YELLOW if on else GREEN)

    def _on_opacity(self, *_):
        v = int(self.opacity_var.get())
        self.opacity_str.set(f"{v}%")
        self.attributes("-alpha", v / 100)

    # ── Window chrome ─────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x = e.x_root - self.winfo_x()
        self._drag_y = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    def _draggable(self, *widgets):
        for w in widgets:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

    def _fix_taskbar(self):
        """Give the borderless window a taskbar button."""
        try:
            hwnd = user32.GetParent(self.winfo_id())
            style = user32.GetWindowLongW(hwnd, -20)
            style = (style & ~0x80) | 0x40000
            user32.SetWindowLongW(hwnd, -20, style)
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            pass

    def _minimize(self):
        if not IS_WIN:
            self.iconify()
            return
        self.update_idletasks()
        self.overrideredirect(False)
        self.iconify()
        self.bind("<Map>", self._restored)

    def _restored(self, e):
        if e.widget is self:
            self.unbind("<Map>")
            self.overrideredirect(True)
            self.after(20, self._fix_taskbar)

    def close(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        save_config()
        if IS_WIN:
            retaker.abort()
            boxer.abort()
            lmb_up()
            release_all_build()
        self.destroy()

    # ── Shell ─────────────────────────────────────────────────────────────────
    def _build_shell(self):
        # sidebar
        side = tk.Frame(self, bg=SIDE, width=self.SIDE)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        logo = tk.Frame(side, bg=SIDE)
        logo.pack(fill="x", padx=18, pady=(18, 22))
        row = tk.Frame(logo, bg=SIDE)
        row.pack(anchor="w")
        tk.Label(row, text="axiom", fg=FG, bg=SIDE, font=fnt(15, True)).pack(side="left")
        tk.Label(row, text=".cc", fg=ACCENT, bg=SIDE, font=fnt(15, True)).pack(side="left")
        tk.Label(logo, text="v2.11  macro suite", fg=FG_DIM, bg=SIDE,
                 font=fnt(7)).pack(anchor="w")
        self._draggable(side, logo, row)

        self._nav = {}
        for name in self.PAGES:
            r = tk.Frame(side, bg=SIDE, cursor="hand2")
            r.pack(fill="x", pady=1)
            ind = tk.Frame(r, bg=SIDE, width=3)
            ind.pack(side="left", fill="y")
            lbl = tk.Label(r, text=name, bg=SIDE, fg=FG_DIM, font=fnt(9, True),
                           anchor="w", padx=16, pady=9, cursor="hand2")
            lbl.pack(side="left", fill="x", expand=True)
            for w in (r, lbl):
                w.bind("<Button-1>", lambda e, n=name: self.show_page(n))
                w.bind("<Enter>",    lambda e, n=name: self._nav_hover(n, True))
                w.bind("<Leave>",    lambda e, n=name: self._nav_hover(n, False))
            self._nav[name] = (r, ind, lbl)

        # master switch at the bottom of the sidebar
        bottom = tk.Frame(side, bg=SIDE)
        bottom.pack(side="bottom", fill="x", padx=18, pady=18)
        tk.Frame(bottom, bg=BORDER, height=1).pack(fill="x", pady=(0, 14))
        line = tk.Frame(bottom, bg=SIDE)
        line.pack(fill="x")
        tk.Label(line, text="MACROS", fg=FG_MID, bg=SIDE,
                 font=fnt(8, True)).pack(side="left")
        Toggle(line, self.active_var, bg=SIDE).pack(side="right")
        self._pause_lbl = tk.Label(bottom, text="", fg=FG_DIM, bg=SIDE, font=fnt(7))
        self._pause_lbl.pack(anchor="w", pady=(6, 0))
        self._refresh_pause_label()

        # main column
        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        top = tk.Frame(main, bg=BG, height=54)
        top.pack(fill="x")
        top.pack_propagate(False)
        self._title = tk.Label(top, text="", fg=FG, bg=BG, font=fnt(14, True))
        self._title.pack(side="left", padx=(22, 8), pady=(12, 0))
        self._subtitle = tk.Label(top, text="", fg=FG_DIM, bg=BG, font=fnt(8))
        self._subtitle.pack(side="left", pady=(18, 0))
        self._draggable(top, self._title, self._subtitle)

        ctr = tk.Frame(top, bg=BG)
        ctr.pack(side="right", padx=10, pady=(8, 0))
        for txt, cmd, hov in (("─", self._minimize, FG_MID), ("✕", self.close, RED)):
            b = tk.Label(ctr, text=txt, fg=FG_DIM, bg=BG, font=fnt(10),
                         cursor="hand2", padx=8)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>",    lambda e, w=b, c=hov: w.config(fg=c))
            b.bind("<Leave>",    lambda e, w=b: w.config(fg=FG_DIM))

        foot = tk.Frame(main, bg=SIDE, height=30)
        foot.pack(side="bottom", fill="x")
        foot.pack_propagate(False)
        tk.Frame(foot, bg=BORDER, height=1).place(x=0, y=0, relwidth=1)
        self._f_mode = tk.Label(foot, text="●  IDLE", fg=FG_DIM, bg=SIDE, font=fnt(8))
        self._f_mode.pack(side="left", padx=(18, 14))
        self._f_game = tk.Label(foot, text="Fortnite  ○", fg=FG_DIM, bg=SIDE, font=fnt(8))
        self._f_game.pack(side="left")
        self._toast_lbl = tk.Label(foot, text="", fg=GREEN, bg=SIDE, font=fnt(8))
        self._toast_lbl.pack(side="right", padx=18)
        if not IS_WIN:
            tk.Label(foot, text="preview mode (no Windows)", fg=YELLOW, bg=SIDE,
                     font=fnt(8)).pack(side="right", padx=8)

        self.body = tk.Frame(main, bg=BG)
        self.body.pack(fill="both", expand=True)

    def _nav_hover(self, name, on):
        if name == self._page:
            return
        r, ind, lbl = self._nav[name]
        col = SIDE_HOV if on else SIDE
        r.config(bg=col)
        lbl.config(bg=col, fg=FG_MID if on else FG_DIM)

    def show_page(self, name):
        self._cancel_capture()
        self._page = name
        self._live.clear()
        for n, (r, ind, lbl) in self._nav.items():
            on = n == name
            r.config(bg=SIDE_ACT if on else SIDE)
            ind.config(bg=ACCENT if on else SIDE)
            lbl.config(bg=SIDE_ACT if on else SIDE, fg=FG if on else FG_DIM)
        for w in self.body.winfo_children():
            w.destroy()
        self._title.config(text=name)
        self._subtitle.config(text=self.SUBS[name])
        getattr(self, f"_page_{name.lower()}")()

    # ── Toast / status ────────────────────────────────────────────────────────
    def toast(self, text, color=GREEN):
        self._toast_lbl.config(text=f"✓ {text}" if color == GREEN else text, fg=color)
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(1800, lambda: self._toast_lbl.config(text=""))

    def _status(self):
        with state_lock:
            active, focused, mode = state["active"], state["focused"], state["mode"]
        if not active:
            return "PAUSED", FG_DIM, focused
        if not focused:
            return "WAITING FOR FORTNITE", YELLOW, focused
        return mode, MODE_COL.get(mode, FG_DIM), focused

    def _tick(self):
        text, col, focused = self._status()
        self._f_mode.config(text=f"●  {text}", fg=col)
        self._f_game.config(text=f"Fortnite  {'●' if focused else '○'}",
                            fg=GREEN if focused else FG_DIM)
        for fn in list(self._live):
            try:
                fn(text, col, focused)
            except tk.TclError:
                pass
        self.after(100, self._tick)

    def _poll_hotkey(self):
        down = key_down(keys["pause"])
        if down and not self._prev_f8:
            self.active_var.set(not self.active_var.get())
            self.toast("macros resumed" if self.active_var.get() else "macros paused",
                       GREEN if self.active_var.get() else YELLOW)
        self._prev_f8 = down
        self.after(30, self._poll_hotkey)

    # ── Rebinding ─────────────────────────────────────────────────────────────
    def _refresh_pause_label(self):
        self._pause_lbl.config(text=f"{kn('pause')} pauses anywhere")

    def _schedule_save(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._autosave)

    def _start_capture(self, name, btn):
        if not IS_WIN:
            self.toast("rebinding needs Windows (preview mode)", YELLOW)
            return
        self._cancel_capture()
        self._cap = (name, btn)
        self._cap_armed = False
        btn.config(text="press a key...", fg=YELLOW)
        self._cap_poll()

    def _cancel_capture(self):
        if self._cap_job:
            try:
                self.after_cancel(self._cap_job)
            except tk.TclError:
                pass
            self._cap_job = None
        if self._cap:
            name, btn = self._cap
            self._cap = None
            try:
                btn.config(text=kn(name), fg=ACCENT)
            except tk.TclError:
                pass

    def _cap_poll(self):
        self._cap_job = None
        if not self._cap:
            return
        down = [vk for vk in CAPTURE_VKS if user32.GetAsyncKeyState(vk) & 0x8000]
        if not self._cap_armed:
            if not down:                    # wait until everything is released first
                self._cap_armed = True
        elif down:
            vk = down[0]
            name = self._cap[0]
            if vk == VK_ESC:
                self._cancel_capture()
                return
            self._cap = None
            self._set_bind(name, vk)
            return
        self._cap_job = self.after(15, self._cap_poll)

    def _set_bind(self, name, vk):
        old = keys[name]
        msg = f"{LABELS[name]} = {vk_name(vk)}"
        if name in TRIGGER_NAMES:           # two triggers can't share a key -> swap
            for other in TRIGGER_NAMES:
                if other != name and keys[other] == vk:
                    keys[other] = old
                    msg += f"  (swapped with {LABELS[other]})"
        keys[name] = vk
        self._schedule_save()
        self._refresh_pause_label()
        if IS_WIN:
            resync_physical()
        self.show_page("Keybinds")
        self.toast(msg)

    def reset_keys(self):
        keys.update(KEY_DEFAULTS)
        self._schedule_save()
        self._refresh_pause_label()
        self.show_page("Keybinds")
        self.toast("keybinds restored")

    # ── Presets / reset ───────────────────────────────────────────────────────
    def apply_preset(self, name):
        for k, v in PRESETS[name].items():
            self.vars[k].set(v)
        self.toast(f"{name.lower()} preset")

    def reset_all(self):
        for k, d in DEFAULTS.items():
            self.vars[k].set(d)
        self.toast("defaults restored")

    # ── Page: DASHBOARD ───────────────────────────────────────────────────────
    def _page_dashboard(self):
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=(4, 16))

        topr = tk.Frame(wrap, bg=BG)
        topr.pack(fill="x")
        topr.columnconfigure(1, weight=1)

        # hero: master power
        hero = card(topr)
        hero.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        hin = tk.Frame(hero, bg=CARD, padx=26, pady=14)
        hin.pack(fill="both", expand=True)
        tk.Label(hin, text="MACRO ENGINE", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).pack()
        pb = PowerButton(hin, self.active_var)
        pb.pack(pady=(8, 6))
        st_lbl = tk.Label(hin, text="", fg=FG, bg=CARD, font=fnt(9, True))
        st_lbl.pack()
        game_lbl = tk.Label(hin, text="", fg=FG_DIM, bg=CARD, font=fnt(8))
        game_lbl.pack(pady=(2, 0))

        def live(text, col, focused):
            pb.set_color(col)
            st_lbl.config(text=text, fg=col)
            game_lbl.config(text="Fortnite detected" if focused else "Fortnite not in focus",
                            fg=GREEN if focused else FG_DIM)
        self._live.append(live)

        # feature rows
        col = tk.Frame(topr, bg=BG)
        col.grid(row=0, column=1, sticky="nsew")
        features = [
            ("BUILD BOT",   "build_enabled",       " / ".join(kn("build_%d" % i) for i in range(1, 6)) + " - tap-spams the build key"),
            ("AUTO PLACE",  "build_click_enabled", "Build bot also left-clicks to place each piece"),
            ("AUTO EDIT",   "auto_edit_enabled",   f"Hold {kn('auto_edit')} - runs the edit loop"),
            ("EDIT CLICK",  "edit_click_enabled",  f"Hold {kn('edit_click')} - holds left click while pressed"),
            ("WALL RETAKE", "wall_retake_enabled", f"Hold {kn('retake')} - pickaxe hit, then spams your wall into the gap"),
            ("BOX MACRO",   "box_enabled",         f"Tap {kn('box')} - 4 walls (turning 90 each) + floor on top"),
        ]
        for i, (label, key, hint) in enumerate(features):
            var = self.vars[key]
            rowc = card(col)
            rowc.pack(fill="x", pady=(0, 8 if i < len(features) - 1 else 0))
            strip = tk.Frame(rowc, width=3, bg=ACCENT if var.get() else CARD)
            strip.pack(side="left", fill="y")
            inner = tk.Frame(rowc, bg=CARD, padx=16, pady=10)
            inner.pack(side="left", fill="both", expand=True)
            left = tk.Frame(inner, bg=CARD)
            left.pack(side="left")
            tk.Label(left, text=label, fg=FG, bg=CARD, font=fnt(9, True)).pack(anchor="w")
            tk.Label(left, text=hint, fg=FG_DIM, bg=CARD, font=fnt(8)).pack(anchor="w", pady=(2, 0))
            Toggle(inner, var).pack(side="right")
            watch(var, strip, lambda s=strip, v=var: s.config(bg=ACCENT if v.get() else CARD))

        # presets
        pr = card(wrap)
        pr.pack(fill="x", pady=(12, 0))
        pin = tk.Frame(pr, bg=CARD, padx=16, pady=12)
        pin.pack(fill="x")
        tk.Label(pin, text="TIMING PRESET", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).pack(side="left", padx=(0, 14))
        for name in PRESETS:
            make_button(pin, name, lambda n=name: self.apply_preset(n)).pack(side="left", padx=(0, 6))
        make_button(pin, "Reset all", self.reset_all).pack(side="right")

        # hotkeys
        hk = card(wrap)
        hk.pack(fill="x", pady=(12, 0))
        hin2 = tk.Frame(hk, bg=CARD, padx=16, pady=12)
        hin2.pack(fill="x")
        tk.Label(hin2, text="HOTKEYS", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).pack(side="left", padx=(0, 14))
        hk_items = ((" ".join(kn("build_%d" % i) for i in range(1, 6)), "build"),
                    ("hold " + kn("auto_edit"), "auto edit"),
                    ("hold " + kn("edit_click"), "edit click"),
                    ("hold " + kn("retake"), "retake"),
                    ("tap " + kn("box"), "box"), (kn("pause"), "pause"))
        hk_grid = tk.Frame(hin2, bg=CARD)
        hk_grid.pack(side="left")
        for n, (kt, what) in enumerate(hk_items):
            cell = tk.Frame(hk_grid, bg=CARD)
            cell.grid(row=n // 3, column=n % 3, sticky="w", padx=(0, 14), pady=2)
            chip(cell, kt).pack(side="left")
            tk.Label(cell, text=what, fg=FG_DIM, bg=CARD, font=fnt(8)).pack(side="left", padx=(5, 0))

    # ── Page: TIMINGS ─────────────────────────────────────────────────────────
    def _page_timings(self):
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=(2, 6))
        wrap.columnconfigure(0, weight=1, uniform="t")
        wrap.columnconfigure(1, weight=1, uniform="t")

        for i, (key, label, lo, hi, desc, group) in enumerate(TIMINGS):
            c = card(wrap)
            c.grid(row=i // 2, column=i % 2, sticky="nsew",
                   padx=(0, 6) if i % 2 == 0 else (6, 0), pady=3)
            inner = tk.Frame(c, bg=CARD, padx=14, pady=5)
            inner.pack(fill="both", expand=True)

            hdr = tk.Frame(inner, bg=CARD)
            hdr.pack(fill="x")
            tk.Label(hdr, text=label, fg=FG, bg=CARD, font=fnt(9, True)).pack(side="left")
            chip(hdr, group, bg=mix(CARD, ACCENT, 0.15), fg=ACCENT).pack(side="left", padx=8)
            tk.Label(hdr, textvariable=self.vstr[key], fg=ACCENT, bg=CARD,
                     font=fnt(10, True)).pack(side="right")

            tk.Label(inner, text=desc, fg=FG_DIM, bg=CARD, font=fnt(8),
                     wraplength=250, justify="left").pack(anchor="w", pady=(2, 2))
            Slider(inner, lo, hi, self.vars[key], DEFAULTS[key]).pack(fill="x")

        tk.Label(wrap, text="drag to set  ·  mouse wheel = ±1 ms  ·  double-click = reset that value",
                 fg=FG_DIM, bg=BG, font=fnt(7)).grid(row=(len(TIMINGS) + 1) // 2, column=0,
                                                     columnspan=2, sticky="w", pady=(4, 0))

    # ── Page: KEYBINDS ────────────────────────────────────────────────────────
    def _page_keybinds(self):
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=(4, 10))
        wrap.columnconfigure(0, weight=1, uniform="k")
        wrap.columnconfigure(1, weight=1, uniform="k")

        def section(col, title, sub, rows):
            c = card(wrap)
            c.grid(row=0, column=col, sticky="nsew",
                   padx=(0, 6) if col == 0 else (6, 0))
            inner = tk.Frame(c, bg=CARD, padx=14, pady=10)
            inner.pack(fill="both", expand=True)
            tk.Label(inner, text=title, fg=FG_DIM, bg=CARD,
                     font=fnt(7, True)).pack(anchor="w")
            tk.Label(inner, text=sub, fg=FG_DIM, bg=CARD, font=fnt(7),
                     wraplength=250, justify="left").pack(anchor="w", pady=(2, 6))
            for name, label, hint in rows:
                r = tk.Frame(inner, bg=CARD)
                r.pack(fill="x", pady=3)
                left = tk.Frame(r, bg=CARD)
                left.pack(side="left")
                tk.Label(left, text=label, fg=FG, bg=CARD,
                         font=fnt(9, True)).pack(anchor="w")
                tk.Label(left, text=hint, fg=FG_DIM, bg=CARD,
                         font=fnt(7)).pack(anchor="w")
                b = tk.Label(r, text=kn(name), bg=CARD2, fg=ACCENT,
                             font=fnt(9, True), width=10, pady=5, cursor="hand2")
                b.pack(side="right")
                b.bind("<Enter>", lambda e, w=b: w.config(bg=HOVER))
                b.bind("<Leave>", lambda e, w=b: w.config(bg=CARD2))
                b.bind("<Button-1>", lambda e, n=name, w=b: self._start_capture(n, w))
                b.bind("<Button-3>", lambda e, n=name: self._set_bind(n, KEY_DEFAULTS[n]))

        section(0, "TRIGGERS", "keys you press to start a macro", TRIGGER_ROWS)
        section(1, "GAME BINDS", "keys the macro presses for you. Set them to match "
                "your Fortnite keybinds", GAME_ROWS)

        # footer under the right column
        side = tk.Frame(wrap, bg=BG)
        side.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        make_button(side, "Reset keybinds", self.reset_keys).pack(side="left")
        tk.Label(side, text="click = rebind  ·  right-click = default  ·  Esc = cancel",
                 fg=FG_DIM, bg=BG, font=fnt(7)).pack(side="left", padx=12)

    # ── Page: INFO ────────────────────────────────────────────────────────────
    def _page_info(self):
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=(4, 10))

        # status
        sc = card(wrap)
        sc.pack(fill="x", pady=(0, 8))
        sin = tk.Frame(sc, bg=CARD, padx=16, pady=10)
        sin.pack(fill="x")
        tk.Label(sin, text="SYSTEM STATUS", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).pack(anchor="w", pady=(0, 4))
        game = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        game.pack(fill="x")
        mode = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        mode.pack(fill="x", pady=(2, 0))
        hook = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        hook.pack(fill="x", pady=(2, 0))
        adm = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        adm.pack(fill="x", pady=(2, 0))
        held = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        held.pack(fill="x", pady=(2, 0))
        sent = tk.Label(sin, text="", bg=CARD, font=fnt(9), anchor="w")
        sent.pack(fill="x", pady=(2, 0))

        def live(text, col, focused):
            if state["force"]:
                game.config(text="Fortnite    ◐  check bypassed (test mode)", fg=YELLOW)
            else:
                game.config(text=f"Fortnite    {'●  detected' if focused else '○  not found'}",
                            fg=GREEN if focused else RED)
            mode.config(text=f"Mode          {text}", fg=col)
            if not IS_WIN:
                hook.config(text="Input hook   n/a (preview mode)", fg=FG_DIM)
                adm.config(text="Admin         n/a (preview mode)", fg=FG_DIM)
            else:
                if HOOK_OK:
                    hook.config(text="Input hook   ●  ok", fg=GREEN)
                else:
                    hook.config(text="Input hook   ○  failed (using fallback)", fg=RED)
                if ADMIN:
                    adm.config(text="Admin         ●  yes", fg=GREEN)
                else:
                    adm.config(text="Admin         ○  no - if the game ignores the macro, "
                                    "restart and accept the UAC prompt", fg=YELLOW)
            held.config(text="Held keys     " + (", ".join(held_names()) or "-"), fg=FG_MID)
            err = stats["err"]
            msg = (f"Build taps    {stats['taps']}    ·    Box moves  {stats['moves']}"
                   f"    ·    SendInput errors  {err}")
            if err:
                msg += f"  (code {stats['last_err']})"
            sent.config(text=msg, fg=RED if err else FG_MID)
        self._live.append(live)

        # keybinds
        kb = card(wrap)
        kb.pack(fill="x", pady=(0, 8))
        kin = tk.Frame(kb, bg=CARD, padx=16, pady=10)
        kin.pack(fill="x")
        tk.Label(kin, text="KEYBINDS", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).pack(anchor="w", pady=(0, 4))
        for kt, action in ((" ".join(kn("build_%d" % i) for i in range(1, 6)),
                            "Build piece (key + click loop)"),
                           ("Hold " + kn("auto_edit"),   "Auto edit loop"),
                           ("Hold " + kn("edit_click"),  "Edit click (LMB held)"),
                           ("Hold " + kn("retake"),      "Wall retake (pickaxe hit, then spam wall)"),
                           ("Tap " + kn("box"),          "Box (4 walls + floor on top, turns 90 between walls)"),
                           (kn("pause"),                 "Pause / resume macros (global)")):
            r = tk.Frame(kin, bg=CARD)
            r.pack(fill="x", pady=1)
            holder = tk.Frame(r, bg=CARD, width=150, height=22)
            holder.pack(side="left")
            holder.pack_propagate(False)
            chip(holder, kt).pack(side="left")
            tk.Label(r, text=action, fg=FG_MID, bg=CARD, font=fnt(8)).pack(side="left")

        # window
        wc = card(wrap)
        wc.pack(fill="x")
        win = tk.Frame(wc, bg=CARD, padx=16, pady=10)
        win.pack(fill="x")
        tk.Label(win, text="WINDOW", fg=FG_DIM, bg=CARD,
                 font=fnt(7, True)).grid(row=0, column=0, sticky="w", pady=(0, 4))
        win.columnconfigure(1, weight=1)
        tk.Label(win, text="Always on top", fg=FG_MID, bg=CARD,
                 font=fnt(9)).grid(row=1, column=0, sticky="w", pady=2)
        Toggle(win, self.topmost_var).grid(row=1, column=2, sticky="e")
        tk.Label(win, text="Opacity", fg=FG_MID, bg=CARD,
                 font=fnt(9)).grid(row=2, column=0, sticky="w", pady=2)
        Slider(win, 55, 100, self.opacity_var, 100).grid(row=2, column=1, sticky="ew", padx=12)
        tk.Label(win, textvariable=self.opacity_str, fg=ACCENT, bg=CARD,
                 font=fnt(9, True), width=5, anchor="e").grid(row=2, column=2, sticky="e")
        tk.Label(win, text="Test mode (any window)", fg=FG_MID, bg=CARD,
                 font=fnt(9)).grid(row=3, column=0, sticky="w", pady=2)
        tk.Label(win, text="only to check if the macro reacts - turn off after",
                 fg=FG_DIM, bg=CARD, font=fnt(7)).grid(row=3, column=1, sticky="w", padx=12)
        Toggle(win, self.force_var).grid(row=3, column=2, sticky="e")


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Windows blocks input between a normal process and an elevated one, so the
    # macro must run elevated whenever the game / launcher does.
    if IS_WIN and not ADMIN and "--no-admin" not in sys.argv:
        if relaunch_as_admin():
            sys.exit(0)
    sys.setswitchinterval(0.001)    # keep the input hooks responsive (GIL hand-off)
    load_config()
    if IS_WIN:
        init_timing()
        threading.Thread(target=input_hook_thread, daemon=True).start()
    threading.Thread(target=logic_thread, daemon=True).start()
    App().mainloop()