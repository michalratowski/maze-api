"""
raycast.py – DDA raycasting engine: Office Space aesthetic.

Visual style
------------
  Walls    – smooth beige/cream painted drywall with a chair-rail divider
  Ceiling  – drop-ceiling tiles (off-white grid) with fluorescent light strips
  Floor    – corporate blue-grey carpet with a subtle weave pattern
  Doors    – painted-metal security doors: wood-veneer lower panel, narrow
             frosted-glass upper pane, and a coloured RFID card-reader badge
  Keys     – HID-style RFID access cards (blue / red / yellow security level)
  Docs     – A4 document sheets: white paper, blue letterhead, grey text lines

Grid tokens (semicolon rows, comma cells)
-----------------------------------------
  W         – wall
  BD/RD/YD  – blue / red / yellow security door
  E         – empty floor
  P         – player start (exactly one required)
  BK/RK/YK  – blue / red / yellow keycard
  DOC       – document to collect
"""

import math
from PIL import Image

# ── Screen & optics ──────────────────────────────────────────────────────────

SCREEN_W  = 500
SCREEN_H  = 500
FOV       = math.pi / 3        # 60° horizontal field of view
_FOG_DIST = 20.0               # corridor wash-out distance

# ── Cell-type constants ───────────────────────────────────────────────────────

_EMPTY  = 0
_WALL   = 1
_DOOR_B = 2
_DOOR_R = 3
_DOOR_Y = 4
_DOOR_H = 5    # hidden door – solid like a wall, rendered like a wall
_CHAIR  = 6    # office chair   (solid, custom render – half-height)
_DESK_C = 7    # desk + computer (solid, custom render)
_DESK_E = 8    # empty desk     (solid, custom render)
_WALL_W = 9    # wall with window (solid, custom render)

_ITEM_B   = 10   # blue  keycard
_ITEM_R   = 11   # red   keycard
_ITEM_Y   = 12   # yellow keycard
_ITEM_DOC = 13   # document

_DOORS     = frozenset({_DOOR_B, _DOOR_R, _DOOR_Y})
_FURNITURE = frozenset({_CHAIR, _DESK_C, _DESK_E})
# All solid cell types that use cell-boundary DDA hit (not mid-plane):
_WALLS     = frozenset({_WALL, _DOOR_H, _WALL_W}) | _FURNITURE

# ── Sprite world-height (controls billboard size on screen) ───────────────────

_ITEM_WORLD_H: dict[int, float] = {
    _ITEM_B:   0.46,
    _ITEM_R:   0.46,
    _ITEM_Y:   0.46,
    _ITEM_DOC: 0.38,   # documents lie flatter → shorter billboard
}

# ── Player facing → camera angle ─────────────────────────────────────────────

FACING_ANGLES: dict[str, float] = {
    "up":    -math.pi / 2,
    "down":   math.pi / 2,
    "left":   math.pi,
    "right":  0.0,
}

# ── Office colour palette ─────────────────────────────────────────────────────
# Fog is LIGHT (bright fluorescent ambient) so far surfaces wash out
# to cream rather than black – key visual difference from Doom.

_C_FOG        = (224, 219, 210)   # bright corridor ambient (fluorescent haze)

# Drywall – above/below chair rail × lit/shadow face
_C_WALL_UP_EW = (210, 204, 194)   # upper wall, east/west face
_C_WALL_UP_NS = (192, 186, 176)   # upper wall, north/south (shadowed)
_C_WALL_LO_EW = (194, 187, 176)   # lower wall, east/west
_C_WALL_LO_NS = (177, 171, 161)   # lower wall, north/south
_C_RAIL       = (152, 144, 132)   # chair-rail / skirting board

# Drop ceiling
_C_CEIL_TILE  = (214, 214, 208)   # ceiling tile surface
_C_CEIL_GRID  = (170, 170, 165)   # tile grid lines (suspended rail)
_C_CEIL_LIGHT = (248, 248, 244)   # fluorescent tube strip
_C_CEIL_HOR   = (228, 226, 218)   # ceiling near horizon

# Carpet floor
_C_FLOOR_HOR  = ( 76,  82,  96)   # carpet near horizon (dark perspective)
_C_FLOOR_BOT  = (104, 112, 128)   # carpet near player

# Office door components
_C_DOOR_METAL = (172, 166, 156)   # painted-metal door face (lit)
_C_DOOR_DARK  = (150, 144, 136)   # painted-metal door face (shadow)
_C_DOOR_FRAME = ( 82,  78,  72)   # frame / edge seal
_C_DOOR_WOOD  = (138,  98,  56)   # wood-veneer panel
_C_DOOR_WOOD_D= (115,  82,  47)   # wood-veneer shadow
_C_DOOR_GLASS = (186, 210, 218)   # frosted glass pane

# RFID badge reader glow  →  (base LED colour, bright LED colour)
_DOOR_BADGE: dict[int, tuple] = {
    _DOOR_B: ((22,  55, 185), ( 70, 130, 255)),
    _DOOR_R: ((185,  22,  22), (255,  75,  75)),
    _DOOR_Y: ((185, 158,  18), (255, 218,  55)),
}

# Keycard accent colours per security level
_KEY_ACCENT: dict[int, tuple] = {
    _ITEM_B: ( 30,  85, 225),
    _ITEM_R: (225,  30,  30),
    _ITEM_Y: (225, 200,  30),
}

# Furniture
_C_CHAIR_FABRIC  = ( 42,  54,  72)   # dark navy blue upholstery
_C_CHAIR_FABRIC2 = ( 54,  66,  86)   # seat cushion (slightly lighter)
_C_CHAIR_METAL   = (118, 120, 124)   # chrome column & armrests
_C_CHAIR_BASE    = ( 24,  24,  26)   # black plastic 5-star base / casters

