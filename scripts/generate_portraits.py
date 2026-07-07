"""Head-and-shoulders character portraits for the UI ($0, keyless).

Uses Pollinations.ai (free) to render one clean, recognizable portrait per
figure — the faces are the centerpiece of the interface. Square, consistent
style. Output -> web/assets/portraits/, served locally. Re-run to regenerate.

    python scripts/generate_portraits.py [id ...]
"""

import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

OUT = Path("web/assets/portraits")
OUT.mkdir(parents=True, exist_ok=True)

STYLE = (
    "painterly semi-realistic digital portrait, warm cinematic rim lighting, "
    "rich muted color, cohesive art style, dark softly-blurred background, "
    "head and shoulders, face centered, looking at viewer, dignified"
)

FACES = {
    "socrates": "Socrates the ancient Greek philosopher, about 65, bald head, broad "
                "snub nose, full grey-white beard, warm intelligent mischievous eyes, "
                "simple undyed woolen robe",
    "twain": "Mark Twain, about 70, wild swept-back white hair, thick bushy white "
             "walrus mustache, cream linen suit with black bow tie, wry amused warm eyes",
    "tesla": "Nikola Tesla about 40, based on his real photograph: lean angular face, "
             "high cheekbones, dark hair parted sharply in the CENTRE and slicked flat, "
             "thin dark mustache, deep-set intense eyes, black frock coat, white shirt",
    "darwin": "Charles Darwin, about 65, long full white beard, bald crown with white "
              "side hair, heavy brow, gentle wise melancholy eyes, dark Victorian coat",
    "ramanujan": "Srinivasa Ramanujan, YOUNG man aged about 30, based on his real "
                 "photograph: full round youthful clean-shaven face, short dark hair "
                 "combed back, warm brown skin, large gentle earnest eyes, plain white "
                 "collared shirt, humble and boyish",
    "visitor": "a friendly timeless traveler, about 30, short tidy hair, warm curious "
               "open expression, simple navy collared coat",
}


def fetch(prompt: str, seed: int) -> bytes:
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt)}"
           f"?width=640&height=640&seed={seed}&nologo=true&model=flux")
    last = None
    for attempt in range(4):
        try:
            r = httpx.get(url, timeout=120)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            print(f"    retry {attempt + 1}: {type(exc).__name__}")
            time.sleep(6)
    raise RuntimeError(f"portrait fetch failed: {last}")


def main() -> int:
    which = sys.argv[1:] or list(FACES)
    for cid in which:
        if cid not in FACES:
            continue
        print(cid)
        data = fetch(f"{FACES[cid]}, {STYLE}", seed=abs(hash(cid)) % 9999)
        (OUT / f"{cid}.jpg").write_bytes(data)
        print(f"  {cid}.jpg {len(data) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
