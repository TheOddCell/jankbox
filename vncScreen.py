"""Mirrors a VNC server's screen into a Jackbox room via the "Draw"
state's backgroundImageUrl (base64 data URI, no external hosting
needed), with keyboard input relayed back to the VNC server, plus a
chat mode (identical to chatApp.py's broadcast chat/history-buttons
design) reachable from the mirror screen.

Three screens, one bc:room state at a time:
  - mirror (Draw): the VM screen, Ctrl/Shift/Esc/Tab/Enter buttons,
    "Type" and "Chat" buttons
  - typing (EnterSingleText): full-string text entry for the VM,
    auto-returns to the mirror after Send
  - chat (EnterSingleText): chatApp-style broadcast chat with message
    history as buttons (click your own message to delete it), a
    "Back to VM" button at the top of the button list returns to the
    mirror - chat does NOT auto-return after Send, same as chatApp.py

Usage:
    ./launch.py ./engine.py ./vncScreen.py QUIPLASH3

Requires Pillow. Edit VNC_HOST/VNC_PORT/VNC_PASSWORD below to point at a
real VNC server.

History, for whoever's reading this later (2026-08-11 build log):
  1st attempt: mirror + click-to-mouse via Draw state's `lines`/autoSubmit,
    keyboard via toggling to EnterSingleText and back. Reported broken.
  2nd attempt: dropped the mirror and toggle entirely, EnterSingleText as
    the only screen. Reported still frozen after Send.
  Root cause A: `vncdotool key Tab`/`key Return` genuinely hang against a
    real QEMU -vnc server. Worked around by threading vnc_command() so
    the hang couldn't block the ecast message loop.
  Root cause B: EnterSingleText disables its own input fieldset after
    every submit unless "repeating" is set. Fixed by re-pushing state
    after every submit, same as chatApp.py does.
  Root cause C: going directly from one full bc:room state to a very
    different one in a single object/set felt "frozen" client-side.
    Fixed by flashing through Lobby in between, same trick chatApp.py
    uses.
  With A/B/C fixed, the mirror+buttons combo rendered and dispatched
  correctly, but named special keys (confirmed: Return) were STILL
  hanging - just silently now, since threading hid it instead of fixing
  it. Tested a from-scratch raw RFB client (see rfbClient.py) directly
  against the same server: KeyEvent messages for the same keys complete
  instantly, no hang, no vncdotool anywhere in the path. So the hang was
  entirely a vncdotool bug, not anything about the VM or protocol.
  vncScreen.py now uses rfbClient.py exclusively - vncdotool is gone.

You can also type on this process's own stdin while it's running:
    type <text>   - types text into the VNC session directly
    key <keyname> - sends a single key (e.g. "key Return", "key a")
    stop          - stops the capture loop
"""

import base64
import io
import os
import threading
import time

import rfbClient


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

VNC_HOST = os.environ.get("VNC_HOST", "127.0.0.1")
VNC_PORT = int(os.environ.get("VNC_PORT", "5901"))
VNC_PASSWORD = os.environ.get("VNC_PASSWORD") or None
FPS_CAP = 10
MAX_WIDTH = 960  # downscale to keep the base64 payload reasonable - bumped up from 480 for legibility
JPEG_QUALITY = 85
MAX_CHAT_HISTORY = 20

SPECIAL_KEYS = {
    "esc": "Escape",
    "tab": "Tab",
    "enter": "Return",
    "backspace": "BackSpace",
    "key_up": "Up",
    "key_down": "Down",
    "key_left": "Left",
    "key_right": "Right",
}
MODIFIER_KEYS = {"ctrl": "Control_L", "shift": "Shift_L"}

MOUSE_STEP = 20  # pixels moved per mouse-direction button press, in real VNC coordinates
MOUSE_MOVES = {
    "mouse_up": (0, -MOUSE_STEP),
    "mouse_down": (0, MOUSE_STEP),
    "mouse_left": (-MOUSE_STEP, 0),
    "mouse_right": (MOUSE_STEP, 0),
}
MOUSE_CLICKS = {"click_left": 1, "click_right": 4}  # RFB button masks: left=1, right=4


def _run(fn, *args):
    try:
        fn(VNC_HOST, VNC_PORT, *args, VNC_PASSWORD)
        print(f"[vncScreen] {fn.__name__}{args} ok")
    except Exception as e:
        print(f"[vncScreen] {fn.__name__}{args} failed: {e}")


def vnc_key(name):
    threading.Thread(target=_run, args=(rfbClient.key, name), daemon=True).start()


def vnc_keydown(name):
    threading.Thread(target=_run, args=(rfbClient.keydown, name), daemon=True).start()


def vnc_keyup(name):
    threading.Thread(target=_run, args=(rfbClient.keyup, name), daemon=True).start()


