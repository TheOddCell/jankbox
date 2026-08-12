"""Encode a raster image as vector strokes and push it to a Jackbox ecast
"doodle" entity - the same drawing primitive Drawful-family games use -
over a `host` connection from engine.py.

Confirmed from Drawful 2's actual client bundle (the real network API,
traced through client.createDoodle()/client.strokeDoodle(), not just the
local canvas-rendering code):
    doodle/create params: {key, acl?, colors?, live, maxLayer?, maxPoints?, size?, weights?}
    doodle/stroke params: {key, color, weight, layer, points, brush?}
    doodle/undo params: {key}   (removes the most recent stroke)
    color: a literal hex string (e.g. "#ff0000"), not a palette index
    points on the LOCAL canvas before submission: "x1,y1|x2,y2|..."
        (pipe-delimited, comma-separated ints) - no direct network call
        site was found to confirm this exact string format survives
        unchanged into the doodle/stroke payload, so treat it as a
        strong guess, not a certainty, until verified against real traffic.

Unconfirmed / best-guess, needs live verification against a real room:
    - `size`'s exact shape - assumed {"width": w, "height": h}
    - whether the room's bc:room state needs to reference the doodle key
      (e.g. a "UGC" state field) for it to actually render on-screen, vs
      the doodle entity being visible on its own once created
"""

import time

from PIL import Image


def _quantize(image, max_colors):
    """Palette-reduces an image and returns (hex_palette, pixel_grid)
    where pixel_grid[y][x] is an index into hex_palette."""
    quantized = image.convert("RGB").quantize(colors=max_colors)
    palette = quantized.getpalette()[: max_colors * 3]
    hex_palette = [
        "#{:02x}{:02x}{:02x}".format(palette[i], palette[i + 1], palette[i + 2])
        for i in range(0, len(palette), 3)
    ]
    width, height = quantized.size
    pixels = quantized.load()
    grid = [[pixels[x, y] for x in range(width)] for y in range(height)]
    return hex_palette, grid


def image_to_strokes(image_path, width=16, height=16, max_colors=16, weight=1):
    """Loads an image, resizes/quantizes it, and run-length-compresses
    each row into horizontal strokes (one stroke per run of same-color
    pixels, instead of one per pixel). Returns (width, height,
    hex_palette, strokes), where each stroke is ready to hand to
    push_strokes()."""
    image = Image.open(image_path).resize((width, height), Image.NEAREST)
    hex_palette, grid = _quantize(image, max_colors)

    strokes = []
    for y, row in enumerate(grid):
        run_start = 0
        for x in range(1, width + 1):
            if x == width or row[x] != row[run_start]:
                color = hex_palette[row[run_start]]
                points = f"{run_start},{y}|{x - 1},{y}"
                strokes.append({"color": color, "weight": weight, "layer": 0, "points": points})
                run_start = x
    return width, height, hex_palette, strokes


def create_canvas(host, wsapp, key, width, height, colors=None, weights=None, live=False):
    """Sends doodle/create for a new canvas. `colors`/`weights` are just
    selectable-palette hints for the model, not a hard limit on what
    individual strokes can use - strokes carry their own literal color."""
    params = {"key": key, "size": {"width": width, "height": height}, "live": live}
    if colors:
        params["colors"] = colors
    if weights:
        params["weights"] = weights
    host.send(wsapp, "doodle/create", params)


def push_strokes(host, wsapp, key, strokes, delay_ms=0):
    """Sends one doodle/stroke per stroke. Pass delay_ms > 0 to pace
    strokes out instead of firing them all at once, useful while
    figuring out real-world rate limits on a fresh room."""
    for stroke in strokes:
        host.send(wsapp, "doodle/stroke", {"key": key, **stroke})
        if delay_ms:
            time.sleep(delay_ms / 1000)


def push_image(host, wsapp, key, image_path, width=16, height=16, max_colors=16, weight=1, delay_ms=0):
    """Convenience wrapper: create_canvas + image_to_strokes + push_strokes.
    Returns the number of strokes sent."""
    w, h, palette, strokes = image_to_strokes(image_path, width, height, max_colors, weight)
    create_canvas(host, wsapp, key, w, h, colors=palette)
    push_strokes(host, wsapp, key, strokes, delay_ms=delay_ms)
    return len(strokes)
