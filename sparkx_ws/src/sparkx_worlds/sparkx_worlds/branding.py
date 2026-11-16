"""Airoot x STEMLearn.AI branding panels for the SparkX arena.

Theme taken from the Airoot TurtleBot S1: ink body, crimson Airoot mark, off-white edge lines and
the three diagonal slashes. Artwork comes from `models/sparkx_media/brand/` (see its README):
the Airoot logo is a mask rasterized from the S1 CAD, the STEMLearn.AI logo is the official asset.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import pbr

INK = (25, 25, 25)
CRIMSON = (229, 2, 39)
OFFWHITE = (240, 239, 242)
NAVY = (36, 40, 96)

BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'models', 'sparkx_media', 'brand')
EVENT = 'SPARKX'
EVENT_SUB = 'DISASTER RESPONSE CHALLENGE'


def _brand_dir():
    if os.path.isdir(BRAND_DIR):
        return BRAND_DIR
    from ament_index_python.packages import get_package_share_directory
    return os.path.join(get_package_share_directory('sparkx_worlds'), 'models', 'sparkx_media', 'brand')


def font(size, weight='black'):
    cands = {'black': ['/usr/share/fonts/truetype/lato/Lato-Black.ttf'],
             'bold': ['/usr/share/fonts/truetype/lato/Lato-Bold.ttf'],
             'regular': ['/usr/share/fonts/truetype/lato/Lato-Regular.ttf']}[weight]
    for p in cands + ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                      '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def airoot(height, color=CRIMSON, part='logo'):
    """Airoot artwork as an RGBA image of the given pixel height. part: logo | mark | wordmark."""
    m = Image.open(os.path.join(_brand_dir(), f'airoot_{part}_mask.png')).convert('L')
    m = m.resize((max(1, int(m.width * height / m.height)), height), Image.LANCZOS)
    out = Image.new('RGBA', m.size, color + (0,))
    out.putalpha(m)
    return out


def stemlearn(height, on_dark=False, part='logo'):
    """STEMLearn.AI artwork as RGBA. On dark backgrounds the navy letters are flipped to off-white
    (the teal / amber / coral accents keep their colours)."""
    im = Image.open(os.path.join(_brand_dir(), f'stemlearn_{part}.png')).convert('RGBA')
    a = np.asarray(im).copy()
    ys, xs = np.nonzero(a[..., 3] > 8)
    im = Image.fromarray(a).crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    if on_dark:
        a = np.asarray(im, np.float32).copy()
        d = np.linalg.norm(a[..., :3] - np.asarray(NAVY, np.float32), axis=2)
        k = np.clip(1.0 - d / 70.0, 0, 1)[..., None]
        a[..., :3] = a[..., :3] * (1 - k) + np.asarray(OFFWHITE, np.float32) * k
        im = Image.fromarray(a.astype(np.uint8), 'RGBA')
    return im.resize((max(1, int(im.width * height / im.height)), height), Image.LANCZOS)


def _panel(w, h, color=INK, seed=0):
    """Printed panel base with a little surface variation so it does not look like a flat fill."""
    n = pbr.fbm(256, 4, seed)
    n = np.asarray(Image.fromarray((n * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255
    a = np.asarray(color, np.float32)[None, None, :] * (0.9 + 0.2 * n[..., None])
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert('RGBA')


def _edge_line(img, inset, width, chamfer, color=OFFWHITE):
    """Off-white outline with chamfered corners (the S1 top-plate edge line)."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    i, c = inset, chamfer
    pts = [(i + c, i), (w - i - c, i), (w - i, i + c), (w - i, h - i - c), (w - i - c, h - i), (i + c, h - i),
           (i, h - i - c), (i, i + c), (i + c, i)]
    d.line(pts, fill=color, width=width, joint='curve')


