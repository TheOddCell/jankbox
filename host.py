import json
import uuid
import threading
import websocket


class host():
    def __init__(self, appTag):
        self.appTag = appTag.value
        import http.client
        conn = http.client.HTTPSConnection("ecast.jackboxgames.com")
        conn.request("POST", "/api/v2/rooms?userId="+str(uuid.uuid4())+"&appTag="+self.appTag)
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        response = json.loads(data)
        if not response.get("ok"):
            raise RuntimeError(f"Failed to create room: {response.get('error')}")
        self.token = response["body"]["token"]
        self.code = response["body"]["code"]
        print("Game Code: " + self.code)
        self.host = response["body"]["host"]
        self.seq = 0
        self.wall_items = []
        self.player_names = {}
        self.connectWS()

    def send(self, wsapp, opcode, params):
        self.seq += 1
        msg = {"seq": self.seq, "opcode": opcode, "params": params}
        wsapp.send(json.dumps(msg))
        return msg

    def set_room_state(self, wsapp, state, mode="set", extra=None):
        val = {"state": state, "platformId": "WIN"}
        if extra:
            val.update(extra)
        print(f"[bc:room] state={state}")
        self.send(wsapp, f"object/{mode}", {
            "key": "bc:room",
            "val": val,
            "acl": ["rw *"],
        })

    def connectWS(self):
        def start_sequence(wsapp):
            # skip Lobby/MakeSingleChoice entirely, go straight to the
            # textbox as soon as someone joins
            self.set_room_state(wsapp, "EnterSingleText", mode="create", extra={
                "textKey": "answer",
                "placeholder": "say something",
                "inlineSubmitText": "Broadcast",
            })
            self.send(wsapp, "text/create", {
                "key": "prompt",
                "val": "say something",
                "acl": ["rw *"],
            })
            self.send(wsapp, "text/create", {
                "key": "answer",
                "val": "",
                "acl": ["rw *"],
            })

        def on_message(wsapp, message):
            print(message)
            data = json.loads(message)
            opcode = data.get("opcode")
            result = data.get("result", {})
            key = result.get("key")

            if opcode == "text" and key == "answer":
                val = result.get("val", "")
                if val:
                    sender_name = self.player_names.get(result.get("from"), "???")
                    labeled_val = f"{sender_name}: {val}"
                    print(f"[broadcast] {labeled_val}")

                    def back_to_textbox():
                        self.send(wsapp, "object/set", {
                            "key": "bc:room",
                            "val": {
                                "state": "EnterSingleText",
                                "platformId": "WIN",
                                "textKey": "answer",
                                "placeholder": labeled_val,
                                "inlineSubmitText": "Broadcast",
                            },
                            "acl": ["rw *"],
                        })
                        # clear the box so they can submit another one
                        self.send(wsapp, "text/set", {
                            "key": "answer",
                            "val": "",
                            "acl": ["rw *"],
                        })

                    # flash Lobby for 1s before returning with the updated
                    # placeholder, as a visible "submitted" transition
                    self.send(wsapp, "object/set", {
                        "key": "bc:room",
                        "val": {"state": "Lobby", "platformId": "WIN"},
                        "acl": ["rw *"],
                    })
                    threading.Timer(1, back_to_textbox).start()
            elif opcode == "client/connected":
                self.player_names[result.get("id")] = result.get("name", "???")
                start_sequence(wsapp)

        wsapp = websocket.WebSocketApp("wss://"+self.host+"/api/v2/rooms/"+self.code+"/play?role=host&format=json&host-token="+self.token, subprotocols=["ecast-v0"], on_message=on_message)
        wsapp.run_forever()