_C_DESK_SURFACE  = (234, 231, 224)   # white-laminate desktop surface
_C_DESK_EDGE     = (198, 194, 186)   # desktop overhang edge (shadow lip)
_C_DESK_FASCIA   = (220, 218, 213)   # front panel face (lit)
_C_DESK_FASCIA_D = (188, 185, 180)   # front panel face (shadow / NS side)
_C_DESK_SEAM     = (172, 169, 164)   # horizontal panel-join seams

_C_MON_BEZEL     = ( 26,  26,  30)   # monitor outer plastic frame
_C_MON_SCREEN    = ( 10,  16,  32)   # screen (off / dark)
_C_MON_GLOW      = ( 42, 118, 210)   # screen blue content glow
_C_MON_STAND     = ( 52,  52,  56)   # monitor stand / neck
_C_KEYBOARD      = (196, 194, 188)   # keyboard deck (light grey)


# ── Colour helpers ────────────────────────────────────────────────────────────

def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _clamp(r: float, g: float, b: float) -> tuple:
    return (max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))))


def _fog(perp: float) -> float:
    """Gentle office corridor wash-out (power > 1 = stays clear up close)."""
    return min(1.0, (perp / _FOG_DIST) ** 1.15)


# ── Wall pixel – painted drywall with chair rail ──────────────────────────────

_RAIL_Y = 0.40    # chair rail sits 40 % down the wall
_RAIL_W = 0.026   # rail thickness as fraction of wall height

