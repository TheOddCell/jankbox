"""Quick test app: sets the room to the "Draw" state with an arbitrary
external image URL as the sketchpad background, to verify backgroundImageUrl
actually renders on a real client. "Draw" is confirmed present in
Quiplash3/Drawful2's own layout switch too, not just ecast-test-client, so
this works under any of the usual appTags.

Usage:
    ./launch.py ./engine.py ./imageBgApp.py QUIPLASH3

Edit IMAGE_URL below, or type a new one on stdin while it's running
(it's wired up via on_command) to swap the background live.
"""

IMAGE_URL = "https://obsidianos.xyz/logo.png"


class imageBgApp():
    def __init__(self, image_url=IMAGE_URL, width=400, height=300):
        self.image_url = image_url
        self.width = width
        self.height = height

    def _room_state(self):
        return {
            "size": {"width": self.width, "height": self.height},
            "backgroundImageUrl": self.image_url,
            "prompt": {"html": "backgroundImageUrl test"},
        }

    def on_message(self, host, wsapp, opcode, result):
        if opcode == "client/connected":
            print(f"[imageBgApp] setting Draw state with backgroundImageUrl={self.image_url!r}")
            host.set_room_state(wsapp, "Draw", mode="create", extra=self._room_state())

    def on_command(self, host, wsapp, line):
        """Type a new image URL on stdin while running to swap it live."""
        url = line.strip()
        if not url:
            return
        self.image_url = url
        print(f"[imageBgApp] updating backgroundImageUrl={url!r}")
        host.set_room_state(wsapp, "Draw", mode="set", extra=self._room_state())
