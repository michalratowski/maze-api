"""
raycast.py – DDA raycasting engine: Doom-1993-style aesthetics.

Grid tokens (semicolon-separated rows, comma-separated cells)
-------------------------------------------------------------
  W         – stone wall
  BD/RD/YD  – blue / red / yellow door  (solid; placed at cell mid-plane)
  E         – empty passable floor
  P         – player start (exactly one required)
  BK/RK/YK  – blue / red / yellow keycard sprite on the floor

Coordinate system
-----------------
  X right (+col), Y down (+row).
  angle=0 → east, −π/2 → north ("up").
"""

import math
from PIL import Image

# ── Screen & optics ──────────────────────────────────────────────────────────

SCREEN_W  = 500
SCREEN_H  = 500
FOV       = math.pi / 3       # 60° horizontal field of view
_FOG_DIST = 18.0              # distance at which surfaces reach full fog

# ── Cell-type constants ───────────────────────────────────────────────────────

_EMPTY  = 0
_WALL   = 1
_DOOR_B = 2    # blue  door
_DOOR_R = 3    # red   door
_DOOR_Y = 4    # yellow door

_ITEM_B = 10   # blue   key (sprite)
_ITEM_R = 11   # red    key (sprite)
_ITEM_Y = 12   # yellow key (sprite)

_DOORS = frozenset({_DOOR_B, _DOOR_R, _DOOR_Y})
_ITEMS = frozenset({_ITEM_B, _ITEM_R, _ITEM_Y})

# ── Player facing ─────────────────────────────────────────────────────────────

FACING_ANGLES: dict[str, float] = {
    "up":    -math.pi / 2,
    "down":   math.pi / 2,
    "left":   math.pi,
    "right":  0.0,
}

# ── Doom-style colour palette ─────────────────────────────────────────────────
# Inspired by the look of DOOM E1 (Knee-Deep in the Dead): warm gray-brown
# stone, near-black ceilings, dim brown floors.  Doors are dark metal slabs
# with a glowing colored lock panel.  Keys are brightly colored keycards.

# Stone walls
_C_WALL_EW    = (124,  98,  72)   # east/west face  – lit
_C_WALL_NS    = ( 86,  68,  50)   # north/south     – shadowed
# Doors – metallic body
_C_DOOR_BODY  = ( 80,  80,  80)   # door face (lit)
_C_DOOR_DARK  = ( 56,  56,  56)   # door face (shadow)
_C_DOOR_FRAME = ( 22,  22,  22)   # frame / riveted seam
_C_DOOR_BRASS = (200, 175,  64)   # handle / knob
# Door lock-panel glow  (base + bright)
_DOOR_COLORS = {
    _DOOR_B: ((28,  60, 200), ( 80, 130, 255)),
    _DOOR_R: ((190,  24,  24), (255,  80,  80)),
    _DOOR_Y: ((190, 165,  28), (255, 230,  90)),
}
# Key sprite colours
_KEY_COLORS = {
    _ITEM_B: ( 32,  88, 228),
    _ITEM_R: (228,  32,  32),
    _ITEM_Y: (228, 204,  32),
}
# Atmosphere
_C_FOG        = (  6,   4,   3)
_C_CEIL_TOP   = ( 10,   8,   6)
_C_CEIL_HOR   = ( 46,  36,  26)
_C_FLOOR_HOR  = ( 40,  32,  20)
_C_FLOOR_BOT  = ( 68,  52,  36)


# ── Colour helpers ────────────────────────────────────────────────────────────

def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _clamp_rgb(r: float, g: float, b: float) -> tuple:
    return (max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))))


def _fog(t: float) -> float:
    """Doom used a power-curve falloff – more dramatic than linear."""
    return min(1.0, (t / _FOG_DIST) ** 0.85)


# ── Stone-wall texture simulation ─────────────────────────────────────────────

