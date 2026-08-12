"""Minimal pure-Python RFB (VNC) client - no vncdotool, no external VNC
dependency at all beyond Pillow for image decoding. Built after finding
that vncdotool's own `key`/`type` commands hang indefinitely against a
real QEMU -vnc server for named special keys (Tab/Return confirmed) while
a raw, from-scratch RFB implementation sending the same KeyEvent messages
completes instantly (tested directly 2026-08-11).

Only implements what vncScreen.py actually needs: connect, send a key
(down/up separately, for held modifiers), type a string, and capture a
frame as a PIL Image. Requests "Raw" encoding (type 0) for capture -
simplest to decode correctly, not the most bandwidth-efficient, fine at
this scale (small downscaled frames, low fps).

Connects fresh for every call rather than holding a persistent
connection - simpler, avoids connection-state bugs, and keyboard/mouse
state lives in the guest OS, not the VNC connection, so holding Ctrl
down and then closing the connection doesn't release it: the next
connection's keypress still arrives with Ctrl held, as far as the guest
is concerned.
"""

import socket
import struct

from PIL import Image

KEYSYMS = {
    "Tab": 0xFF09,
    "Return": 0xFF0D,
    "Escape": 0xFF1B,
    "BackSpace": 0xFF08,
    "Delete": 0xFFFF,
    "Control_L": 0xFFE3,
    "Shift_L": 0xFFE1,
    "Alt_L": 0xFFE9,
    "Up": 0xFF52,
    "Down": 0xFF54,
    "Left": 0xFF51,
    "Right": 0xFF53,
}


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("VNC server closed the connection")
        buf += chunk
    return buf


def connect(host, port, password=None, timeout=5):
    """Handshakes with the server and returns (sock, width, height,
    pixel_format_dict). Raises on failure. Only supports "None" (type 1)
    security - if the server requires VNC auth (a real password), this
    will raise; QEMU's -vnc with no password uses None."""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)

    server_version = _recv_exact(sock, 12)
    if not server_version.startswith(b"RFB "):
        raise ConnectionError(f"not an RFB server: {server_version!r}")
    sock.sendall(b"RFB 003.008\n")

    n_types = _recv_exact(sock, 1)[0]
    if n_types == 0:
        reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
        reason = _recv_exact(sock, reason_len)
        raise ConnectionError(f"server rejected connection: {reason!r}")
    types = _recv_exact(sock, n_types)
    if 1 not in types:
        raise ConnectionError(f"no supported security type (got {list(types)}, only 'None' (1) supported)")
    sock.sendall(bytes([1]))

    result = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if result != 0:
        reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
        reason = _recv_exact(sock, reason_len)
        raise ConnectionError(f"security handshake failed: {reason!r}")

    sock.sendall(bytes([1]))  # ClientInit: shared=1

    header = _recv_exact(sock, 24)
    width, height = struct.unpack(">HH", header[0:4])
    (bpp, depth, big_endian, true_color, red_max, green_max, blue_max,
     red_shift, green_shift, blue_shift) = struct.unpack(">BBBBHHHBBB3x", header[4:20])
    name_len = struct.unpack(">I", header[20:24])[0]
    _recv_exact(sock, name_len)  # server name, unused

    pixel_format = {
        "bpp": bpp, "depth": depth, "big_endian": big_endian, "true_color": true_color,
        "red_max": red_max, "green_max": green_max, "blue_max": blue_max,
        "red_shift": red_shift, "green_shift": green_shift, "blue_shift": blue_shift,
    }
    return sock, width, height, pixel_format


def send_key_event(sock, keysym, down):
    sock.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))


def send_pointer_event(sock, x, y, button_mask):
    """button_mask bit 0 = left button, bit 1 = middle, bit 2 = right."""
    sock.sendall(struct.pack(">BBHH", 5, button_mask, x, y))


def click(host, port, x, y, button_mask=1, password=None):
    """Presses and releases `button_mask` (default: left button) at
    (x, y) in the server's real framebuffer coordinates."""
    sock, *_ = connect(host, port, password)
    try:
        send_pointer_event(sock, x, y, button_mask)
        send_pointer_event(sock, x, y, 0)  # release
    finally:
        sock.close()


def move(host, port, x, y, password=None):
    """Moves the pointer to (x, y) without pressing any button."""
    sock, *_ = connect(host, port, password)
    try:
        send_pointer_event(sock, x, y, 0)
    finally:
        sock.close()


def open_pointer_session(host, port, password=None):
    """Opens a connection meant to be kept open and reused for every
    subsequent pointer event, instead of connecting fresh per call like
    click()/move() do.

    QEMU's default mouse (no `-device usb-tablet`) is a *relative* PS/2
    device: each PointerEvent's (x, y) isn't warped to absolute - QEMU
    computes the delta as (this x/y - the last x/y it saw *on this same
    VNC connection*) and moves the guest cursor by that delta. Reconnect
    between moves (as click()/move() do) and that "last seen" reference
    resets, so every move looks like a fresh jump from some arbitrary
    origin instead of a continuation - the guest cursor barely moves or
    jumps unpredictably. Keeping one connection open across a whole
    sequence of moves makes consecutive PointerEvents behave like an
    actual relative mouse. Caller owns the returned socket and must
    close it when done."""
    sock, *_ = connect(host, port, password)
    return sock


