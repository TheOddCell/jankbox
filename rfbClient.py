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

# --- DES, for RFB "VNC Authentication" (security type 2) ---
#
# The RFB spec's password auth is a 16-byte DES challenge-response using the
# password (null-padded/truncated to 8 bytes) as the key - but with a VNC-
# specific quirk inherited from the original AT&T implementation: each key
# byte's bits are reversed before use. No crypto library is installed in
# this environment (no pycryptodome/pyDes), so this is a from-scratch pure-
# Python DES - only ever used for this single 8-byte-block ECB encryption,
# not meant as a general-purpose implementation.

_DES_IP = [
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
]
_DES_FP = [
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25,
]
_DES_E = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1,
]
_DES_P = [
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25,
]
_DES_PC1 = [
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4,
]
_DES_PC2 = [
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32,
]
_DES_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_DES_SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _bits_from_bytes(data):
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _bytes_from_bits(bits):
    return bytes(
        sum(bits[i + j] << (7 - j) for j in range(8))
        for i in range(0, len(bits), 8)
    )


def _permute(bits, table):
    return [bits[i - 1] for i in table]


def _des_key_schedule(key_bytes):
    bits = _permute(_bits_from_bytes(key_bytes), _DES_PC1)
    c, d = bits[:28], bits[28:]
    subkeys = []
    for shift in _DES_SHIFTS:
        c = c[shift:] + c[:shift]
        d = d[shift:] + d[:shift]
        subkeys.append(_permute(c + d, _DES_PC2))
    return subkeys


def _des_encrypt_block(block, subkeys):
    bits = _permute(_bits_from_bytes(block), _DES_IP)
    l, r = bits[:32], bits[32:]
    for subkey in subkeys:
        expanded = _permute(r, _DES_E)
        xored = [b ^ k for b, k in zip(expanded, subkey)]
        sbox_out = []
        for i in range(8):
            chunk = xored[i * 6:i * 6 + 6]
            row = (chunk[0] << 1) | chunk[5]
            col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
            val = _DES_SBOX[i][row * 16 + col]
            sbox_out.extend((val >> j) & 1 for j in (3, 2, 1, 0))
        f = _permute(sbox_out, _DES_P)
        l, r = r, [a ^ b for a, b in zip(l, f)]
    return _bytes_from_bits(_permute(r + l, _DES_FP))


def _vnc_des_key(password):
    key = (password.encode("latin-1") + b"\x00" * 8)[:8]
    # VNC's own quirk: reverse the bits within each key byte before use.
    return bytes(int(f"{b:08b}"[::-1], 2) for b in key)

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
    pixel_format_dict). Raises on failure. Supports "None" (type 1) and
    "VNC Authentication" (type 2, DES password challenge-response)."""
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
    if 2 in types and password is not None:
        sock.sendall(bytes([2]))
        challenge = _recv_exact(sock, 16)
        subkeys = _des_key_schedule(_vnc_des_key(password))
        response = _des_encrypt_block(challenge[:8], subkeys) + _des_encrypt_block(challenge[8:], subkeys)
        sock.sendall(response)
    elif 1 in types:
        sock.sendall(bytes([1]))
    else:
        raise ConnectionError(
            f"no usable security type (got {list(types)}; only 'None' (1) and "
            f"'VNC Authentication' (2, needs a password) are supported)"
        )

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


def send_key_by_name(sock, name, down):
    """Presses/releases a named special key (see KEYSYMS) or a single
    printable character on an already-connected socket."""
    keysym = KEYSYMS.get(name) or (ord(name) if len(name) == 1 else None)
    if keysym is None:
        raise ValueError(f"unknown key name: {name!r}")
    send_key_event(sock, keysym, down)


def send_text(sock, text):
    """Types a string on an already-connected socket - see type_text()
    for why shifted characters need explicit Shift_L handling."""
    shift_keysym = KEYSYMS["Shift_L"]
    for ch in text:
        keysym = KEYSYMS["Return"] if ch == "\n" else ord(ch)
        needs_shift = ch.isupper() or ch in SHIFTED_CHARS
        if needs_shift:
            send_key_event(sock, shift_keysym, True)
        send_key_event(sock, keysym, True)
        send_key_event(sock, keysym, False)
        if needs_shift:
            send_key_event(sock, shift_keysym, False)


def send_set_encodings(sock):
    """SetEncodings: only Raw (0) - simplest to decode correctly."""
    sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))


def request_frame(sock, width, height, pixel_format):
    """Requests and decodes one full FramebufferUpdate on an already-
    connected socket that has already had send_set_encodings() called on
    it at least once. Returns a PIL Image."""
    bpp_bytes = pixel_format["bpp"] // 8
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
    """Returns a PIL Image of the current framebuffer. Connects fresh and
    closes after - for a long-lived mirror loop, open one connection with
    connect() + send_set_encodings() once and call request_frame()
    repeatedly on it instead."""
    sock, width, height, pixel_format = connect(host, port, password)
    try:
        send_set_encodings(sock)
        return request_frame(sock, width, height, pixel_format)
    finally:
        sock.close()
