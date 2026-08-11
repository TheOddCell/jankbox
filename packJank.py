import argparse
import os
import sys
import subprocess
import importlib.util


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_attr(path, module_name):
    """Loads `path` as a module and returns the attribute named after its
    filename (chatApp.py -> module.chatApp, chatAppGUI.py -> module.chatAppGUI, ...),
    the naming convention every game/UI module in this project follows.
    The engine module is the exception: see load_engine()."""
    module = load_module(path, module_name)
    attr_name = os.path.splitext(os.path.basename(path))[0]
    return getattr(module, attr_name)


def load_engine(path, module_name):
    """Loads the engine module and returns (host_cls, appTags_enum). The
    engine module always exports its connection class as `host` and its
    app tag enum as `appTags`, regardless of what the file itself is named."""
    module = load_module(path, module_name)
    return module.host, module.appTags


def check_selection(engine_path, game_path, ui_path, appTag_name):
    """Sanity-checks every module loads and the game id resolves, without
    running any of them. ui_path may be None (no GUI)."""
    _, appTags_enum = load_engine(engine_path, "engine_check")
    load_attr(game_path, "game_check")
    if ui_path:
        load_attr(ui_path, "ui_check")
    appTags_enum[appTag_name]


def build_entry_script(engine_path, game_path, ui_path, appTag_name, embed_sources):
    """Builds a standalone entry-point script that wires host(appTag, game(), gui())
    together, sourcing the appTags enum from the engine module. ui_path may be
    None, in which case host() is called without a gui. With embed_sources=True
    the modules' source is embedded inline so the script has no file
    dependencies (needed for packing with PyInstaller)."""

    def attr_name(path):
        return os.path.splitext(os.path.basename(path))[0]

    loader = (
        "import importlib.util, os, sys\n\n"
        "def load_module_from_path(path, name):\n"
        "    spec = importlib.util.spec_from_file_location(name, path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    return module\n\n"
        "def load_attr_from_path(path, name):\n"
        "    module = load_module_from_path(path, name)\n"
        "    return getattr(module, os.path.splitext(os.path.basename(path))[0])\n\n"
        "def load_engine_from_path(path, name):\n"
        "    module = load_module_from_path(path, name)\n"
        "    return module.host, module.appTags\n\n"
        "def load_module_from_source(source, name):\n"
        "    module = type(sys)(name)\n"
        "    exec(compile(source, name, 'exec'), module.__dict__)\n"
        "    return module\n\n"
        "def load_attr_from_source(source, name, attr_name):\n"
        "    return getattr(load_module_from_source(source, name), attr_name)\n\n"
        "def load_engine_from_source(source, name):\n"
        "    module = load_module_from_source(source, name)\n"
        "    return module.host, module.appTags\n\n"
    )

    if embed_sources:
        with open(engine_path) as f:
            engine_src = f.read()
        with open(game_path) as f:
            game_src = f.read()

        body = (
            f"host_cls, appTags_enum = load_engine_from_source({engine_src!r}, 'engine')\n"
            f"game_cls = load_attr_from_source({game_src!r}, 'game', {attr_name(game_path)!r})\n"
        )
        if ui_path:
            with open(ui_path) as f:
                ui_src = f.read()
            body += f"ui_cls = load_attr_from_source({ui_src!r}, 'ui', {attr_name(ui_path)!r})\n"
    else:
        body = (
            f"host_cls, appTags_enum = load_engine_from_path({engine_path!r}, 'engine_selected')\n"
            f"game_cls = load_attr_from_path({game_path!r}, 'game_selected')\n"
        )
        if ui_path:
            body += f"ui_cls = load_attr_from_path({ui_path!r}, 'ui_selected')\n"

    body += f"appTag = appTags_enum[{appTag_name!r}]\n"
    if ui_path:
        body += "host_cls(appTag, game_cls(), ui_cls())\n"
    else:
        body += "host_cls(appTag, game_cls())\n"
    return loader + body


def pack(engine_path, game_path, ui_path, appTag_name, output_script_path):
    """Writes a self-contained entry script to output_script_path and runs
    PyInstaller on it. ui_path may be None (no GUI). Returns the completed
    subprocess.CompletedProcess."""
    with open(output_script_path, "w") as f:
        f.write(build_entry_script(engine_path, game_path, ui_path, appTag_name, embed_sources=True))

    name = appTag_name.lower()
    return subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--onefile", "--name", name, output_script_path],
        cwd=os.path.dirname(os.path.abspath(output_script_path)),
        capture_output=True,
        text=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Pack a jackbox game into a standalone onefile executable")
    parser.add_argument("engine", help="path to the engine module (e.g. engine.py); its appTags enum is used")
    parser.add_argument("game", help="path to the game module (e.g. chatApp.py)")
    parser.add_argument("appTag", help="app tag id to use, e.g. QUIPLASH3")
    parser.add_argument("-u", "--ui", help="path to the UI module (e.g. chatAppGUI.py); omit to pack without a GUI")
    parser.add_argument("-o", "--output", help="entry script path (default: <appTag>.py next to the engine module)")
    args = parser.parse_args()

    output = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.engine)), f"{args.appTag.lower()}.py")

    try:
        check_selection(args.engine, args.game, args.ui, args.appTag)
    except Exception as e:
        print(f"Failed to load modules: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Packing {args.appTag} -> {output}")
    result = pack(args.engine, args.game, args.ui, args.appTag, output)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    dist_path = os.path.join(os.path.dirname(os.path.abspath(output)), "dist", args.appTag.lower())
    print(f"Packed to {dist_path}")


if __name__ == "__main__":
    main()
