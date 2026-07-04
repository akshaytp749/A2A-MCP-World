"""One-time art generation for the illustrated café UI ($0, no API key).

Uses Pollinations.ai (free, keyless) for painterly illustrations, then rembg
(local U2Net, free) to cut characters out of whatever background FLUX paints.
Outputs are committed to web/assets/ and served locally — NO runtime
dependency on either. Re-run only to regenerate assets.

    python scripts/generate_art.py [name ...]   # omit names to build all
"""

import io
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

OUT = Path("web/assets")
OUT.mkdir(parents=True, exist_ok=True)

STYLE = (
    "soft painterly digital oil illustration, semi-realistic, warm candlelight from "
    "the left, muted rich autumn palette, gentle visible brushwork, cohesive art style, "
    "highly detailed face"
)

CHARACTERS = {
    "twain": "full body portrait of Mark Twain aged 70, wild white hair, bushy white "
             "walrus mustache, cream three-piece linen suit, black bow tie, holding an "
             "unlit cigar, wry amused smile, standing relaxed",
    "tesla": "full body portrait of Nikola Tesla aged 40, tall slender, neat dark "
             "side-parted hair, thin dark mustache, immaculate black three-piece suit "
             "with white wing collar, intense visionary eyes, standing tall",
    "socrates": "full body portrait of ancient Greek philosopher Socrates aged 65, bald "
                "head, broad snub nose, full grey-white beard, simple undyed woolen robe "
                "over one shoulder, barefoot, warm mischievous eyes, standing",
    "darwin": "full body portrait of Charles Darwin aged 65, long full white beard, bald "
              "crown with white side hair, heavy dark Victorian overcoat and waistcoat, "
              "gentle wise melancholy eyes, standing slightly stooped",
    "ramanujan": "full body portrait of Indian mathematician Srinivasa Ramanujan aged "
                 "30, warm brown skin, dark combed hair, gentle shy smile, plain cream "
                 "kurta shirt and white dhoti, holding a small dark slate, standing",
    "visitor": "full body portrait of a friendly timeless young traveler, short hair, "
               "navy wool coat over simple clothes, leather satchel, warm curious "
               "expression, standing",
}


def fetch(prompt: str, w: int, h: int, seed: int) -> Image.Image:
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt)}"
           f"?width={w}&height={h}&seed={seed}&nologo=true&model=flux")
    last = None
    for attempt in range(4):
        try:
            r = httpx.get(url, timeout=120)
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception as exc:  # transient upstream load
            last = exc
            print(f"    retry {attempt + 1}: {type(exc).__name__}")
            time.sleep(6)
    raise RuntimeError(f"image fetch failed: {last}")


_rembg_session = None


def cut_out(img: Image.Image) -> Image.Image:
    """Remove the background with rembg (local U2Net), then trim to the subject."""
    global _rembg_session
    from rembg import new_session, remove

    if _rembg_session is None:
        _rembg_session = new_session("u2net")
    out = remove(img.convert("RGBA"), session=_rembg_session).convert("RGBA")
    box = out.getbbox()
    return out.crop(box) if box else out


def build_backdrop():
    prompt = (
        "wide empty interior of a cozy timeless cafe-bar, NO people, long polished "
        "wooden bar with glowing amber bottles on the right, huge chalkboard wall "
        "covered in white handwritten mathematical equations on the left, mismatched "
        "antique furniture from many centuries, warm brass wall lamps, worn red rug, "
        "open double doors at the back showing a starry night courtyard, "
        + STYLE + ", cinematic wide establishing shot"
    )
    print("cafe backdrop...")
    img = fetch(prompt, 1024, 576, seed=42).convert("RGB")
    img.save(OUT / "cafe.jpg", quality=86)
    print(f"  cafe.jpg {img.size}")


def build_character(name: str):
    prompt = (
        f"{CHARACTERS[name]}, {STYLE}, whole figure head to feet, facing viewer, "
        "full length standing, generous margin around the figure, simple plain "
        "studio background, no text"
    )
    print(f"{name}...")
    img = cut_out(fetch(prompt, 512, 768, seed=abs(hash(name)) % 9999))
    img.thumbnail((300, 460), Image.LANCZOS)
    img.save(OUT / f"{name}.png")
    print(f"  {name}.png {img.size}")


def main() -> int:
    which = sys.argv[1:]
    if not which or "cafe" in which:
        build_backdrop()
    for name in CHARACTERS:
        if not which or name in which:
            build_character(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
