"""앱 아이콘(thermo.ico)을 그린다: 어두운 둥근 사각형 위 온도계. 빌드 때만 쓴다(Pillow)."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

BG = (18, 20, 24, 255)
EDGE = (42, 47, 56, 255)
GLASS = (232, 234, 237, 255)
RED = (255, 107, 107, 255)
GREEN = (52, 199, 89, 255)


def draw(size: int = 512) -> Image.Image:
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((s * .04, s * .04, s * .96, s * .96), radius=s * .22, fill=BG, outline=EDGE, width=int(s * .02))
    # 온도계: 관 + 구
    cx = s * .5
    tube_w, top, bulb_r = s * .16, s * .16, s * .15
    bulb_cy = s * .70
    d.rounded_rectangle((cx - tube_w / 2, top, cx + tube_w / 2, bulb_cy), radius=tube_w / 2, fill=GLASS)
    d.ellipse((cx - bulb_r, bulb_cy - bulb_r, cx + bulb_r, bulb_cy + bulb_r), fill=GLASS)
    # 수은
    inner = tube_w * .45
    d.rounded_rectangle((cx - inner / 2, s * .36, cx + inner / 2, bulb_cy), radius=inner / 2, fill=RED)
    d.ellipse((cx - bulb_r * .72, bulb_cy - bulb_r * .72, cx + bulb_r * .72, bulb_cy + bulb_r * .72), fill=RED)
    # 눈금
    for i, y in enumerate((.24, .32, .40, .48, .56)):
        w = s * (.10 if i % 2 == 0 else .06)
        d.rounded_rectangle((cx + tube_w * .75, s * y - s * .012, cx + tube_w * .75 + w, s * y + s * .012),
                            radius=s * .012, fill=EDGE if i else GREEN)
    # 판정 점 (정상 = 초록)
    d.ellipse((s * .70, s * .70, s * .84, s * .84), fill=GREEN, outline=BG, width=int(s * .02))
    return im


def main(out: str) -> None:
    img = draw()
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"아이콘: {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("thermo.ico")))