def vnc_type(text):
    threading.Thread(target=_run, args=(rfbClient.type_text, text), daemon=True).start()




class vncScreen():
    def __init__(self):
        self._stop = threading.Event()
        self._started = False
        self._mode = "draw"  # "draw" | "typing" | "chat"
        self._held_modifiers = set()
        self._real_size = (MAX_WIDTH, MAX_WIDTH)  # updated each captured frame
        self._sent_size = (MAX_WIDTH, MAX_WIDTH)
        self._mouse_pos = (MAX_WIDTH // 2, MAX_WIDTH // 2)  # tracked in real VNC coordinates
        self._mouse_lock = threading.Lock()
        self._mouse_sock = None  # persistent connection, see rfbClient.open_pointer_session
        self._chat_history = []  # [{"id", "sender_id", "text"}, ...], most recent first
        self._next_chat_id = 1

    # --- room state: mirror (Draw) ---

    def _key_actions(self):
        def label(name, key):
            held = " (held)" if key in self._held_modifiers else ""
            return {"text": f"{name}{held}", "action": "choose", "key": key}
        return [
            label("Ctrl", "ctrl"),
            label("Shift", "shift"),
            {"text": "Esc", "action": "choose", "key": "esc"},
            {"text": "Tab", "action": "choose", "key": "tab"},
            {"text": "Enter", "action": "choose", "key": "enter"},
            {"text": "Backspace", "action": "choose", "key": "backspace"},
            {"text": "Ctrl+C", "action": "choose", "key": "ctrlc"},
            {"text": "Key ↑", "action": "choose", "key": "key_up"},
            {"text": "Key ↓", "action": "choose", "key": "key_down"},
            {"text": "Key ←", "action": "choose", "key": "key_left"},
            {"text": "Key →", "action": "choose", "key": "key_right"},
            {"text": "Mouse ↑", "action": "choose", "key": "mouse_up"},
            {"text": "Mouse ↓", "action": "choose", "key": "mouse_down"},
            {"text": "Mouse ←", "action": "choose", "key": "mouse_left"},
            {"text": "Mouse →", "action": "choose", "key": "mouse_right"},
            {"text": "Click L", "action": "choose", "key": "click_left"},
            {"text": "Click R", "action": "choose", "key": "click_right"},
        ]

    def _draw_state(self, background_data_uri=None, width=None, height=None):
        return {
            "state": "Draw",
            "platformId": "WIN",
            "size": {"width": width or MAX_WIDTH, "height": height or MAX_WIDTH},
            "backgroundImageUrl": background_data_uri or False,
            "lines": [],  # explicitly cleared every frame
            "autoSubmit": False,  # mouse is now buttons-only, canvas taps aren't wired to anything
            "hideSubmit": True,
            "live": False,
            "actions": self._key_actions() + [
                {"text": "Type", "action": "choose", "key": "type"},
                {"text": "Chat", "action": "choose", "key": "chat"},
            ],
            "prompt": {"html": "VNC mirror - buttons below for keys and mouse, Type for full text, Chat to talk"},
        }

    # --- room state: VM text entry (EnterSingleText) ---

    def _typing_state(self):
        return {
            "state": "EnterSingleText",
            "platformId": "WIN",
            "textKey": "typed",
            "placeholder": "type here, hit Send",
            "actions": [{"text": "Send", "action": "submit"}] + self._key_actions() +
                       [{"text": "Back to mirror", "action": "choose", "key": "mirror"}],
            "prompt": {"text": "VNC keyboard - Send types the text, or use the keys below"},
        }

    # --- room state: chat (EnterSingleText, chatApp.py-identical design) ---

    def _chat_actions(self):
        actions = [
            {"text": "Send", "action": "submit"},
            {"text": "Back to VM", "action": "choose", "key": "mirror"},
        ]
        actions += [{"text": e["text"], "action": "choose", "key": e["id"]}
                    for e in self._chat_history]
        return actions

    def _chat_placeholder(self):
        return self._chat_history[0]["text"] if self._chat_history else "No messages yet"

    def _chat_state(self):
        return {
            "state": "EnterSingleText",
            "platformId": "WIN",
            "textKey": "chatmsg",
            "placeholder": self._chat_placeholder(),
            "actions": self._chat_actions(),
            "prompt": {"text": "Jankbox Chat - click your own message to delete it"},
        }

    def _sync_chat_state(self, host, wsapp):
        host.set_room_state(wsapp, "EnterSingleText", mode="set", extra=self._chat_state())

    # --- dispatch ---

    def on_message(self, host, wsapp, opcode, result):
        if opcode == "client/connected" and not self._started:
            self._started = True
            host.set_room_state(wsapp, "Draw", mode="create", extra=self._draw_state())
            host.send(wsapp, "text/create", {"key": "typed", "val": "", "acl": ["rw *"]})
            host.send(wsapp, "text/create", {"key": "chatmsg", "val": "", "acl": ["rw *"]})
            threading.Thread(target=self._loop, args=(host, wsapp), daemon=True).start()
        elif opcode == "client/send":
            self._on_action(host, wsapp, result)
        elif opcode == "text" and result.get("key") == "typed":
            self._on_typed(host, wsapp, result)
        elif opcode == "text" and result.get("key") == "chatmsg":
            self._on_chat_message(host, wsapp, result)

    def _on_action(self, host, wsapp, result):
        body = result.get("body", {})
        # Draw state's own buttons send {"action":"choose","index":<key>};
        # EnterSingleText's buttons send {"action":<key>} directly. Same
        # render component, different click-handler wiring per view.
        key = body.get("index") if body.get("action") == "choose" else body.get("action")

        if key == "type":
            self._mode = "typing"
            self._flash_to_state(host, wsapp, "EnterSingleText", self._typing_state())
            return
        if key == "chat":
            self._mode = "chat"
            self._flash_to_state(host, wsapp, "EnterSingleText", self._chat_state())
            return
        if key == "mirror":
            self._mode = "draw"
            self._flash_to_state(host, wsapp, "Draw", self._draw_state())
            return

        if self._mode == "chat" and isinstance(key, int):
            self._delete_chat_message(host, wsapp, key, result.get("from"))
            return

        if key == "ctrlc":
            self._send_ctrl_c()
            return

        if key in MOUSE_MOVES:
            dx, dy = MOUSE_MOVES[key]
            self._move_mouse(dx, dy)
            return

        if key in MOUSE_CLICKS:
            self._click_mouse(MOUSE_CLICKS[key])
            return

        if key in MODIFIER_KEYS:
            keysym_name = MODIFIER_KEYS[key]
            if key in self._held_modifiers:
                self._held_modifiers.discard(key)
                vnc_keyup(keysym_name)
                print(f"[vncScreen] released {key}")
            else:
                self._held_modifiers.add(key)
                vnc_keydown(keysym_name)
                print(f"[vncScreen] holding {key}")
            self._refresh_current_state(host, wsapp)
        elif key in SPECIAL_KEYS:
            vnc_key(SPECIAL_KEYS[key])
            print(f"[vncScreen] key {SPECIAL_KEYS[key]}")

    def _ensure_mouse_session(self):
        """Must be called with self._mouse_lock held."""
        if self._mouse_sock is None:
            try:
                self._mouse_sock = rfbClient.open_pointer_session(VNC_HOST, VNC_PORT, VNC_PASSWORD)
                print("[vncScreen] opened persistent mouse session")
            except Exception as e:
                print(f"[vncScreen] failed to open mouse session: {e}")
                self._mouse_sock = None
        return self._mouse_sock

    def _move_mouse(self, dx, dy):
        real_w, real_h = self._real_size
        x, y = self._mouse_pos
        x = max(0, min(real_w - 1, x + dx))
        y = max(0, min(real_h - 1, y + dy))
        self._mouse_pos = (x, y)

        def run():
            with self._mouse_lock:
                sock = self._ensure_mouse_session()
                if not sock:
                    return
                try:
                    rfbClient.send_pointer_event(sock, x, y, 0)
                    print(f"[vncScreen] mouse move to ({x},{y})")
                except Exception as e:
                    print(f"[vncScreen] mouse move failed: {e}")
                    self._mouse_sock = None
        threading.Thread(target=run, daemon=True).start()

    def _click_mouse(self, button_mask):
        x, y = self._mouse_pos

        def run():
            with self._mouse_lock:
                sock = self._ensure_mouse_session()
                if not sock:
                    return
                try:
                    rfbClient.send_pointer_event(sock, x, y, button_mask)
                    rfbClient.send_pointer_event(sock, x, y, 0)
                    print(f"[vncScreen] click button_mask={button_mask} at ({x},{y})")
                except Exception as e:
                    print(f"[vncScreen] click failed: {e}")
                    self._mouse_sock = None
        threading.Thread(target=run, daemon=True).start()

    def _send_ctrl_c(self):
        def run():
            try:
                rfbClient.keydown(VNC_HOST, VNC_PORT, "Control_L", VNC_PASSWORD)
                rfbClient.key(VNC_HOST, VNC_PORT, "c", VNC_PASSWORD)
                rfbClient.keyup(VNC_HOST, VNC_PORT, "Control_L", VNC_PASSWORD)
                print("[vncScreen] sent ctrl+c")
            except Exception as e:
                print(f"[vncScreen] ctrl+c failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def _on_typed(self, host, wsapp, result):
        val = result.get("val", "")
        if val:
            print(f"[vncScreen] typing: {val!r}")
            vnc_type(val)
        host.send(wsapp, "text/set", {"key": "typed", "val": "", "acl": ["rw *"]})
        # Auto-return to the mirror after Send, through the same Lobby
        # flash as the explicit "Back to mirror" button, so you see the
        # result immediately instead of needing an extra click.
        self._mode = "draw"
        self._flash_to_state(host, wsapp, "Draw", self._draw_state())

    # --- chat ---

    def _on_chat_message(self, host, wsapp, result):
        val = result.get("val", "")
        if not val:
            return
        sender_id = result.get("from")
        sender_name = host.player_names.get(sender_id, "???")
        labeled_val = f"{sender_name}: {val}"
        print(f"[vncScreen] chat: {labeled_val}")

        self._chat_history.insert(0, {"id": self._next_chat_id, "sender_id": sender_id, "text": labeled_val})
        self._next_chat_id += 1
        del self._chat_history[MAX_CHAT_HISTORY:]

        def back_to_chatbox():
            self._sync_chat_state(host, wsapp)
            host.send(wsapp, "text/set", {"key": "chatmsg", "val": "", "acl": ["rw *"]})

        # flash Lobby for a moment before returning with the updated
        # placeholder, as a visible "submitted" transition (chatApp.py's trick)
        host.send(wsapp, "object/set", {
            "key": "bc:room",
            "val": {"state": "Lobby", "platformId": "WIN"},
            "acl": ["rw *"],
        })
        threading.Timer(0.05, back_to_chatbox).start()

    def _delete_chat_message(self, host, wsapp, entry_id, requester_id):
        entry = next((e for e in self._chat_history if e["id"] == entry_id), None)
        if not entry or entry["sender_id"] != requester_id:
            return  # only the original sender can delete their own message
        self._chat_history.remove(entry)
        print(f"[vncScreen] chat deleted: {entry['text']}")
        self._sync_chat_state(host, wsapp)

    # --- shared state-transition helpers ---

    def _flash_to_state(self, host, wsapp, state, extra):
        """Briefly flashes through Lobby before landing on `state` - going
        directly from one full bc:room state to a very different one in a
        single object/set felt "frozen" client-side; Lobby in between
        gives the client a clean reset point (same trick chatApp.py uses)."""
        host.send(wsapp, "object/set", {
            "key": "bc:room",
            "val": {"state": "Lobby", "platformId": "WIN"},
            "acl": ["rw *"],
        })
        threading.Timer(0.05, lambda: host.set_room_state(wsapp, state, mode="set", extra=extra)).start()

    def _refresh_current_state(self, host, wsapp):
        if self._mode == "typing":
            host.set_room_state(wsapp, "EnterSingleText", mode="set", extra=self._typing_state())
        elif self._mode == "chat":
            self._sync_chat_state(host, wsapp)
        else:
            host.set_room_state(wsapp, "Draw", mode="set", extra=self._draw_state())

    # --- capture loop (mirror only, paused while off the mirror screen) ---

    def _capture_frame(self):
        image = rfbClient.capture(VNC_HOST, VNC_PORT, VNC_PASSWORD)
        self._real_size = image.size
        if image.width > MAX_WIDTH:
            scale = MAX_WIDTH / image.width
            image = image.resize((MAX_WIDTH, int(image.height * scale)))
        self._sent_size = image.size
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return image.width, image.height, base64.b64encode(buf.getvalue()).decode()

    def _loop(self, host, wsapp):
        interval = 1 / FPS_CAP
        frame_count = 0
        while not self._stop.is_set():
            start = time.time()
            if self._mode == "draw":
                try:
                    width, height, b64 = self._capture_frame()
                    host.send(wsapp, "object/set", {
                        "key": "bc:room",
                        "val": self._draw_state(f"data:image/jpeg;base64,{b64}", width, height),
                        "acl": ["rw *"],
                    })
                    frame_count += 1
                    if frame_count % FPS_CAP == 0:
                        print(f"[vncScreen] sent {frame_count} frames, last payload ~{len(b64) // 1024}KB")
                except Exception as e:
                    print(f"[vncScreen] frame failed: {e}")
            elapsed = time.time() - start
            time.sleep(max(0, interval - elapsed))

    # --- terminal commands ---

    def on_command(self, host, wsapp, line):
        line = line.strip()
        if line == "stop":
            self._stop.set()
            with self._mouse_lock:
                if self._mouse_sock:
                    self._mouse_sock.close()
                    self._mouse_sock = None
            print("[vncScreen] capture loop stopped")
        elif line.startswith("type "):
            vnc_type(line[len("type "):])
        elif line.startswith("key "):
            vnc_key(line[len("key "):])