def _slashes(img, x, y0, y1, color=OFFWHITE, n=3, width=None, gap=None):
    """The three diagonal slashes from the S1 side panel."""
    d = ImageDraw.Draw(img)
    hgt = y1 - y0
    width = width or max(4, hgt // 9)
    gap = gap or int(hgt * 0.36)
    lean = int(hgt * 0.55)
    for k in range(n):
        xa = x + k * gap
        d.line([(xa, y1), (xa + lean, y0)], fill=color, width=width)
    return x + (n - 1) * gap + lean


def _grime(img, amount=0.22, seed=3):
    """Dust and soot settling on a panel that has been through a blast."""
    w, h = img.size
    n = pbr.fbm(512, 5, seed)
    n = np.asarray(Image.fromarray((n * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255
    streak = np.linspace(0.4, 1.0, h)[:, None]
    k = np.clip(n * 1.9 - 0.8, 0, 1) * streak * amount
    a = np.asarray(img.convert('RGB'), np.float32)
    dust = np.array([120, 110, 96], np.float32)
    return Image.fromarray(np.clip(a * (1 - k[..., None]) + dust * k[..., None], 0, 255).astype(np.uint8))


def _paste_c(img, art, cx, cy):
    img.alpha_composite(art, (int(cx - art.width / 2), int(cy - art.height / 2)))


def _fit_text(d, text, max_w, size, weight='black'):
    f = font(size, weight)
    tb = d.textbbox((0, 0), text, font=f)
    if tb[2] - tb[0] > max_w:
        f = font(int(size * max_w / (tb[2] - tb[0])), weight)
        tb = d.textbbox((0, 0), text, font=f)
    return f, tb


def banner():
    """8 m x 2 m perimeter banner: Airoot | STEMLearn.AI on ink."""
    w, h = 2048, 512
    img = _panel(w, h, INK, 1)
    _edge_line(img, 26, 7, 46)
    a = airoot(190)
    s = stemlearn(128, on_dark=True)
    gap = 110
    total = a.width + gap + s.width
    x = (w - total) // 2 - 120
    img.alpha_composite(a, (x, (h - a.height) // 2))
    d = ImageDraw.Draw(img)
    xd = x + a.width + gap // 2
    d.line([(xd, h * 0.3), (xd, h * 0.7)], fill=OFFWHITE, width=5)
    img.alpha_composite(s, (x + a.width + gap, (h - s.height) // 2))
    _slashes(img, w - 330, int(h * 0.34), int(h * 0.66), width=16, gap=64)
    return _grime(img, 0.25, 11)


def billboard():
    """6 m x 3 m event billboard."""
    w, h = 2048, 1024
    img = _panel(w, h, INK, 2)
    _edge_line(img, 34, 9, 60)
    d = ImageDraw.Draw(img)
    f, tb = _fit_text(d, EVENT, w * 0.5, 300)
    tx = 150
    d.text((tx, 130 - tb[1]), EVENT, fill=OFFWHITE, font=f)
    _slashes(img, tx + (tb[2] - tb[0]) + 90, 150, 150 + (tb[3] - tb[1]), width=26, gap=92)
    f2, tb2 = _fit_text(d, EVENT_SUB, w - 2 * tx, 104, 'bold')
    y2 = 130 + (tb[3] - tb[1]) + 70
    d.text((tx, y2 - tb2[1]), EVENT_SUB, fill=CRIMSON, font=f2)
    d.line([(tx, y2 + (tb2[3] - tb2[1]) + 52), (w - tx, y2 + (tb2[3] - tb2[1]) + 52)], fill=OFFWHITE, width=5)
    f3 = font(46, 'bold')
    d.text((tx, h - 330), 'ROBOTICS & AI CHALLENGE  ·  POWERED BY', fill=(170, 170, 174), font=f3)
    a = airoot(150)
    s = stemlearn(104, on_dark=True)
    img.alpha_composite(a, (tx, h - 245))
    img.alpha_composite(s, (w - tx - s.width, h - 245 + (a.height - s.height) // 2))
    return _grime(img, 0.2, 12)


def panel_airoot():
    w, h = 1024, 512
    img = _panel(w, h, INK, 3)
    _edge_line(img, 22, 6, 40)
    a = airoot(200)
    if a.width > w - 250:
        a = airoot(int(200 * (w - 250) / a.width))
    _paste_c(img, a, w / 2 - 50, h / 2)
    _slashes(img, w - 190, int(h * 0.36), int(h * 0.64), width=9, gap=38)
    return _grime(img, 0.2, 13)


def panel_stemlearn():
    w, h = 1024, 512
    img = _panel(w, h, OFFWHITE, 4)
    _edge_line(img, 22, 6, 40, color=NAVY)
    s = stemlearn(122)
    if s.width > w - 150:
        s = stemlearn(int(122 * (w - 150) / s.width))
    _paste_c(img, s, w / 2, h / 2)
    return _grime(img, 0.2, 14)


def basepad():
    """8 m x 8 m rescue-base start pad painted on scanned concrete (replaces the generic helipad)."""
    px = 1024
    conc = pbr.load('dirty_concrete', 'Diffuse', 512)
    base = pbr.tile_image(conc, 2, 2).convert('RGBA')
    paint = Image.new('RGBA', (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(paint)
    c = px // 2
    d.ellipse([70, 70, px - 70, px - 70], outline=OFFWHITE + (255,), width=26)
    d.ellipse([112, 112, px - 112, px - 112], outline=CRIMSON + (255,), width=8)
    for k in range(4):                                       # corner chevrons pointing at the pad
        sx, sy = (1 if k in (0, 3) else -1), (1 if k in (0, 1) else -1)
        x, y = (30 if sx > 0 else px - 30), (30 if sy > 0 else px - 30)
        d.line([(x + sx * 110, y), (x, y), (x, y + sy * 110)], fill=OFFWHITE + (255,), width=18, joint='curve')
    mark = airoot(250, part='mark')
    _paste_c(paint, mark, c, c - 44)
    word = airoot(84, part='wordmark')
    _paste_c(paint, word, c, c + 150)
    f = font(58)
    tb = d.textbbox((0, 0), 'RESCUE BASE', font=f)
    d.text((c - (tb[2] - tb[0]) / 2, 215 - tb[1]), 'RESCUE BASE', fill=OFFWHITE + (255,), font=f)
    s = stemlearn(60, on_dark=True)
    _paste_c(paint, s, c, px - 222)
    # worn paint: flake the alpha, then let the concrete shading show through
    wear = pbr.fbm(px, 6, 21)
    alpha = np.asarray(paint, np.float32)[..., 3] * np.clip(0.5 + wear * 1.1, 0, 1)
    paint.putalpha(Image.fromarray(alpha.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)))
    shade = pbr.luminance(base.convert('RGB'))
    shade = np.clip(shade / (np.median(shade) + 1e-6), 0.65, 1.2)[..., None]
    p = np.asarray(paint, np.float32)
    p[..., :3] *= shade
    base.alpha_composite(Image.fromarray(np.clip(p, 0, 255).astype(np.uint8), 'RGBA'))
    return base.convert('RGB')


def robot_deck():
    """Graphic for the top of the rover's electronics bay (0.42 m x 0.32 m, +X of the robot to the
    right of the image). The sensor mast stands at 70 % of the width, so that area stays clear."""
    w, h = 1024, 780
    img = _panel(w, h, INK, 6)
    _edge_line(img, 22, 7, 60)
    _paste_c(img, airoot(230, part='mark'), w * 0.30, h * 0.40)
    _paste_c(img, airoot(72, part='wordmark'), w * 0.30, h * 0.70)
    s = stemlearn(50, on_dark=True)
    img.alpha_composite(s, (w - 70 - s.width, h - 62 - s.height))
    _slashes(img, w - 250, 70, 170, width=11, gap=42)
    return img.convert('RGB')


def gantry():
    """7 m x 1 m start-gate header."""
    w, h = 2048, 292
    img = _panel(w, h, INK, 7)
    _edge_line(img, 14, 5, 30)
    d = ImageDraw.Draw(img)
    f = font(120)
    tb = d.textbbox((0, 0), EVENT, font=f)
    d.text((70, h / 2 - (tb[3] - tb[1]) / 2 - tb[1]), EVENT, fill=OFFWHITE, font=f)
    x = 70 + (tb[2] - tb[0]) + 40
    x = _slashes(img, x, int(h * 0.3), int(h * 0.7), width=11, gap=40) + 40
    f2 = font(50, 'bold')
    d.text((x, h / 2 - 30), EVENT_SUB, fill=CRIMSON, font=f2)
    a = airoot(92)
    s = stemlearn(62, on_dark=True)
    img.alpha_composite(s, (w - 60 - s.width, (h - s.height) // 2))
    img.alpha_composite(a, (w - 60 - s.width - 50 - a.width, (h - a.height) // 2))
    return _grime(img, 0.15, 17)


def flag(which):
    """0.8 m x 3.0 m feather-flag sail (portrait)."""
    w, h = 512, 1920
    dark = which == 'airoot'
    img = _panel(w, h, INK if dark else OFFWHITE, 8 if dark else 9)
    art = (airoot(150) if dark else stemlearn(118)).rotate(90, expand=True)
    _paste_c(img, art, w / 2, h * 0.55)
    if dark:
        _paste_c(img, airoot(250, part='mark'), w / 2, h * 0.14)
        d = ImageDraw.Draw(img)
        for k in range(3):
            d.line([(150 + k * 70, h - 120), (230 + k * 70, h - 260)], fill=OFFWHITE, width=22)
    else:
        _paste_c(img, stemlearn(260, part='icon'), w / 2, h * 0.14)
    return _grime(img, 0.15, 18)


def build_all(out_dir):
    jobs = {'brand_banner.jpg': banner, 'brand_billboard.jpg': billboard, 'brand_airoot.jpg': panel_airoot,
            'brand_stemlearn.jpg': panel_stemlearn, 'basepad.jpg': basepad, 'robot_deck.jpg': robot_deck,
            'brand_gantry.jpg': gantry, 'brand_flag_airoot.jpg': lambda: flag('airoot'),
            'brand_flag_stemlearn.jpg': lambda: flag('stemlearn')}
    for name, fn in jobs.items():
        fn().convert('RGB').save(os.path.join(out_dir, name), quality=90, optimize=True)
    # normal / roughness of the pad follow the concrete it is painted on
    pbr.save(pbr.tile_image(pbr.load('dirty_concrete', 'nor_gl', 512), 2, 2), os.path.join(out_dir, 'basepad_n.jpg'), 92)
    pbr.save(pbr.tile_image(pbr.load('dirty_concrete', 'Rough', 512), 2, 2), os.path.join(out_dir, 'basepad_r.jpg'))
    for old in ('helipad.png',):
        if os.path.exists(os.path.join(out_dir, old)):
            os.remove(os.path.join(out_dir, old))
    return sorted(jobs)