def _stone_shade(wx: float, wy: float) -> float:
    """
    Staggered brick / mortar shade factor.
    Returns a multiplier ≈ [0.65 … 1.07] to apply to the base colour.

    wx, wy ∈ [0, 1]: fractional hit coordinates within the wall cell.
    """
    row_idx = int(wy * 6)
    # Alternate brick columns every other row (stagger)
    fx = (wx * 7.0 + (0.5 if row_idx & 1 else 0.0)) % 1.0
    fy = (wy * 6.0) % 1.0

    # Mortar darkening at fx ≈ 0 (vertical joint) and fy ≈ 0 (horizontal joint)
    mx = max(0.0, 1.0 - fx / 0.05) if fx < 0.05 else 0.0
    my = max(0.0, 1.0 - fy / 0.04) if fy < 0.04 else 0.0
    mortar = max(mx, my)

    # Subtle surface noise (sinusoidal, deterministic)
    noise = math.sin(wx * 83.7) * math.sin(wy * 57.1) * 0.05

    return (1.0 - mortar * 0.38) * (1.0 + noise)


def _wall_pixel(side: int, perp: float, wx: float, wy: float) -> tuple:
    base  = _C_WALL_EW if side == 0 else _C_WALL_NS
    shade = _stone_shade(wx, wy)
    fog_t = _fog(perp)
    r, g, b = base[0] * shade, base[1] * shade, base[2] * shade
    return _lerp(_clamp_rgb(r, g, b), _C_FOG, fog_t)


# ── Door colour ───────────────────────────────────────────────────────────────

def _door_pixel(door_type: int, side: int, perp: float, wx: float, wy: float) -> tuple:
    """
    Doom-style metal door with a rectangular glowing lock panel.

    Regions (wx=horizontal, wy=vertical, both 0→1):
      Outer frame        – dark metal seam
      Horizontal seams   – at 28% and 72% height
      Central lock panel – 28–72% height, 32–68% width; colour varies by type
      Door body          – dark metallic panels with subtle horizontal banding
      Handle/knob        – small brass oval near right of lock panel
    """
    fog_t = _fog(perp)

    # ── Outer frame / seam ────────────────────────────────────────────────
    if wx < 0.04 or wx > 0.96 or wy < 0.03 or wy > 0.97:
        return _lerp(_C_DOOR_FRAME, _C_FOG, fog_t)

    # ── Horizontal panel divider lines ────────────────────────────────────
    if abs(wy - 0.28) < 0.014 or abs(wy - 0.72) < 0.014:
        return _lerp(_C_DOOR_FRAME, _C_FOG, fog_t)

    # ── Vertical centre seam ──────────────────────────────────────────────
    if abs(wx - 0.50) < 0.012 and not (0.28 < wy < 0.72):
        return _lerp(_C_DOOR_FRAME, _C_FOG, fog_t)

    # ── Glowing colour lock panel ─────────────────────────────────────────
    if 0.28 < wy < 0.72 and 0.32 < wx < 0.68:
        base_col, glow_col = _DOOR_COLORS[door_type]
        # Radial glow centred on the panel
        dx = (wx - 0.50) / 0.18
        dy = (wy - 0.50) / 0.22
        glow_t = max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy))
        panel  = _lerp(base_col, glow_col, glow_t * 0.65)

        # Door handle: small brass accent to the right of centre
        if 0.54 < wx < 0.62 and 0.45 < wy < 0.55:
            panel = _C_DOOR_BRASS

        return _lerp(panel, _C_FOG, fog_t)

    # ── Door body: metallic panels with slight horizontal banding ─────────
    body = _C_DOOR_BODY if side == 0 else _C_DOOR_DARK
    # Faint panel-line darkening every ⅓ of each panel section
    panel_y = wy % 0.28
    band    = max(0.0, 1.0 - panel_y / 0.018) * 0.22 if panel_y < 0.018 else 0.0
    shade   = 1.0 - band
    r, g, b = body[0] * shade, body[1] * shade, body[2] * shade
    return _lerp(_clamp_rgb(r, g, b), _C_FOG, fog_t)


# ── Keycard sprite texture ────────────────────────────────────────────────────

