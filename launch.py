#!/usr/bin/env python3
import argparse

from packJank import load_attr, load_engine


def main():
    parser = argparse.ArgumentParser(description="Launch a jackbox game")
    parser.add_argument("engine", help="path to the engine module (e.g. engine.py); its appTags enum is used")
    parser.add_argument("game", help="path to the game module (e.g. chatApp.py)")
    parser.add_argument("appTag", help="app tag id to use, e.g. QUIPLASH3")
    parser.add_argument("-u", "--ui", help="path to the UI module (e.g. chatAppGUI.py); omit to run without a GUI")
    args = parser.parse_args()

    host_cls, appTags_enum = load_engine(args.engine, "engine_selected")
    game_cls = load_attr(args.game, "game_selected")
    gui = load_attr(args.ui, "ui_selected")() if args.ui else None
    appTag = appTags_enum[args.appTag]

    host_cls(appTag, game_cls(), gui)


if __name__ == "__main__":
    main()
