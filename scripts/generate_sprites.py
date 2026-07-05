"""One-time character spritesheet generation from LPC layers ($0, CC-BY-SA).

Composites recognisable-by-silhouette LPC characters (body + eyes + clothing +
hair + facial hair), each a 576x256 four-direction walk sheet (9 frames/row).
Recolouring preserves the art's shading (luminance) while applying each
figure's palette. Output -> web/assets/sprites/, served locally; no runtime
dependency on the LPC repo. LPC art is CC-BY-SA 3.0 / GPL 3.0 — see
web/assets/sprites/CREDITS.md.

    python scripts/generate_sprites.py [id ...]
"""

import sys
from pathlib import Path

import httpx
from PIL import Image

RAW = ("https://raw.githubusercontent.com/liberatedpixelcup/"
       "Universal-LPC-Spritesheet-Character-Generator/master/spritesheets")
OUT = Path("web/assets/sprites")
CACHE = OUT / "_layers"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

BODY = "body/bodies/male/walk.png"
EYES = "eyes/human/adult/neutral/walk.png"
NOSE = "head/nose/straight/adult/walk.png"
BROW = "eyes/eyebrows/thick/adult/walk.png"

# (layer path, tint rgb | None). Order = back to front.
CHARS = {
    # Layer order: body, nose, eyes, eyebrows (face structure), then clothes,
    # hair, facial hair. Philosophers are bald+bearded (clean face).
    "socrates": [
        (BODY, None), (NOSE, None), (EYES, (66, 48, 38)), (BROW, (206, 200, 188)),
        ("torso/clothes/robe/female/walk/light_gray.png", (222, 214, 191)),   # cream toga
        ("beards/beard/basic/walk.png", (224, 219, 206)),                     # white beard, bald
    ],
    "twain": [
        (BODY, None), (NOSE, None), (EYES, (66, 48, 38)), (BROW, (228, 222, 212)),
        ("legs/pants/male/walk/gray.png", (224, 216, 198)),                   # cream trousers
        ("torso/clothes/longsleeve/formal/male/walk/white.png", (233, 226, 208)),  # cream suit
        ("hair/high_and_tight/adult/walk.png", (236, 232, 224)),              # neat white hair
        ("beards/mustache/walrus/walk.png", (236, 232, 224)),                 # white 'stache
    ],
    "tesla": [
        (BODY, None), (NOSE, None), (EYES, (66, 48, 38)), (BROW, (48, 38, 30)),
        ("legs/pants/male/walk/black.png", (34, 31, 29)),
        ("torso/clothes/longsleeve/formal/male/walk/white.png", (40, 36, 33)),     # black suit
        ("hair/high_and_tight/adult/walk.png", (44, 34, 27)),                 # neat dark hair
        ("beards/mustache/chevron/walk.png", (40, 32, 26)),                   # thin dark 'stache
    ],
    "darwin": [
        (BODY, None), (NOSE, None), (EYES, (66, 48, 38)), (BROW, (226, 220, 210)),
        ("legs/pants/male/walk/charcoal.png", None),
        ("torso/clothes/longsleeve/formal/male/walk/white.png", (74, 58, 42)),     # dark coat
        ("beards/beard/medium/walk.png", (230, 225, 216)),                    # white beard, bald
    ],
    "ramanujan": [
        (BODY, (156, 100, 62)), (NOSE, (150, 96, 60)), (EYES, (66, 48, 38)), (BROW, (34, 26, 20)),
        ("legs/pants/male/walk/gray.png", (231, 224, 208)),                   # white dhoti
        ("torso/clothes/longsleeve/longsleeve/male/walk.png", (228, 219, 199)),  # cream kurta
        ("hair/high_and_tight/adult/walk.png", (28, 22, 18)),                 # short dark hair
    ],
    "visitor": [
        (BODY, None), (NOSE, None), (EYES, (66, 48, 38)), (BROW, (96, 66, 44)),
        ("legs/pants/male/walk/navy.png", None),
        ("torso/clothes/longsleeve/formal/male/walk/white.png", (58, 78, 112)),    # navy coat
        ("hair/high_and_tight/adult/walk.png", (92, 64, 42)),                 # short brown hair
    ],
}


def fetch(path: str) -> Image.Image | None:
    cached = CACHE / path.replace("/", "__")
    if cached.exists():
        try:
            return Image.open(cached).convert("RGBA")
        except Exception:
            cached.unlink(missing_ok=True)
    try:
        r = httpx.get(f"{RAW}/{path}", timeout=30)
        r.raise_for_status()
        cached.write_bytes(r.content)
        return Image.open(cached).convert("RGBA")
    except Exception as exc:
        print(f"    skip (missing): {path}  [{type(exc).__name__}]")
        return None


def tint(im: Image.Image, rgb) -> Image.Image:
    """Recolour opaque pixels to `rgb`, keeping its hue and each pixel's shading.

    Multiplicative: shadow pixels scale the target down, highlights scale it up,
    so dark targets stay dark (no washing to grey) and light ones stay light.
    """
    if rgb is None:
        return im
    r0, g0, b0 = rgb
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            s = 0.5 + 1.05 * lum                # 0.5 .. 1.55
            px[x, y] = (min(255, int(r0 * s)), min(255, int(g0 * s)),
                        min(255, int(b0 * s)), a)
    return im


def build(cid: str) -> None:
    print(cid)
    base = None
    for path, rgb in CHARS[cid]:
        layer = fetch(path)
        if layer is None:
            continue
        layer = tint(layer, rgb)
        base = layer if base is None else Image.alpha_composite(base, layer)
    if base is None:
        print("  FAILED — no layers")
        return
    base.save(OUT / f"{cid}.png")
    print(f"  {cid}.png {base.size}")


def write_credits() -> None:
    (OUT / "CREDITS.md").write_text(
        "# Character sprite credits\n\n"
        "Character sprites are composited from the Liberated Pixel Cup (LPC) "
        "Universal Spritesheet, licensed CC-BY-SA 3.0 / GPL 3.0.\n\n"
        "Source: https://github.com/liberatedpixelcup/"
        "Universal-LPC-Spritesheet-Character-Generator\n\n"
        "See that repository's CREDITS.csv for the full list of contributing "
        "artists. Recolouring and composition by this project.\n",
        encoding="utf-8",
    )


def main() -> int:
    which = sys.argv[1:] or list(CHARS)
    for cid in which:
        build(cid)
    write_credits()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