def _wall_pixel(side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Office drywall shader.
    - Above rail : lighter beige
    - Rail band  : medium-grey skirting / chair-rail moulding
    - Below rail : slightly warmer/darker beige
    Subtle vertical paint-roller streaks add micro-texture.
    """
    fog_t = _fog(perp)

    if abs(wy - _RAIL_Y) < _RAIL_W:
        return _lerp(_C_RAIL, _C_FOG, fog_t)

    if wy > _RAIL_Y + _RAIL_W:
        base = _C_WALL_LO_EW if side == 0 else _C_WALL_LO_NS
    else:
        base = _C_WALL_UP_EW if side == 0 else _C_WALL_UP_NS

    # Faint vertical roller-brush texture
    streak = (math.sin(wx * 131.7) * 0.55
            + math.sin(wx *  44.3) * 0.30
            + math.sin(wx *  11.1) * 0.15) * 0.016
    return _lerp(_clamp(base[0]*(1+streak), base[1]*(1+streak), base[2]*(1+streak)),
                 _C_FOG, fog_t)


# ── Door pixel – office security door ────────────────────────────────────────

_GLASS_SPLIT = 0.28    # frosted glass occupies top 28 % of door
_BADGE_X  = (0.74, 0.91)
_BADGE_Y  = (0.52, 0.74)

def _door_pixel(door_type: int, side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Office security door:
      Top 28 %   – frosted-glass vision panel
      Rail        – painted metal rail at glass/wood boundary
      Lower 72 % – wood-veneer panel with grain
      Right side  – RFID card-reader badge with coloured LED glow
    """
    fog_t = _fog(perp)

    # ── Outer frame ───────────────────────────────────────────────────────
    if wx < 0.035 or wx > 0.965 or wy < 0.018 or wy > 0.982:
        return _lerp(_C_DOOR_FRAME, _C_FOG, fog_t)

    # ── Horizontal rail (glass / wood divider) ───────────────────────────
    if abs(wy - _GLASS_SPLIT) < 0.018:
        return _lerp(_C_DOOR_FRAME, _C_FOG, fog_t)

    # ── RFID badge reader (recessed box on wood panel, right side) ────────
    bx1, bx2 = _BADGE_X
    by1, by2 = _BADGE_Y
    if bx1 < wx < bx2 and by1 < wy < by2:
        # Recessed housing border
        inner = 0.025
        if wx < bx1+inner or wx > bx2-inner or wy < by1+inner or wy > by2-inner:
            return _lerp((48, 46, 44), _C_FOG, fog_t)
        # LED glow (radial, centred on reader face)
        base_led, glow_led = _DOOR_BADGE[door_type]
        cx = (bx1 + bx2) / 2.0
        cy = (by1 + by2) / 2.0
        dx = (wx - cx) / 0.055
        dy = (wy - cy) / 0.072
        glow_t = max(0.0, 1.0 - math.sqrt(dx*dx + dy*dy))
        # Small circular indicator in top-centre of reader
        ind_x = abs(wx - cx) / 0.025
        ind_y = abs(wy - (by1 + 0.06)) / 0.025
        if math.sqrt(ind_x*ind_x + ind_y*ind_y) < 1.0:
            led = _lerp(base_led, glow_led, 0.9)
        else:
            led = _lerp(base_led, glow_led, glow_t * 0.55)
        # Reader body (dark plastic)
        reader_body = _lerp((55, 54, 52), led, glow_t * 0.35 + 0.1)
        return _lerp(reader_body, _C_FOG, fog_t)

    # ── Frosted glass panel ───────────────────────────────────────────────
    if wy < _GLASS_SPLIT:
        # Subtle random frost pattern
        frost = (math.sin(wx * 97.3) * math.cos(wy * 73.1)) * 0.06
        glass = _clamp(
            _C_DOOR_GLASS[0] * (1 + frost),
            _C_DOOR_GLASS[1] * (1 + frost),
            _C_DOOR_GLASS[2] * (1 + frost),
        )
        return _lerp(glass, _C_FOG, fog_t * 0.5)   # glass is less fogged

    # ── Wood-veneer panel ─────────────────────────────────────────────────
    # Horizontal wood grain lines
    grain_y  = (wy * 28.0) % 1.0
    grain    = 1.0 - max(0.0, 0.18 - abs(grain_y - 0.5) * 7.0) * 0.28
    # Subtle vertical figure / medullary rays
    ray      = math.sin(wx * 19.3 + wy * 3.7) * 0.04
    shade    = grain * (1.0 + ray)
    wood     = _C_DOOR_WOOD if side == 0 else _C_DOOR_WOOD_D
    return _lerp(_clamp(wood[0]*shade, wood[1]*shade, wood[2]*shade), _C_FOG, fog_t)


# ── Keycard sprite texture ────────────────────────────────────────────────────

def _build_keycard(accent: tuple, size: int = 72) -> list[list[tuple | None]]:
    """
    HID-style RFID access card.

    Layout
    ------
      Top 20 %   – company colour bar with card-level accent stripe
      Next 55 %  – white card body: left=photo box, right=name lines + logo
      Bottom 25 % – magnetic stripe + chip contact pads
    """
    tex: list[list[tuple | None]] = [[None] * size for _ in range(size)]

    pad = max(2, size // 22)
    x1, x2 = pad, size - pad - 1
    y1, y2 = pad, size - pad - 1
    W = x2 - x1 + 1

    accent_hi = tuple(min(255, int(c * 1.35)) for c in accent)
    accent_lo = tuple(max(0,   int(c * 0.55)) for c in accent)

    _WHITE  = (248, 247, 244)
    _LGRAY  = (210, 208, 204)
    _MGRAY  = (155, 152, 148)
    _DGRAY  = ( 72,  70,  68)
    _FRAME  = ( 30,  28,  26)
    _STRIPE = ( 20,  20,  20)   # magnetic stripe
    _CHIP   = (195, 170,  80)   # gold chip contacts

    bar_y2  = y1 + int((y2 - y1) * 0.20)
    body_y2 = y1 + int((y2 - y1) * 0.75)
    # Photo box: left 35 % of body
    photo_x2 = x1 + int(W * 0.38)

    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            fx = (x - x1) / max(1, W - 1)
            fy = (y - y1) / max(1, y2 - y1)

            # Card border
            if x == x1 or x == x2 or y == y1 or y == y2:
                tex[y][x] = _FRAME
                continue

            # ── Colour bar (top 20 %) ─────────────────────────────────
            if y <= bar_y2:
                # Gradient from accent_lo to accent along bar
                t = (y - y1) / max(1, bar_y2 - y1)
                tex[y][x] = _lerp(accent_hi, accent, t)
                # Thin white highlight stripe at top
                if y == y1 + 1:
                    tex[y][x] = _lerp(_WHITE, accent, 0.5)
                continue

            # ── Magnetic stripe (bottom 25 %) ─────────────────────────
            if y > body_y2:
                stripe_t = (y - body_y2) / max(1, y2 - body_y2)
                # Stripe band across mid section
                if 0.15 < stripe_t < 0.55:
                    tex[y][x] = _STRIPE
                elif 0.60 < stripe_t < 0.85 and 0.15 < fx < 0.55:
                    # Gold chip contact pads (2×3 grid)
                    cpx = int(fx * 10) % 2
                    cpy = int(stripe_t * 20) % 3
                    if cpx == 0 and cpy < 2:
                        tex[y][x] = _CHIP
                    else:
                        tex[y][x] = _LGRAY
                else:
                    tex[y][x] = _LGRAY
                continue

            # ── White card body ────────────────────────────────────────
            if x <= photo_x2:
                # Photo placeholder box
                inner = 3
                if (x == x1 + inner or x == photo_x2 or
                        y == bar_y2 + inner or y == body_y2 - inner):
                    tex[y][x] = _MGRAY         # photo frame
                else:
                    # Simple silhouette (head + shoulders gradient)
                    body_fy = (y - bar_y2) / max(1, body_y2 - bar_y2)
                    photo_fx = (x - x1 - inner) / max(1, photo_x2 - x1 - inner)
                    if body_fy < 0.40:
                        # Head circle
                        hx = photo_fx - 0.5
                        hy = body_fy - 0.20
                        if math.hypot(hx * 2, hy * 3.5) < 0.55:
                            tex[y][x] = _MGRAY
                        else:
                            tex[y][x] = _LGRAY
                    else:
                        # Shoulders wedge
                        w_edge = abs(photo_fx - 0.5) * 1.6
                        if w_edge < body_fy - 0.30:
                            tex[y][x] = _MGRAY
                        else:
                            tex[y][x] = _LGRAY
            else:
                # Name / info area (right side of card body)
                info_fy = (y - bar_y2) / max(1, body_y2 - bar_y2)
                info_fx = (x - photo_x2) / max(1, x2 - photo_x2)

                # 3 name/title text lines in upper half
                if info_fy < 0.55:
                    line_t = info_fy * 5.0
                    frac   = line_t % 1.0
                    lnum   = int(line_t)
                    # Line widths: full, medium, short
                    widths = [0.85, 0.60, 0.45]
                    lw = widths[min(lnum, 2)]
                    if frac < 0.48 and info_fx > 0.06 and info_fx < lw:
                        # First line uses accent colour (name)
                        tex[y][x] = accent_lo if lnum == 0 else _MGRAY
                    else:
                        tex[y][x] = _WHITE

                # Access level badge in lower right
                elif 0.62 < info_fy < 0.88 and 0.50 < info_fx < 0.92:
                    # Small coloured badge
                    bfx = (info_fx - 0.50) / 0.42
                    bfy = (info_fy - 0.62) / 0.26
                    if 0.05 < bfx < 0.95 and 0.1 < bfy < 0.9:
                        border = bfx < 0.10 or bfx > 0.90 or bfy < 0.15 or bfy > 0.85
                        tex[y][x] = accent_lo if border else accent
                    else:
                        tex[y][x] = _WHITE
                else:
                    tex[y][x] = _WHITE

    return tex


# ── Document sprite texture ───────────────────────────────────────────────────

def _build_doc_texture(size: int = 72) -> list[list[tuple | None]]:
    """
    A4 document: white paper with blue letterhead, grey text lines,
    and a slight dog-ear fold at the top-right corner.
    """
    tex: list[list[tuple | None]] = [[None] * size for _ in range(size)]

    pad = max(3, size // 18)
    x1, x2 = pad, size - pad - 1
    y1, y2 = pad, size - pad - 1
    W = x2 - x1 + 1
    H = y2 - y1 + 1

    _PAPER   = (252, 250, 244)
    _SHADOW  = (220, 216, 208)   # right/bottom edge shadow
    _BORDER  = (190, 186, 178)
    _HEADER  = ( 52,  98, 172)   # corporate blue header
    _HEAD_HI = ( 80, 130, 210)   # header highlight
    _LINE    = (158, 152, 143)   # text lines
    _LINE_S  = (185, 180, 172)   # shorter / lighter lines
    _FOLD_F  = (230, 226, 218)   # fold flap face
    _FOLD_S  = (200, 196, 188)   # fold flap shadow

    fold   = max(5, size // 10)  # dog-ear size in pixels
    head_h = int(H * 0.18)       # header height

    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            # ── Dog-ear fold (top-right corner) ──────────────────────────
            local_x = x - (x2 - fold)
            local_y = y1 + fold - y
            if local_x > 0 and local_y > 0:
                if local_x + local_y > fold:
                    continue   # transparent – corner cut away
                elif local_x + local_y == fold:
                    tex[y][x] = _FOLD_S   # fold crease
                    continue
                else:
                    tex[y][x] = _FOLD_F   # underside of folded corner
                    continue

            fy = (y - y1) / max(1, H - 1)
            fx = (x - x1) / max(1, W - 1)

            # ── Paper border & shadow ─────────────────────────────────────
            if x == x1 or y == y1:
                tex[y][x] = _BORDER
                continue
            if x == x2 or y == y2:
                tex[y][x] = _SHADOW
                continue

            # ── Blue letterhead ───────────────────────────────────────────
            if y < y1 + head_h:
                bar_t = (y - y1) / max(1, head_h)
                col   = _lerp(_HEAD_HI, _HEADER, bar_t)
                # Thin white company-name text strip in the middle of header
                if 0.25 < bar_t < 0.55 and 0.06 < fx < 0.55:
                    col = _lerp(col, (255, 255, 255), 0.55)
                # Logo box at right
                if 0.65 < fx < 0.92 and 0.20 < bar_t < 0.80:
                    col = _lerp(col, (255, 255, 255), 0.25)
                tex[y][x] = col
                continue

            # ── Thin rule below header ────────────────────────────────────
            if y == y1 + head_h:
                tex[y][x] = _lerp(_HEADER, _BORDER, 0.5)
                continue

            # ── Body text lines ───────────────────────────────────────────
            tex[y][x] = _PAPER
            body_fy = (y - (y1 + head_h + 1)) / max(1, H - head_h - 2)
            body_line = body_fy * 9.0     # ~9 lines of text
            frac      = body_line % 1.0

            # Line rendering: thin band at ~30 % of each line slot
            if 0.05 < frac < 0.38 and body_line < 8.5:
                lnum = int(body_line)
                # Every third line is a paragraph gap (blank)
                if lnum % 4 != 3:
                    # Last line of each paragraph is shorter
                    max_fx = 0.88 if lnum % 4 != 2 else 0.52
                    min_fx = 0.06
                    if min_fx < fx < max_fx:
                        tex[y][x] = _LINE if lnum % 2 == 0 else _LINE_S

    return tex


# ── Pre-build all sprite textures ─────────────────────────────────────────────

_TEX_SIZE = 72
_ITEM_TEXTURES: dict[int, list[list[tuple | None]]] = {
    _ITEM_B:   _build_keycard(_KEY_ACCENT[_ITEM_B],   _TEX_SIZE),
    _ITEM_R:   _build_keycard(_KEY_ACCENT[_ITEM_R],   _TEX_SIZE),
    _ITEM_Y:   _build_keycard(_KEY_ACCENT[_ITEM_Y],   _TEX_SIZE),
    _ITEM_DOC: _build_doc_texture(_TEX_SIZE),
}


# ── Chair pixel ──────────────────────────────────────────────────────────────

def _chair_pixel(side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Office task-chair – occupies the **bottom half** of the cell strip only.

    wy < 0.5  → wall is visible above the chair (delegate to _wall_pixel).
    wy ≥ 0.5  → chair anatomy, remapped so cwy = (wy−0.5)×2 ∈ [0, 1]:

      cwy 0.00–0.54  backrest  (dark navy fabric, button-grid dimples, crown)
      cwy 0.54–0.62  gap       (open air + metal armrests on sides)
      cwy 0.62–0.76  seat cushion  (slightly lighter fabric)
      cwy 0.76–0.87  pneumatic column  (chrome cylinder)
      cwy 0.87–1.00  5-star base / casters  (black plastic spokes)

    The chair occupies the centre 72 % of the cell width; transparent areas
    delegate to _wall_pixel so the correct wall texture shows through.
    """
    # ── Upper half: wall above the chair ─────────────────────────────────
    if wy < 0.5:
        return _wall_pixel(side, perp, wx, wy)

    fog_t = _fog(perp)

    # Remap lower half → [0, 1] for chair anatomy
    cwy = (wy - 0.5) * 2.0

    def _wall_bg():
        """Show wall texture at the original wy for transparent chair areas."""
        return _wall_pixel(side, perp, wx, wy)

    # Horizontal extents
    BACK_L, BACK_R = 0.14, 0.86
    SEAT_L, SEAT_R = 0.10, 0.90
    COL_L,  COL_R  = 0.44, 0.56
    ARM_L,  ARM_R  = 0.14, 0.26
    ARM_L2, ARM_R2 = 0.74, 0.86

    # ── Backrest ──────────────────────────────────────────────────────────
    if cwy < 0.54:
        if not (BACK_L < wx < BACK_R):
            return _wall_bg()
        # Rounded crown
        if cwy < 0.07:
            crown_w = 0.72 * (1.0 - (0.07 - cwy) * 8.0)
            if abs(wx - 0.50) > crown_w / 2:
                return _wall_bg()
        # Button-grid dimple pattern
        bx  = int(wx * 8) % 2
        by  = int(cwy * 11) % 2
        dim = (bx == 0 and by == 0)
        col = tuple(max(0, c - 14) for c in _C_CHAIR_FABRIC) if dim else _C_CHAIR_FABRIC
        return _lerp(col, _C_FOG, fog_t)

    # ── Gap / armrest zone ────────────────────────────────────────────────
    if cwy < 0.62:
        if (ARM_L < wx < ARM_R) or (ARM_L2 < wx < ARM_R2):
            return _lerp(_C_CHAIR_METAL, _C_FOG, fog_t)
        return _wall_bg()

    # ── Seat cushion ──────────────────────────────────────────────────────
    if cwy < 0.76:
        if not (SEAT_L < wx < SEAT_R):
            return _wall_bg()
        bx  = int(wx * 5) % 2
        by  = int((cwy - 0.62) * 18) % 2
        dim = (bx == 0 and by == 0)
        col = tuple(max(0, c - 10) for c in _C_CHAIR_FABRIC2) if dim else _C_CHAIR_FABRIC2
        return _lerp(col, _C_FOG, fog_t)

    # ── Pneumatic column ──────────────────────────────────────────────────
    if cwy < 0.87:
        if not (COL_L < wx < COL_R):
            return _wall_bg()
        hl    = max(0.0, 1.0 - (wx - COL_L) / (COL_R - COL_L) * 2.5)
        shade = 1.0 + hl * 0.30
        return _lerp(_clamp(_C_CHAIR_METAL[0]*shade,
                             _C_CHAIR_METAL[1]*shade,
                             _C_CHAIR_METAL[2]*shade), _C_FOG, fog_t)

    # ── 5-star base / casters ─────────────────────────────────────────────
    cx, cy   = 0.50, 1.02
    dx, dy   = wx - cx, cwy - cy
    angle    = math.atan2(dx, -dy) % (2 * math.pi)
    spoke_a  = angle % (2 * math.pi / 5)
    dist     = math.hypot(dx * 1.8, dy)
    if spoke_a < 0.22 and dist < 0.44:
        return _lerp(_C_CHAIR_BASE, _C_FOG, fog_t)
    return _wall_bg()


# ── Desk pixel ────────────────────────────────────────────────────────────────

# Vertical split points (wy, 0 = top of strip, 1 = bottom)
_DESK_SURF_Y  = 0.46   # top surface of the desk
_DESK_EDGE_Y  = 0.50   # underside of overhanging edge
_DESK_SEAM_Y1 = 0.66   # first horizontal panel seam on fascia
_DESK_SEAM_Y2 = 0.84   # second horizontal panel seam

# Monitor geometry (only for _DESK_C)
_MON_L, _MON_R = 0.16, 0.74    # monitor left / right (wx)
_MON_T, _MON_B = 0.04, 0.40    # monitor top / bottom (wy)
_BEZEL          = 0.022         # bezel thickness fraction
_STAND_L        = 0.42
_STAND_R        = 0.54

def _desk_pixel(desk_type: int, side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Office desk viewed from the front as a solid cell.

    Vertical anatomy
    ────────────────
    DC (desk + computer):
      Monitor region (wy 0.04–0.40):
        Thin dark bezel → screen content (blue-gradient spreadsheet glow)
      Monitor stand (wy 0.40–0.46, centre strip)
      Keyboard strip  (wy 0.40–0.46, left of stand)
      Desktop surface (wy 0.46–0.50) – white laminate
      Desk fascia     (wy 0.50–1.00) – panel with two horizontal seams

    DE (empty desk):
      Wall above desk (wy 0.00–0.46) – shows drywall + chair rail
      Desktop surface (wy 0.46–0.50)
      Desk fascia     (wy 0.50–1.00)
    """
    fog_t = _fog(perp)

    # ── Above desk surface ────────────────────────────────────────────────
    if wy < _DESK_SURF_Y:
        if desk_type == _DESK_C:
            # Monitor bezel
            in_mon = _MON_L < wx < _MON_R and _MON_T < wy < _MON_B
            if in_mon:
                on_bezel = (wx < _MON_L + _BEZEL or wx > _MON_R - _BEZEL or
                            wy < _MON_T + _BEZEL or wy > _MON_B - _BEZEL)
                if on_bezel:
                    return _lerp(_C_MON_BEZEL, _C_FOG, fog_t)
                # Screen content – blue spreadsheet gradient
                sfx = (wx - _MON_L - _BEZEL) / (_MON_R - _MON_L - 2*_BEZEL)
                sfy = (wy - _MON_T - _BEZEL) / (_MON_B - _MON_T - 2*_BEZEL)
                # Faint horizontal scan-line bands
                scanline = 0.92 if int(sfy * 28) % 2 == 0 else 1.0
                # Column-rule grid lines (spreadsheet)
                col_rule = int(sfx * 7) % 7 == 0
                row_rule = int(sfy * 12) % 12 == 0
                if col_rule or row_rule:
                    screen = _lerp(_C_MON_GLOW, (220, 235, 255), 0.55)
                else:
                    screen = _lerp(_C_MON_SCREEN, _C_MON_GLOW, sfy * 0.55 * scanline)
                return _lerp(screen, _C_FOG, fog_t)

            # Monitor stand (centre) and keyboard (left of stand)
            if _MON_T < wy < _DESK_SURF_Y:
                if _STAND_L < wx < _STAND_R and wy > _MON_B:
                    return _lerp(_C_MON_STAND, _C_FOG, fog_t)
                if wx < _STAND_L and wy > _MON_B + 0.01:
                    # Keyboard – faint key grid
                    kfx = (wx - _MON_L) / (_STAND_L - _MON_L)
                    kfy = (wy - _MON_B - 0.01) / (_DESK_SURF_Y - _MON_B - 0.01)
                    key = (int(kfx * 12) % 3 == 0 or int(kfy * 4) % 2 == 0)
                    base = tuple(max(0, c - 14) for c in _C_KEYBOARD) if key else _C_KEYBOARD
                    return _lerp(base, _C_FOG, fog_t)

        # Anything above desk not covered by monitor/keyboard → show wall
        wall_wy = wy / _DESK_SURF_Y   # rescale so wall shader fills this band
        return _wall_pixel(side, perp, wx, wall_wy)

    # ── Desktop surface ───────────────────────────────────────────────────
    if wy < _DESK_EDGE_Y:
        if wy < _DESK_SURF_Y + 0.008:
            return _lerp(_C_DESK_EDGE, _C_FOG, fog_t)       # edge lip shadow
        return _lerp(_C_DESK_SURFACE, _C_FOG, fog_t)

    # ── Desk fascia (front panel) ─────────────────────────────────────────
    fascia = _C_DESK_FASCIA if side == 0 else _C_DESK_FASCIA_D

    # Cable-management grommet on right side
    if 0.78 < wx < 0.90 and 0.52 < wy < 0.62:
        if math.hypot((wx - 0.84) * 6, (wy - 0.57) * 10) < 1.0:
            return _lerp(_C_DESK_SEAM, _C_FOG, fog_t)

    # Horizontal panel seams
    if abs(wy - _DESK_SEAM_Y1) < 0.012 or abs(wy - _DESK_SEAM_Y2) < 0.012:
        return _lerp(_C_DESK_SEAM, _C_FOG, fog_t)

    # Subtle vertical panel-edge shadow on far left/right
    if wx < 0.04 or wx > 0.96:
        return _lerp(_C_DESK_SEAM, _C_FOG, fog_t)

    return _lerp(fascia, _C_FOG, fog_t)


# ── Window-wall colours ──────────────────────────────────────────────────────

_C_WIN_FRAME  = (238, 236, 230)   # white-painted PVC / aluminium frame
_C_WIN_SILL   = (226, 223, 216)   # window sill (slightly darker, protruding)
_C_WIN_BLIND  = (198, 200, 196)   # venetian blind slat (pearl white)
_C_SKY_TOP    = (178, 208, 232)   # overcast sky near top of pane
_C_SKY_BOT    = (148, 178, 208)   # overcast sky near bottom (deeper blue-grey)
_C_OUTSIDE    = (120, 148, 168)   # distant ground / horizon below sky

# Window geometry constants (wx / wy fractions of the wall strip)
_WIN_L    = 0.12   # left edge of window
_WIN_R    = 0.88   # right edge
_WIN_T    = 0.18   # top edge
_WIN_B    = 0.62   # bottom edge
_WIN_FW   = 0.022  # frame thickness
_WIN_SILL_H = 0.04 # sill height below window
_WIN_MID_X  = 0.50 # centre vertical muntin
_WIN_MID_Y  = (_WIN_T + _WIN_B) / 2   # centre horizontal muntin


def _wall_window_pixel(side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Wall with a double-pane office window (venetian blinds half-open).

    Layout
    ──────
    Normal drywall outside the window opening.

    Window opening  (wx 0.12–0.88, wy 0.18–0.62):
      White PVC frame (outer border + centre cross muntin)
      Glass panes – overcast sky gradient split by horizontal venetian blinds
        odd  slots  → blind slat  (pearl-white, slightly angled shade)
        even slots  → gap between slats → sky colour visible

    Window sill     (wx 0.10–0.90, wy 0.62–0.66): slightly protruding ledge
    """
    fog_t = _fog(perp)

    sill_t = _WIN_B
    sill_b = _WIN_B + _WIN_SILL_H

    in_opening = _WIN_L < wx < _WIN_R and _WIN_T < wy < _WIN_B
    on_sill    = _WIN_L - 0.02 < wx < _WIN_R + 0.02 and sill_t <= wy < sill_b

    # ── Window sill ───────────────────────────────────────────────────────
    if on_sill:
        return _lerp(_C_WIN_SILL, _C_FOG, fog_t)

    # ── Frame and muntins ─────────────────────────────────────────────────
    if in_opening:
        on_frame  = (wx < _WIN_L + _WIN_FW or wx > _WIN_R - _WIN_FW or
                     wy < _WIN_T + _WIN_FW or wy > _WIN_B - _WIN_FW)
        on_muntin = (abs(wx - _WIN_MID_X) < _WIN_FW * 0.8 or
                     abs(wy - _WIN_MID_Y) < _WIN_FW * 0.8)
        if on_frame or on_muntin:
            return _lerp(_C_WIN_FRAME, _C_FOG, fog_t)

        # ── Glass / venetian blinds ───────────────────────────────────────
        # Fractional position within the glass area
        glass_t = (wy - _WIN_T - _WIN_FW) / (_WIN_B - _WIN_T - 2 * _WIN_FW)

        # Sky gradient (top pane brighter; split at mid muntin)
        sky_col = _lerp(_C_SKY_TOP, _C_SKY_BOT, glass_t)

        # 12 blind slots across the full pane height
        slot     = (glass_t * 12) % 1.0
        on_slat  = slot < 0.42                 # 42 % of each slot is slat

        if on_slat:
            # Slat with a subtle tilt-shade: top edge lighter, bottom darker
            tilt = slot / 0.42                 # 0 = top of slat, 1 = bottom
            shade = _lerp(_C_WIN_FRAME, _C_WIN_BLIND, tilt * 0.5)
            # Fog applies less strongly to the bright window area
            return _lerp(shade, _C_FOG, fog_t * 0.45)
        else:
            # Gap between slats – see the sky (or ground near bottom)
            if glass_t > 0.82:
                sky_col = _lerp(_C_SKY_BOT, _C_OUTSIDE, (glass_t - 0.82) / 0.18)
            return _lerp(sky_col, _C_FOG, fog_t * 0.30)   # windows stay bright

    # ── Drywall outside the opening ───────────────────────────────────────
    return _wall_pixel(side, perp, wx, wy)


# ── Grid parser ───────────────────────────────────────────────────────────────

_SOLID_TOKENS: dict[str, int] = {
    "W":  _WALL,
    "BD": _DOOR_B, "RD": _DOOR_R, "YD": _DOOR_Y,
    "HD": _DOOR_H,   # hidden door – looks like a wall
    "C":  _CHAIR,    # office chair
    "DC": _DESK_C,   # desk with computer
    "DE": _DESK_E,   # empty desk
    "WW": _WALL_W,   # wall with window
    "D":  _DOOR_B,   # legacy
}
_ITEM_TOKENS: dict[str, int] = {
    "BK": _ITEM_B, "RK": _ITEM_R, "YK": _ITEM_Y,
    "K":  _ITEM_B,   # legacy
    "DOC": _ITEM_DOC,
}


def parse_grid(text: str) -> tuple[
    list[list[int]],
    list[tuple[float, float, int]],
    float,
    float,
]:
    """
    Parse a semicolon-separated grid string.

    Returns (solid, items, px, py).
    solid – 2-D int array (_EMPTY / _WALL / _DOOR_*)
    items – list of (world_x, world_y, item_type)
    px/py – player world position
    """
    solid: list[list[int]] = []
    items: list[tuple[float, float, int]] = []
    px: float | None = None
    py: float | None = None

    for row_idx, line in enumerate(text.strip().split(";")):
        tokens = [c.strip().upper() for c in line.strip().split(",") if c.strip()]
        if not tokens:
            continue
        row: list[int] = []
        for col_idx, tok in enumerate(tokens):
            if tok in _SOLID_TOKENS:
                row.append(_SOLID_TOKENS[tok])
            elif tok in _ITEM_TOKENS:
                row.append(_EMPTY)
                items.append((col_idx + 0.5, row_idx + 0.5, _ITEM_TOKENS[tok]))
            elif tok == "P":
                row.append(_EMPTY)
                px = col_idx + 0.5
                py = row_idx + 0.5
            else:
                row.append(_EMPTY)
        solid.append(row)

    if px is None:
        raise ValueError("Grid contains no player cell 'P'.")
    return solid, items, px, py


def facing_to_angle(facing: str) -> float:
    key = facing.strip().lower()
    if key not in FACING_ANGLES:
        raise ValueError(
            f"Invalid facing '{facing}'. Must be one of: "
            + ", ".join(FACING_ANGLES)
        )
    return FACING_ANGLES[key]


# ── DDA ray caster ────────────────────────────────────────────────────────────

def _cast_ray(
    solid: list[list[int]],
    px: float, py: float,
    ray_angle: float,
    camera_angle: float,
) -> tuple[float, int, int, float]:
    """
    Cast one ray, return (perp_dist, side, cell_type, wall_x).
    Doors are placed at the midpoint of their cell (classic Wolf3D technique).
    """
    cos_a = math.cos(ray_angle)
    sin_a = math.sin(ray_angle)

    mx, my = int(px), int(py)
    rows   = len(solid)
    cols   = len(solid[0]) if rows else 0

    ddx = abs(1.0 / cos_a) if cos_a != 0.0 else 1e30
    ddy = abs(1.0 / sin_a) if sin_a != 0.0 else 1e30

    step_x = 1 if cos_a > 0.0 else -1
    step_y = 1 if sin_a > 0.0 else -1

    sdx = (mx + 1 - px) * ddx if cos_a > 0.0 else (px - mx) * ddx
    sdy = (my + 1 - py) * ddy if sin_a > 0.0 else (py - my) * ddy

    side = 0
    raw  = _FOG_DIST
    ct   = _WALL
    wx   = 0.0

    for _ in range(512):
        if sdx < sdy:
            sdx += ddx; mx += step_x; side = 0
        else:
            sdy += ddy; my += step_y; side = 1

        if not (0 <= mx < cols and 0 <= my < rows):
            raw, ct = _FOG_DIST, _WALL
            break

        ct = solid[my][mx]

        if ct in _WALLS:   # _WALL and _DOOR_H both hit at cell boundary
            if side == 0:
                raw   = (mx - px + (1 - step_x) / 2.0) / cos_a
                hit_y = py + raw * sin_a
                wx    = hit_y - math.floor(hit_y)
                if cos_a > 0: wx = 1.0 - wx
            else:
                raw   = (my - py + (1 - step_y) / 2.0) / sin_a
                hit_x = px + raw * cos_a
                wx    = hit_x - math.floor(hit_x)
                if sin_a < 0: wx = 1.0 - wx
            break

        elif ct in _DOORS:
            if side == 0:
                door_d = (mx + 0.5 - px) / cos_a
                if door_d > 0:
                    y_hit = py + door_d * sin_a
                    if int(y_hit) == my:
                        raw = door_d
                        wx  = y_hit - math.floor(y_hit)
                        if cos_a > 0: wx = 1.0 - wx
                        break
            else:
                door_d = (my + 0.5 - py) / sin_a
                if door_d > 0:
                    x_hit = px + door_d * cos_a
                    if int(x_hit) == mx:
                        raw = door_d
                        wx  = x_hit - math.floor(x_hit)
                        if sin_a < 0: wx = 1.0 - wx
                        break
            ct = _EMPTY   # ray missed door plane – continue

    perp = max(0.01, raw * math.cos(ray_angle - camera_angle))
    return perp, side, ct, wx


# ── Sprite renderer ───────────────────────────────────────────────────────────

def _render_sprites(
    pixels,
    zbuf: list[float],
    items: list[tuple[float, float, int]],
    px: float, py: float,
    angle: float,
    W: int, H: int,
) -> None:
    """Project and rasterise all floor items (keycards + documents)."""
    if not items:
        return

    dir_x   = math.cos(angle)
    dir_y   = math.sin(angle)
    half    = math.tan(FOV / 2.0)
    pl_x    = -dir_y * half
    pl_y    =  dir_x * half
    inv_det = 1.0 / (pl_x * dir_y - dir_x * pl_y)
    ts      = _TEX_SIZE

    for sx, sy, stype in sorted(
        items, key=lambda s: -(s[0]-px)**2 - (s[1]-py)**2
    ):
        rx = sx - px
        ry = sy - py

        tx = inv_det * ( dir_y * rx - dir_x * ry)
        tz = inv_det * (-pl_y  * rx + pl_x  * ry)

        if tz <= 0.05:
            continue

        scr_cx   = int((W / 2.0) * (1.0 + tx / tz))
        world_h  = _ITEM_WORLD_H.get(stype, 0.45)
        sprite_h = max(1, int(H * world_h / tz))
        sprite_w = sprite_h

        floor_y   = H // 2 + int(H / (2.0 * tz))
        row_end   = min(H - 1, floor_y)
        row_start = max(0, floor_y - sprite_h)
        col_start = max(0, scr_cx - sprite_w // 2)
        col_end   = min(W - 1, scr_cx + sprite_w // 2)

        tex      = _ITEM_TEXTURES[stype]
        fog_dist = _fog(tz) * 0.75

        for col in range(col_start, col_end + 1):
            if tz >= zbuf[col]:
                continue

            tex_x = int((col - (scr_cx - sprite_w // 2)) * ts / max(1, sprite_w))
            tex_x = max(0, min(ts - 1, tex_x))

            for row in range(row_start, row_end + 1):
                tex_y = int((row - row_start) * ts / max(1, sprite_h))
                tex_y = max(0, min(ts - 1, tex_y))

                colour = tex[tex_y][tex_x]
                if colour is None:
                    continue

                pixels[col, row] = _lerp(colour, _C_FOG, fog_dist)


# ── Main renderer ─────────────────────────────────────────────────────────────

def render(
    solid: list[list[int]],
    items: list[tuple[float, float, int]],
    px: float, py: float,
    angle: float,
) -> Image.Image:
    """
    Raytrace a full frame and return a PIL RGB Image.

    Pass 1 – column DDA → ceiling / wall (or door) / floor per pixel.
    Pass 2 – sprite projection → keycards & documents, floor-anchored.
    """
    W, H     = SCREEN_W, SCREEN_H
    half_fov = FOV / 2.0

    img  = Image.new("RGB", (W, H))
    pix  = img.load()
    zbuf = [_FOG_DIST] * W

    for col in range(W):
        ray_angle          = angle - half_fov + FOV * col / W
        perp, side, ct, wx = _cast_ray(solid, px, py, ray_angle, angle)

        zbuf[col] = perp

        wall_h    = int(H / perp)
        wall_top  = max(0, H // 2 - wall_h // 2)
        wall_bot  = min(H - 1, H // 2 + wall_h // 2)
        wall_span = max(1, wall_bot - wall_top)

        is_door      = ct in _DOORS      # _DOOR_H excluded – rendered as wall
        is_furniture = ct in _FURNITURE   # chair / desk_c / desk_e

        for row in range(H):
            if row < wall_top:
                # ── Drop ceiling ──────────────────────────────────────────
                # Perspective t: 0 at top of screen, 1 at horizon
                t = (row / wall_top) if wall_top > 0 else 1.0

                # Approximate ceiling tile grid using screen-space coords.
                # Tiles appear denser near the horizon (foreshortening).
                scale    = max(1.0, wall_top / 12.0)
                g_col    = int(col / scale) % 34
                g_row    = int(row / scale) % 28
                is_grid  = g_col < 2 or g_row < 2

                # Fluorescent light bays (every ~80 px column band)
                bay_lit  = (col // 80) % 3 != 2

                if is_grid:
                    base = _C_CEIL_GRID
                elif bay_lit and g_col > 8 and g_col < 24:
                    base = _C_CEIL_LIGHT
                else:
                    base = _C_CEIL_TILE

                pix[col, row] = _lerp(base, _C_CEIL_HOR, t ** 0.6)

            elif row <= wall_bot:
                # ── Wall, door, or furniture ──────────────────────────────
                wy = (row - wall_top) / wall_span
                if is_door:
                    pix[col, row] = _door_pixel(ct, side, perp, wx, wy)
                elif is_furniture:
                    if ct == _CHAIR:
                        pix[col, row] = _chair_pixel(side, perp, wx, wy)
                    else:
                        pix[col, row] = _desk_pixel(ct, side, perp, wx, wy)
                elif ct == _WALL_W:
                    pix[col, row] = _wall_window_pixel(side, perp, wx, wy)
                else:
                    pix[col, row] = _wall_pixel(side, perp, wx, wy)

            else:
                # ── Carpet floor ──────────────────────────────────────────
                span = H - 1 - wall_bot
                t    = ((row - wall_bot) / span) ** 0.55 if span > 0 else 1.0

                # Diagonal weave pattern (subtle ±4 % brightness shift)
                weave = ((col + row) // 3) % 2
                shade = 1.04 if weave else 0.96

                base  = _lerp(_C_FLOOR_HOR, _C_FLOOR_BOT, t)
                pix[col, row] = _clamp(
                    base[0] * shade, base[1] * shade, base[2] * shade
                )

    _render_sprites(pix, zbuf, items, px, py, angle, W, H)
    return img
