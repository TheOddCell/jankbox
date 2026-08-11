import threading

MAX_HISTORY = 20


class chatApp():
    """Skips Lobby/MakeSingleChoice entirely and turns the room into a
    broadcast textbox: whatever a player submits gets shown to everyone,
    labeled with their name, then the box resets for the next message.
    Chat history is shown as a stack of buttons below Send (most recent
    on top) in addition to the most recent message in the placeholder.
    Clicking your own message deletes it; clicking someone else's does
    nothing."""

    def __init__(self):
        self.history = []  # [{"id", "sender_id", "text"}, ...], most recent first
        self._next_id = 1

    def on_message(self, host, wsapp, opcode, result):
        key = result.get("key")

        if opcode == "client/connected":
            self.start_sequence(host, wsapp)
        elif opcode == "text" and key == "answer":
            self.on_answer(host, wsapp, result)
        elif opcode == "client/send":
            self.on_action(host, wsapp, result)

    def _actions(self):
        # "Send" is the real submit button; each history entry is a
        # "choose" button keyed by its id, so clicking it reports back
        # {"action": <id>} and we can tell which message it was and who
        # sent it.
        actions = [{"text": "Send", "action": "submit"}]
        actions += [{"text": e["text"], "action": "choose", "key": e["id"]}
                    for e in self.history]
        return actions

    def _placeholder(self):
        return self.history[0]["text"] if self.history else "No messages yet"

    def _room_state(self):
        return {
            "state": "EnterSingleText",
            "platformId": "WIN",
            "textKey": "answer",
            "placeholder": self._placeholder(),
            "actions": self._actions(),
            "prompt": {"text": "Jankbox Chat"},
        }

    def _push_room_state(self, host, wsapp):
        host.send(wsapp, "object/set", {
            "key": "bc:room",
            "val": self._room_state(),
            "acl": ["rw *"],
        })

    def start_sequence(self, host, wsapp):
        host.set_room_state(wsapp, "EnterSingleText", mode="create",
                             extra=self._room_state())
        host.send(wsapp, "text/create", {
            "key": "answer",
            "val": "",
            "acl": ["rw *"],
        })

    def on_answer(self, host, wsapp, result):
        val = result.get("val", "")
        if not val:
            return
        sender_id = result.get("from")
        sender_name = host.player_names.get(sender_id, "???")
        labeled_val = f"{sender_name}: {val}"
        print(f"[broadcast] {labeled_val}")
        if host.gui:
            host.gui.add_message(labeled_val)

        self.history.insert(0, {"id": self._next_id, "sender_id": sender_id, "text": labeled_val})
        self._next_id += 1
        del self.history[MAX_HISTORY:]

        def back_to_textbox():
            self._push_room_state(host, wsapp)
            # clear the box so they can submit another one
            host.send(wsapp, "text/set", {
                "key": "answer",
                "val": "",
                "acl": ["rw *"],
            })

        # flash Lobby for 1s before returning with the updated
        # placeholder, as a visible "submitted" transition
        host.send(wsapp, "object/set", {
            "key": "bc:room",
            "val": {"state": "Lobby", "platformId": "WIN"},
            "acl": ["rw *"],
        })
        threading.Timer(0.05, back_to_textbox).start()

    def on_action(self, host, wsapp, result):
        clicked_id = result.get("body", {}).get("action")
        clicker_id = result.get("from")
        entry = next((e for e in self.history if e["id"] == clicked_id), None)
        if not entry or entry["sender_id"] != clicker_id:
            return  # not a history button, or not the original sender
        self.history.remove(entry)
        print(f"[deleted] {entry['text']}")
        if host.gui:
            host.gui.set_messages([e["text"] for e in reversed(self.history)])
        self._push_room_state(host, wsapp)