def _build_keycard(key_color: tuple, size: int = 72) -> list[list[tuple | None]]:
    """
    Build a Doom-style rectangular keycard texture (size × size).

    Structure:
      Top 55%  – bright coloured panel with horizontal circuit-trace lines
      5%       – dark divider strip
      Bottom 40% – gray metallic contact area with a CPU chip block
    Transparent pixels (outside the card outline) are stored as None.
    """
    tex: list[list[tuple | None]] = [[None] * size for _ in range(size)]

    pad   = max(2, size // 20)
    x1, x2 = pad, size - pad - 1
    y_top   = pad
    y_bot   = size - pad - 1

    div_y1  = int(size * 0.55)
    div_y2  = int(size * 0.60)

    kc_hi = tuple(min(255, int(c * 1.38)) for c in key_color)
    kc_lo = tuple(max(0,   int(c * 0.55)) for c in key_color)

    _FRAME = (18, 18, 18)
    _CHIP  = (88, 88, 88)

    card_w = x2 - x1 + 1

    for y in range(y_top, y_bot + 1):
        for x in range(x1, x2 + 1):
            fx = (x - x1) / max(1, card_w - 1)   # 0→1 across card width
            iy = y - y_top
            top_span = div_y1 - y_top

            # ── Outer card border ─────────────────────────────────────
            if x == x1 or x == x2 or y == y_top or y == y_bot:
                tex[y][x] = _FRAME
                continue

            # ── Divider strip ─────────────────────────────────────────
            if div_y1 <= y <= div_y2:
                tex[y][x] = _FRAME
                continue

            # ── Top coloured panel ────────────────────────────────────
            if y < div_y1:
                fy = iy / max(1, top_span)
                # Inner edge shadow
                if x == x1 + 1 or x == x2 - 1 or y == y_top + 1:
                    tex[y][x] = kc_lo
                # Top-left corner shine
                elif (x - x1) < 5 and iy < 5:
                    tex[y][x] = kc_hi
                # Horizontal circuit-trace lines
                elif abs(fy - 0.32) < 0.04 and 0.14 < fx < 0.86:
                    tex[y][x] = kc_hi
                elif abs(fy - 0.62) < 0.04 and 0.14 < fx < 0.55:
                    tex[y][x] = kc_hi
                # Small connector dot
                elif abs(fy - 0.62) < 0.04 and 0.60 < fx < 0.72:
                    tex[y][x] = kc_hi
                else:
                    tex[y][x] = key_color

            # ── Bottom metallic contact panel ─────────────────────────
            else:
                bot_start = div_y2 + 1
                bot_span  = y_bot - bot_start
                fy2 = (y - bot_start) / max(1, bot_span)

                # CPU chip block in centre
                if 0.30 < fx < 0.70 and 0.22 < fy2 < 0.70:
                    # Chip body with subtle grid
                    on_grid = (
                        abs((fx * 8) % 1.0 - 0.5) < 0.06 or
                        abs((fy2 * 5) % 1.0 - 0.5) < 0.06
                    )
                    shade = 72 if not on_grid else 55
                    tex[y][x] = (shade, shade, shade)
                else:
                    # Gray metal gradient (slightly darker toward bottom)
                    g_val = int(70 - 18 * fy2)
                    tex[y][x] = (g_val, g_val, g_val)

    return tex


# Pre-build one texture per key colour at import time
_KEY_TEX_SIZE = 72
_KEY_TEXTURES: dict[int, list[list[tuple | None]]] = {
    _ITEM_B: _build_keycard(_KEY_COLORS[_ITEM_B], _KEY_TEX_SIZE),
    _ITEM_R: _build_keycard(_KEY_COLORS[_ITEM_R], _KEY_TEX_SIZE),
    _ITEM_Y: _build_keycard(_KEY_COLORS[_ITEM_Y], _KEY_TEX_SIZE),
}


# ── Grid parser ───────────────────────────────────────────────────────────────

_TOKEN_MAP: dict[str, int] = {
    "W":  _WALL,
    "BD": _DOOR_B, "RD": _DOOR_R, "YD": _DOOR_Y,
    # legacy single-char aliases kept for back-compat
    "D":  _DOOR_B,
}

_ITEM_TOKEN_MAP: dict[str, int] = {
    "BK": _ITEM_B, "RK": _ITEM_R, "YK": _ITEM_Y,
    # legacy
    "K":  _ITEM_B,
}


def parse_grid(text: str) -> tuple[
    list[list[int]],
    list[tuple[float, float, int]],
    float,
    float,
]:
    """
    Parse a maze grid string (rows separated by ';', cells by ',').

    Returns
    -------
    solid : list[list[int]]
        Per-cell solid type (_EMPTY / _WALL / _DOOR_*).
    items : list[(wx, wy, item_type)]
        World-space centre and type for every sprite pickup.
    px, py : float
        Player starting position (centre of the P cell).
    """
    solid: list[list[int]] = []
    items: list[tuple[float, float, int]] = []
    px: float | None = None
    py: float | None = None

    for row_idx, line in enumerate(text.strip().split(";")):
        row_cells = [c.strip().upper() for c in line.strip().split(",") if c.strip()]
        if not row_cells:
            continue
        row: list[int] = []
        for col_idx, token in enumerate(row_cells):
            if token in _TOKEN_MAP:
                row.append(_TOKEN_MAP[token])
            elif token in _ITEM_TOKEN_MAP:
                row.append(_EMPTY)
                items.append((col_idx + 0.5, row_idx + 0.5, _ITEM_TOKEN_MAP[token]))
            elif token == "P":
                row.append(_EMPTY)
                px = col_idx + 0.5
                py = row_idx + 0.5
            else:
                row.append(_EMPTY)   # E or unknown
        solid.append(row)

    if px is None:
        raise ValueError("Grid contains no player cell 'P'.")

    return solid, items, px, py


def facing_to_angle(facing: str) -> float:
    """Translate 'up' / 'down' / 'left' / 'right' to a radian camera angle."""
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
    Digital Differential Analyzer (DDA) ray cast.

    Returns (perp_dist, side, cell_type, wall_x)
    -----------------------------------------------
    perp_dist – fish-eye-corrected perpendicular distance
    side      – 0 = E/W face  (vertical grid line crossed)
                1 = N/S face  (horizontal grid line crossed)
    cell_type – _WALL or one of _DOOR_*
    wall_x    – fractional hit position along the hit surface [0, 1]
                (used to look up horizontal texture coordinate)
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
            sdx += ddx
            mx  += step_x
            side = 0
        else:
            sdy += ddy
            my  += step_y
            side = 1

        if not (0 <= mx < cols and 0 <= my < rows):
            raw, ct = _FOG_DIST, _WALL
            break

        ct = solid[my][mx]

        if ct == _WALL:
            # ── Regular solid wall ──────────────────────────────────────
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
            # ── Door: thin plane at cell mid-point ─────────────────────
            # Project ray to the centre of the door cell and test whether
            # the intersection still falls inside that cell.
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
            # Ray missed the door plane – treat as empty and keep walking
            ct = _EMPTY

    perp = max(0.01, raw * math.cos(ray_angle - camera_angle))
    return perp, side, ct, wx


# ── Sprite (key) renderer ────────────────────────────────────────────────────

def _render_sprites(
    pixels,
    zbuf: list[float],
    items: list[tuple[float, float, int]],
    px: float, py: float,
    angle: float,
    W: int, H: int,
) -> None:
    """
    Project and rasterise key sprite pickups onto the already-rendered frame.

    Uses:
     • Camera-plane transform for correct perspective projection.
     • Z-buffer for per-column wall occlusion.
     • Painter's algorithm (farthest drawn first) for sprite-on-sprite.
     • Bottom edge anchored to the floor horizon so the key appears to rest
       on the ground rather than floating.
    """
    if not items:
        return

    dir_x  = math.cos(angle)
    dir_y  = math.sin(angle)
    half   = math.tan(FOV / 2.0)
    # Camera plane vector (perpendicular to direction)
    pl_x   = -dir_y * half
    pl_y   =  dir_x * half
    # Determinant of [dir | plane]  (always non-zero for valid FOV)
    inv_det = 1.0 / (pl_x * dir_y - dir_x * pl_y)

    ts = _KEY_TEX_SIZE

    # Painter's algorithm: farthest item first
    for sx, sy, stype in sorted(
        items,
        key=lambda s: -(s[0] - px) ** 2 - (s[1] - py) ** 2,
    ):
        rel_x = sx - px
        rel_y = sy - py

        # Transform to camera space
        # tx: left/right screen offset   tz: depth (must be > 0)
        tx = inv_det * ( dir_y * rel_x - dir_x * rel_y)
        tz = inv_det * (-pl_y  * rel_x + pl_x  * rel_y)

        if tz <= 0.05:
            continue    # behind player

        # Horizontal screen centre
        scr_cx = int((W / 2.0) * (1.0 + tx / tz))

        # Sprite world-height ≈ 0.45 units → projected screen height
        sprite_h = max(1, int(H * 0.45 / tz))
        sprite_w = sprite_h

        # Anchor bottom of sprite to the floor horizon at depth tz
        floor_y  = H // 2 + int(H / (2.0 * tz))

        row_end   = min(H - 1, floor_y)
        row_start = max(0, floor_y - sprite_h)
        col_start = max(0, scr_cx - sprite_w // 2)
        col_end   = min(W - 1, scr_cx + sprite_w // 2)

        tex = _KEY_TEXTURES[stype]

        for col in range(col_start, col_end + 1):
            if tz >= zbuf[col]:
                continue    # wall is closer in this column

            tex_x = int((col - (scr_cx - sprite_w // 2)) * ts / max(1, sprite_w))
            tex_x = max(0, min(ts - 1, tex_x))

            for row in range(row_start, row_end + 1):
                tex_y = int((row - row_start) * ts / max(1, sprite_h))
                tex_y = max(0, min(ts - 1, tex_y))

                colour = tex[tex_y][tex_x]
                if colour is None:
                    continue    # transparent

                fog_t = _fog(tz) * 0.8
                pixels[col, row] = _lerp(colour, _C_FOG, fog_t)


# ── Main renderer ─────────────────────────────────────────────────────────────

def render(
    solid: list[list[int]],
    items: list[tuple[float, float, int]],
    px: float, py: float,
    angle: float,
) -> Image.Image:
    """
    Full-frame raytrace.  Returns a SCREEN_W × SCREEN_H PIL Image (RGB).

    Pass 1 – Column DDA: ceiling / wall (or door) / floor, per pixel.
    Pass 2 – Sprite projection: key pickups, floor-anchored, z-buffered.
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

        wall_h   = int(H / perp)
        wall_top = max(0, H // 2 - wall_h // 2)
        wall_bot = min(H - 1, H // 2 + wall_h // 2)
        wall_span = max(1, wall_bot - wall_top)

        is_door = ct in _DOORS

        # Pre-compute fog factor (same for all rows in this column)
        fog_t = _fog(perp)

        for row in range(H):
            if row < wall_top:
                # ── Ceiling ────────────────────────────────────────────
                # Quadratic gradient: near-black at top, dim brown near horizon
                t = (row / wall_top) ** 2 if wall_top > 0 else 0.0
                pix[col, row] = _lerp(_C_CEIL_TOP, _C_CEIL_HOR, t)

            elif row <= wall_bot:
                # ── Wall or door ────────────────────────────────────────
                wy = (row - wall_top) / wall_span  # vertical tex coord [0,1]
                if is_door:
                    pix[col, row] = _door_pixel(ct, side, perp, wx, wy)
                else:
                    pix[col, row] = _wall_pixel(side, perp, wx, wy)

            else:
                # ── Floor ───────────────────────────────────────────────
                # Square-root gradient: darker at horizon, brighter near player
                span = H - 1 - wall_bot
                t    = ((row - wall_bot) / span) ** 0.5 if span > 0 else 1.0
                pix[col, row] = _lerp(_C_FLOOR_HOR, _C_FLOOR_BOT, t)

    _render_sprites(pix, zbuf, items, px, py, angle, W, H)
    return img