def key(host, port, name, password=None):
    """Presses and releases a named special key (see KEYSYMS) or a
    single printable character."""
    keysym = KEYSYMS.get(name) or (ord(name) if len(name) == 1 else None)
    if keysym is None:
        raise ValueError(f"unknown key name: {name!r}")
    sock, *_ = connect(host, port, password)
    try:
        send_key_event(sock, keysym, True)
        send_key_event(sock, keysym, False)
    finally:
        sock.close()


def keydown(host, port, name, password=None):
    keysym = KEYSYMS[name]
    sock, *_ = connect(host, port, password)
    try:
        send_key_event(sock, keysym, True)
    finally:
        sock.close()


def keyup(host, port, name, password=None):
    keysym = KEYSYMS[name]
    sock, *_ = connect(host, port, password)
    try:
        send_key_event(sock, keysym, False)
    finally:
        sock.close()


SHIFTED_CHARS = set('~!@#$%^&*()_+{}|:"<>?')


def type_text(host, port, text, password=None):
    """Types a string of printable characters (plus \\n as Return) by
    sending each character's keysym - for printable ASCII, the X11
    keysym value is just the character's own ordinal.

    Sending the keysym for a shifted symbol (e.g. "!") on its own isn't
    enough: QEMU's VNC keyboard emulation maps the keysym to a physical
    key and presses it WITHOUT synthesizing Shift, so "!" came out as
    "1". Fixed by explicitly holding Shift_L around any character that
    needs it (uppercase letters, or symbols in SHIFTED_CHARS)."""
    shift_keysym = KEYSYMS["Shift_L"]
    sock, *_ = connect(host, port, password)
    try:
        for ch in text:
            keysym = KEYSYMS["Return"] if ch == "\n" else ord(ch)
            needs_shift = ch.isupper() or ch in SHIFTED_CHARS
            if needs_shift:
                send_key_event(sock, shift_keysym, True)
            send_key_event(sock, keysym, True)
            send_key_event(sock, keysym, False)
            if needs_shift:
                send_key_event(sock, shift_keysym, False)
    finally:
        sock.close()


def _decode_pixel_format(raw, pixel_format, count):
    """Decodes `count` pixels of raw bytes per the server's native pixel
    format into a flat list of (r, g, b) tuples."""
    bpp_bytes = pixel_format["bpp"] // 8
    fmt = {1: "B", 2: "H", 4: "I"}[bpp_bytes]
    endian = ">" if pixel_format["big_endian"] else "<"
    values = struct.unpack(f"{endian}{count}{fmt}", raw)
    rs, gs, bs = pixel_format["red_shift"], pixel_format["green_shift"], pixel_format["blue_shift"]
    rmax, gmax, bmax = pixel_format["red_max"], pixel_format["green_max"], pixel_format["blue_max"]
    pixels = []
    for v in values:
        r = (v >> rs) & rmax
        g = (v >> gs) & gmax
        b = (v >> bs) & bmax
        # scale each channel up to 0-255 regardless of the server's bit depth
        r = r * 255 // rmax if rmax else 0
        g = g * 255 // gmax if gmax else 0
        b = b * 255 // bmax if bmax else 0
        pixels.append((r, g, b))
    return pixels


def capture(host, port, password=None):
    """Returns a PIL Image of the current framebuffer. Requests Raw
    encoding (simplest to decode correctly, not the most efficient)."""
    sock, width, height, pixel_format = connect(host, port, password)
    try:
        bpp_bytes = pixel_format["bpp"] // 8
        # SetEncodings: only Raw (0)
        sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))
        # FramebufferUpdateRequest: incremental=0 (full frame)
        sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))

        msg_type = _recv_exact(sock, 1)[0]
        if msg_type != 0:
            raise ConnectionError(f"expected FramebufferUpdate (0), got {msg_type}")
        _recv_exact(sock, 1)  # padding
        n_rects = struct.unpack(">H", _recv_exact(sock, 2))[0]

        image = Image.new("RGB", (width, height))
        for _ in range(n_rects):
            rx, ry, rw, rh, encoding = struct.unpack(">HHHHi", _recv_exact(sock, 12))
            if encoding != 0:
                raise ConnectionError(f"unexpected encoding {encoding}, only Raw (0) was requested")
            raw = _recv_exact(sock, rw * rh * bpp_bytes)
            pixels = _decode_pixel_format(raw, pixel_format, rw * rh)
            rect_image = Image.new("RGB", (rw, rh))
            rect_image.putdata(pixels)
            image.paste(rect_image, (rx, ry))
        return image
    finally:
        sock.close()
