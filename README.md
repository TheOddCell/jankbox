# Jankbox
a "engine" for custom apps/games in jackbox with a chat app example

# How To Use
make an instance of the host() class (in engine.py) and input an appTag, a game object, and optionally a gui object. Use the appTags enum from engine.py.

Run a game directly:
```
./launch.py ./engine.py ./chatApp.py QUIPLASH3 -u ./chatAppGUI.py
```

Pack a game into a standalone onefile executable:
```
./packJank.py ./engine.py ./chatApp.py QUIPLASH3 -u ./chatAppGUI.py
```
