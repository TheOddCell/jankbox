# Jankbox
a "engine" for custom apps/games in jackbox with a chat app example and a more advanced VNC client example

<img width="393" height="103" alt="image" src="https://github.com/user-attachments/assets/60650a79-38bb-44ec-abf1-0ad8eb701335" />

<sup><sub>the above image [is not related](https://www.reddit.com/r/jackboxgames/comments/1sz2m1f/comment/oizbul0/?context=3) but kind of applicable. it's grey area but very close to fine and also nobody cares</sup></sub>

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
