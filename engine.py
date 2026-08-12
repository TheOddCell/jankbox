import json
import sys
import uuid
import threading
import http.client
import websocket
from enum import Enum

_appTagValues = {'PROTOTYPE': 'prototype', 'ECASTTESTCLIENT': 'ecast-test-client', 'QUIPLASH2INTERLASHIONAL': 'quiplash2-international',
        'GUESSPIONAGECROWDPLAY': 'guesspionage-crowdplay', 'DRAWFUL2': 'drawful2', 'DRAWFUL2INTERNATIONAL': 'drawful2international',
        'ACQUISITIONS,INC.': 'acquisitions-inc', "YOUDON'TKNOWJACK2015": 'ydkj2015', 'DRAWFUL': 'drawful', 'WORDSPUD': 'wordspud',
        'LIESWATTER': 'lieswatter', 'FIBBAGE': 'fibbage', 'FIBBAGE2': 'fibbage2', 'EARWAX': 'earwax', 'BIDIOTS': 'auction',
        'BOMBCORP': 'bombintern', 'QUIPLASH': 'quiplash', "FAKIN'IT": 'fakinit', 'TEEK.O.': 'awshirt',
        'QUIPLASH2': 'quiplash2', 'TRIVIAMURDERPARTY': 'triviadeath', 'GUESSPIONAGE': 'pollposition', 'FIBBAGE3': 'fibbage3',
        'SURVIVETHEINTERNET': 'survivetheinternet', 'MONSTERSEEKINGMONSTER': 'monstermingle',
        'BRACKETEERING': 'bracketeering', 'CIVICDOODLE': 'overdrawn', "YOUDON'TKNOWJACK:FULLSTREAM": 'ydkj2018',
        'SPLITTHEROOM': 'splittheroom', 'MADVERSECITY': 'rapbattle', 'ZEEPLEDOME': 'slingshoot', 'PATENTLYSTUPID': 'patentlystupid',
        'TRIVIAMURDERPARTY2': 'triviadeath2', 'ROLEMODELS': 'rolemodels', 'JOKEBOAT': 'jokeboat',
        'DICTIONARIUM': 'ridictionary', 'PUSHTHEBUTTON': 'pushthebutton', 'TALKINGPOINTS': 'jackbox-talks',
        'QUIPLASH3': 'quiplash3', 'THEDEVILSANDTHEDETAILS': 'everyday', "CHAMP'DUP": 'worldchamps',
        "BLATHER'ROUND": 'blanky-blank', 'JOBJOB': 'apply-yourself', 'DRAWFULANIMATE': 'drawful-animate',
        'THEWHEELOFENORMOUSPROPORTIONS': 'the-wheel', 'THEPOLLMINE': 'survey-bomb', 'WEAPONSDRAWN': 'murder-detectives',
        'QUIPLASH3STARTER': 'quiplash3-tjsp', 'TEEK.O.STARTER': 'awshirt-tjsp', 'TRIVIAMURDERPARTY2STARTER': 'triviadeath2-tjsp',
        'FIBBAGE4': 'fourbage', 'ROOMERANG': 'htmf', 'JUNKTOPIA': 'antique-freak', 'NONSENSORY': 'range-game', 'QUIXORT': 'lineup',
        'TEST': 'ecast-test-client', 'PROTOTYPE': 'prototype', 'TEEKO-WEB': '@teeko-web', 'MODERATOR': '@moderator', 'CONNECT': '@connect',
        'RUIN': 'you-ruined-it'}

appTags = Enum('appTags', _appTagValues)


class host():
    """Generic ecast room/host connection. Game-specific behavior lives in the
    `app` object passed in, which must implement `on_message(host, wsapp, opcode, result)`.
    If `app` also implements `on_command(host, wsapp, line)`, lines typed on
    stdin while the host is running are forwarded to it (started once the
    connection is confirmed live).

    `gui` is optional and must implement `set_code(code)` and `run()`. When
    given, the websocket connection runs on a background thread so
    `gui.run()` can own the main thread (required by most GUI toolkits'
    event loops)."""

    def __init__(self, appTag, app, gui=None):
        self.appTag = appTag.value
        self.app = app
        self.gui = gui
        self.seq = 0
        self.player_names = {}
        self._command_thread_started = False
        self._create_room()

        if self.gui:
            self.gui.set_code(self.code)
            threading.Thread(target=self.connectWS, daemon=True).start()
            self.gui.run()
        else:
            self.connectWS()

    def _create_room(self):
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

    def _start_command_thread(self, wsapp):
        if self._command_thread_started or not hasattr(self.app, "on_command"):
            return
        self._command_thread_started = True
        threading.Thread(target=self._read_commands, args=(wsapp,), daemon=True).start()

    def _read_commands(self, wsapp):
        for line in sys.stdin:
            line = line.strip()
            if line:
                self.app.on_command(self, wsapp, line)

    def connectWS(self):
        def on_message(wsapp, message):
            print(message)
            data = json.loads(message)
            opcode = data.get("opcode")
            result = data.get("result", {})

            if opcode == "client/connected":
                self.player_names[result.get("id")] = result.get("name", "???")
            elif opcode == "client/welcome":
                self._start_command_thread(wsapp)

            self.app.on_message(self, wsapp, opcode, result)

        wsapp = websocket.WebSocketApp("wss://"+self.host+"/api/v2/rooms/"+self.code+"/play?role=host&format=json&host-token="+self.token, subprotocols=["ecast-v0"], on_message=on_message)
        wsapp.run_forever()
