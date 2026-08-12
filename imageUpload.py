"""Upload a real image file into a Jackbox ecast room as an `image`
entity - the actual upload pipeline (used for real player photo/UGC
submissions in some games), as an alternative to the doodle/stroke
vector path in doodleImage.py.

Confirmed from the ecast client SDK (traced through wsClient.createImage()
/submitImage() and the ecastPlugin's uploadFile() helper in the real
client bundle):
    image/create params: {key, prefix, mimeType}
      -> server responds with an entity carrying `uploadDetails`, which
         includes at least {uploadUrl, mimeType}
    Actual upload: plain HTTP PUT to uploadDetails.uploadUrl, headers
        {"if-none-match": "*", "Content-Type": mimeType}, body = raw
        image bytes - NOT multipart/form-data, just a raw PUT of the
        file bytes. Traced directly from the client's own upload code:
            const {uploadUrl, mimeType} = entity.uploadDetails
            fetch(uploadUrl, {method: "PUT",
                headers: {"if-none-match": "*", "Content-Type": mimeType},
                body: fileBytes})
    image/submit params: {key, success: bool}   - marks the upload
        done/failed after the PUT completes; the client always calls
        this, with success reflecting whether the PUT itself succeeded
    image/moderate params: {key}                - not needed if the
        room's moderationEnabled is off (confirmed off by default)

Unconfirmed / needs a live test:
    - the exact opcode/shape of the server's reply to `image/create`
      (this module assumes it arrives as an "image" opcode carrying the
      same entity fields the client reads, matched by `key` - not
      verified against real traffic yet)
    - whether a host-role connection is actually authorized to call
      image/create/submit at all (vs only a player role) - nothing in
      the client code restricts it, but that's server-side enforcement
      that can't be seen from static analysis
"""

import mimetypes
import threading
import urllib.request


def _guess_mime(path):
    mime, _ = mimetypes.guess_type(path)
    return mime or "image/jpeg"


def upload_image(host, wsapp, key, image_path, prefix="jankbox", timeout=10):
    """Blocking helper: creates the image entity, waits for the server's
    uploadDetails, PUTs the file, then submits. Must be called from a
    thread OTHER than the websocket's own message-handling thread - it
    blocks waiting for a reply that arrives via that same thread's
    on_message callback, e.g. call this from engine.py's stdin command
    thread (on_command), not directly from on_message. Returns True/False.
    """
    mime_type = _guess_mime(image_path)
    got_details = threading.Event()
    details = {}

    # Temporarily wraps the app's on_message to watch for the reply,
    # since engine.py doesn't have a built-in request/response mechanism.
    original = host.app.on_message

    def wrapped(*args):
        _host, _wsapp, opcode, result = args
        if opcode == "image" and result.get("key") == key and result.get("uploadDetails"):
            details["uploadDetails"] = result["uploadDetails"]
            got_details.set()
        return original(*args)

    host.app.on_message = wrapped
    try:
        host.send(wsapp, "image/create", {"key": key, "prefix": prefix, "mimeType": mime_type})
        if not got_details.wait(timeout):
            print(f"[imageUpload] timed out waiting for uploadDetails for {key!r}")
            return False
    finally:
        host.app.on_message = original

    upload_url = details["uploadDetails"]["uploadUrl"]
    with open(image_path, "rb") as f:
        body = f.read()

    req = urllib.request.Request(
        upload_url, data=body, method="PUT",
        headers={"if-none-match": "*", "Content-Type": mime_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= resp.status < 300
    except Exception as e:
        print(f"[imageUpload] upload failed: {e}")
        ok = False

    host.send(wsapp, "image/submit", {"key": key, "success": ok})
    if ok:
        print(f"[imageUpload] uploaded {image_path} as {key!r}")
    return ok
