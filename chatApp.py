import threading

MAX_HISTORY = 20


class chatApp():
    """Skips Lobby/MakeSingleChoice entirely and turns the room into a
    broadcast textbox: whatever a player submits gets shown to everyone,
    labeled with their name, then the box resets for the next message.
    Chat history is shown as a stack of buttons below Send (most recent
    on top) in addition to the most recent message in the placeholder.

    Clicking your own message deletes it; admins can delete anyone's,
    from in-room, the GUI, or the terminal. Admin status is managed by
    the operator via the GUI or by typing terminal commands while the
    host is running:
        admin <username>      grant admin
        deadmin <username>    revoke admin
        delete <id>           delete a history entry by id
        list                  print current history with ids
    Admins can also grant/revoke admin from inside the room by typing
    "/admin <username>" or "/deadmin <username>" as their message.
    """

    def __init__(self):
        self.history = []  # [{"id", "sender_id", "text"}, ...], most recent first
        self._next_id = 1
        self.admins = set()  # player ids

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

    def _reset_input(self, host, wsapp):
        host.send(wsapp, "text/set", {
            "key": "answer",
            "val": "",
            "acl": ["rw *"],
        })

    def _sync_gui(self, host):
        if host.gui:
            host.gui.set_messages([(e["id"], e["text"]) for e in self.history])

    def start_sequence(self, host, wsapp):
        if host.gui:
            host.gui.on_delete_requested = lambda entry_id: self._delete_by_id(host, wsapp, entry_id, None)
            host.gui.on_admin_grant_requested = lambda name: self.set_admin(host, wsapp, name, True)
            host.gui.on_admin_revoke_requested = lambda name: self.set_admin(host, wsapp, name, False)

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

        # in-room admin commands, only honored from an existing admin
        if sender_id in self.admins:
            parts = val.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].lower() in ("/admin", "/deadmin"):
                self.set_admin(host, wsapp, parts[1].strip(), parts[0].lower() == "/admin")
                self._reset_input(host, wsapp)
                return

        sender_name = host.player_names.get(sender_id, "???")
        labeled_val = f"{sender_name}: {val}"
        print(f"[broadcast] {labeled_val}")

        self.history.insert(0, {"id": self._next_id, "sender_id": sender_id, "text": labeled_val})
        self._next_id += 1
        del self.history[MAX_HISTORY:]
        self._sync_gui(host)

        def back_to_textbox():
            self._push_room_state(host, wsapp)
            self._reset_input(host, wsapp)

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
        self._delete_by_id(host, wsapp, clicked_id, clicker_id)

    def _delete_by_id(self, host, wsapp, entry_id, requester_id):
        """requester_id=None means the operator (GUI/terminal) is deleting,
        which is always allowed. Otherwise only the original sender or an
        admin can delete."""
        entry = next((e for e in self.history if e["id"] == entry_id), None)
        if not entry:
            return
        if requester_id is not None and entry["sender_id"] != requester_id and requester_id not in self.admins:
            return
        self.history.remove(entry)
        print(f"[deleted] {entry['text']}")
        self._sync_gui(host)
        self._push_room_state(host, wsapp)

    def set_admin(self, host, wsapp, username, admin):
        target_id = next((pid for pid, name in host.player_names.items()
                           if name.lower() == username.lower()), None)
        if target_id is None:
            print(f"[admin] no player named {username!r} found")
            return
        if admin:
            self.admins.add(target_id)
            print(f"[admin] {username} is now admin")
        else:
            self.admins.discard(target_id)
            print(f"[admin] {username} is no longer admin")

    def on_command(self, host, wsapp, line):
        """Terminal command handler, called by engine.py's stdin reader."""
        parts = line.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "admin" and arg:
            self.set_admin(host, wsapp, arg, True)
        elif cmd in ("deadmin", "unadmin") and arg:
            self.set_admin(host, wsapp, arg, False)
        elif cmd == "delete" and arg:
            try:
                entry_id = int(arg)
            except ValueError:
                print("[command] usage: delete <id>")
                return
            self._delete_by_id(host, wsapp, entry_id, None)
        elif cmd == "list":
            if not self.history:
                print("[history] empty")
            for e in self.history:
                print(f"  [{e['id']}] {e['text']}")
        else:
            print("[command] usage: admin <username> | deadmin <username> | delete <id> | list")
