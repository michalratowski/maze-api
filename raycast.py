"""
raycast.py – DDA raycasting engine with walls, doors, and floor-key sprites.

Coordinate system
-----------------
  X increases right  (grid column)
  Y increases down   (grid row)
  angle=0     → east  (+X)
  angle=π/2   → south (+Y)
  angle=−π/2  → north (−Y, "up" in grid terms)

Cell types parsed from the grid
--------------------------------
  W – wall     (solid, stone colour)
  D – door     (solid at cell mid-plane, wood colour with frame & handle)
  E – empty    (passable floor space)
  P – player   (passable; records starting position)
  K – key      (passable; rendered as a gold sprite resting on the floor)
"""

import math
from PIL import Image

# ── Rendering constants ───────────────────────────────────────────────────────

SCREEN_W  = 500
SCREEN_H  = 500
FOV       = math.pi / 3     # 60° horizontal field of view
_FOG_DIST = 16.0            # distance at which everything is fully fogged

# ── Cell-type sentinel values ─────────────────────────────────────────────────

_EMPTY = 0
_WALL  = 1
_DOOR  = 2

# ── Player facing → camera angle ─────────────────────────────────────────────

FACING_ANGLES: dict[str, float] = {
    "up":    -math.pi / 2,
    "down":   math.pi / 2,
    "left":   math.pi,
    "right":  0.0,
}

# ── Colour palette ────────────────────────────────────────────────────────────

_C_WALL_EW   = (210, 175, 120)   # stone, east/west face  (bright)
_C_WALL_NS   = (145, 115,  78)   # stone, north/south face (shadowed)
_C_DOOR_EW   = (155,  90,  38)   # wood, E/W face
_C_DOOR_NS   = (105,  60,  25)   # wood, N/S face
_C_FRAME     = ( 68,  42,  16)   # door frame / divider
_C_HANDLE    = (205, 168,  80)   # door knob
_C_FOG       = ( 18,  14,   9)   # deep ambient fog
_C_CEIL_TOP  = ( 10,  10,  22)   # ceiling far from horizon
_C_CEIL_HOR  = ( 38,  34,  68)   # ceiling near horizon
_C_FLOOR_HOR = ( 26,  17,   7)   # floor near horizon
_C_FLOOR_BOT = ( 78,  53,  23)   # floor near player


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


# ── Key sprite texture ────────────────────────────────────────────────────────

_KEY_TEX_SIZE = 64

def _build_key_texture(size: int = _KEY_TEX_SIZE) -> list[list[tuple | None]]:
    """
    Generate a key-shaped sprite texture.
    Returns a size×size grid; None means transparent.

    Layout (proportional):
      ┌───────────────┐
      │   ○ (ring)    │   top quarter
      │   │ (stem)    │   middle half
      │   ├─  (tooth) │   two teeth on right side
      │   └─  (tooth) │
      └───────────────┘
    """
    tex: list[list[tuple | None]] = [[None] * size for _ in range(size)]

    cx     = size // 2
    ring_y = size // 4
    r_out  = size // 6
    r_in   = max(2, size // 11)
    sw     = max(1, size // 14)     # half-stem width
    sy1    = ring_y + r_out         # stem top Y
    sy2    = int(size * 0.84)       # stem bottom Y
    tlen   = max(3, size // 9)      # tooth length in X
    th     = max(2, size // 14)     # tooth height in Y
    ty_a   = sy2 - int(size * 0.10) # first tooth top
    ty_b   = sy2 - int(size * 0.22) # second tooth top

    for y in range(size):
        for x in range(size):
            dx = x - cx
            dy = y - ring_y
            d  = math.hypot(dx, dy)

            in_ring_hollow = r_in <= d <= r_out
            in_ring_core   = d < r_in
            on_stem        = sy1 <= y <= sy2 and abs(x - cx) <= sw
            on_tooth       = (
                x > cx + sw and x <= cx + sw + tlen
                and ((ty_a <= y <= ty_a + th) or (ty_b <= y <= ty_b + th))
            )

            if in_ring_core:
                tex[y][x] = (160, 110, 0)           # dark gold hole
            elif in_ring_hollow or on_stem or on_tooth:
                # Left-edge highlight for a metallic look
                hl = max(0.0, 1.0 - (x - (cx - sw)) / max(1, 2 * sw + 2))
                r  = min(255, int(195 + 60 * hl))
                g  = min(255, int(148 + 52 * hl))
                b  = min(255, int(  0 + 28 * hl))
                tex[y][x] = (r, g, b)

    return tex


_KEY_TEXTURE: list[list[tuple | None]] = _build_key_texture()


# ── Grid parser ───────────────────────────────────────────────────────────────

def parse_grid(text: str) -> tuple[list[list[int]], list[tuple[float, float]], float, float]:
    """
    Parse a maze grid where rows are separated by ';' and cells by ','.

    Returns
    -------
    solid : list[list[int]]
        2-D grid of cell types (_EMPTY / _WALL / _DOOR).
    items : list[(wx, wy)]
        World-space centres of key pickups.
    px, py : float
        Player starting position (centre of the P cell).
    """
    solid: list[list[int]] = []
    items: list[tuple[float, float]] = []
    px: float | None = None
    py: float | None = None

    for row_idx, line in enumerate(text.strip().split(";")):
        line = line.strip()
        if not line:
            continue
        row: list[int] = []
        for col_idx, cell in enumerate(line.split(",")):
            token = cell.strip().upper()
            if token == "W":
                row.append(_WALL)
            elif token == "D":
                row.append(_DOOR)
            elif token == "P":
                row.append(_EMPTY)
                px = col_idx + 0.5
                py = row_idx + 0.5
            elif token == "K":
                row.append(_EMPTY)
                items.append((col_idx + 0.5, row_idx + 0.5))
            else:                         # E or unknown
                row.append(_EMPTY)
        solid.append(row)

    if px is None:
        raise ValueError("Grid contains no player cell 'P'.")

    return solid, items, px, py


def facing_to_angle(facing: str) -> float:
    """Translate 'up' / 'down' / 'left' / 'right' to a radian angle."""
    key = facing.strip().lower()
    if key not in FACING_ANGLES:
        valid = ", ".join(FACING_ANGLES)
        raise ValueError(f"Invalid facing '{facing}'. Must be one of: {valid}.")
    return FACING_ANGLES[key]


# ── DDA ray caster ────────────────────────────────────────────────────────────

def _cast_ray(
    solid: list[list[int]],
    px: float, py: float,
    ray_angle: float,
    camera_angle: float,
) -> tuple[float, int, int, float]:
    """
    Cast a single ray using the Digital Differential Analyzer algorithm.

    Returns
    -------
    perp  : float  perpendicular (fish-eye-corrected) distance to hit
    side  : int    0 = E/W face  (vertical grid line)
                   1 = N/S face  (horizontal grid line)
    ct    : int    _WALL or _DOOR
    wx    : float  fractional hit position along the wall [0, 1]
                   (used for door panel details)
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
            # Out of bounds – return fog distance
            raw = _FOG_DIST
            ct  = _WALL
            break

        ct = solid[my][mx]

        if ct == _WALL:
            # ── Regular wall hit ────────────────────────────────────────
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

        elif ct == _DOOR:
            # ── Door: thin wall at the MIDPOINT of the cell ─────────────
            # Project the ray to the cell's mid-plane and test containment.
            if side == 0:
                door_d = (mx + 0.5 - px) / cos_a
                if door_d > 0:
                    y_at = py + door_d * sin_a
                    if int(y_at) == my:         # mid-plane inside this cell
                        raw  = door_d
                        wx   = y_at - math.floor(y_at)
                        if cos_a > 0: wx = 1.0 - wx
                        break
            else:
                door_d = (my + 0.5 - py) / sin_a
                if door_d > 0:
                    x_at = px + door_d * cos_a
                    if int(x_at) == mx:
                        raw  = door_d
                        wx   = x_at - math.floor(x_at)
                        if sin_a < 0: wx = 1.0 - wx
                        break
            # Ray misses the door plane – treat cell as empty and continue
            ct = _EMPTY

    perp = max(0.01, raw * math.cos(ray_angle - camera_angle))
    return perp, side, ct, wx


# ── Surface colouring ─────────────────────────────────────────────────────────

def _wall_color(side: int, perp: float) -> tuple:
    base = _C_WALL_EW if side == 0 else _C_WALL_NS
    fog  = min(1.0, perp / _FOG_DIST)
    return _lerp(base, _C_FOG, fog ** 1.5)


def _door_color(side: int, perp: float, wx: float) -> tuple:
    """Colour a door pixel.  wx∈[0,1] drives frame, centre divide, and handle."""
    if wx < 0.045 or wx > 0.955:
        base = _C_FRAME                      # side frame
    elif 0.455 < wx < 0.545:
        base = _C_FRAME                      # centre vertical divide
    elif 0.60 < wx < 0.645:
        base = _C_HANDLE                     # door handle / knob
    else:
        base = _C_DOOR_EW if side == 0 else _C_DOOR_NS
    fog = min(1.0, perp / _FOG_DIST)
    return _lerp(base, _C_FOG, fog ** 1.5)


# ── Sprite renderer ───────────────────────────────────────────────────────────

def _render_sprites(
    pixels,
    zbuf: list[float],
    items: list[tuple[float, float]],
    px: float, py: float,
    angle: float,
    W: int, H: int,
) -> None:
    """
    Project and draw key sprites using the camera-plane transform.

    Sprites are drawn farthest-first (painter's algorithm) but are also
    occluded by the z-buffer so they disappear behind walls correctly.
    Each key is anchored at floor level for a convincing "lying on ground"
    appearance.
    """
    if not items:
        return

    dir_x = math.cos(angle)
    dir_y = math.sin(angle)
    half  = math.tan(FOV / 2.0)
    pl_x  = -dir_y * half
    pl_y  =  dir_x * half

    # Inverse determinant of [dir | plane] camera matrix
    inv_det = 1.0 / (pl_x * dir_y - dir_x * pl_y)

    ts = _KEY_TEX_SIZE

    # Farthest first so nearer sprites paint over far ones
    for sx, sy in sorted(items, key=lambda s: -(s[0]-px)**2-(s[1]-py)**2):
        rx = sx - px
        ry = sy - py

        # Transform sprite into camera space
        # tx: horizontal offset (negative = left, positive = right)
        # tz: depth (must be > 0 to be in front of camera)
        tx = inv_det * ( dir_y * rx - dir_x * ry)
        tz = inv_det * (-pl_y  * rx + pl_x  * ry)

        if tz <= 0.05:
            continue    # behind player or too close

        # Horizontal screen centre
        scr_cx = int((W / 2) * (1.0 + tx / tz))

        # Sprite represents a key roughly 0.45 units tall in world space.
        # Height on screen scales with inverse distance (same as walls).
        sprite_h = max(1, int(H * 0.45 / tz))
        sprite_w = sprite_h     # square sprite

        # Anchor bottom of sprite to the floor level at this distance
        wall_half = int(H / (2.0 * tz))
        floor_scr = H // 2 + wall_half     # y where floor begins at distance tz

        row_end   = min(H - 1, floor_scr)
        row_start = max(0, floor_scr - sprite_h)
        col_start = max(0, scr_cx - sprite_w // 2)
        col_end   = min(W - 1, scr_cx + sprite_w // 2)

        for col in range(col_start, col_end + 1):
            if tz >= zbuf[col]:
                continue    # wall is closer → sprite hidden in this column

            tex_x = int((col - (scr_cx - sprite_w // 2)) * ts / max(1, sprite_w))
            tex_x = max(0, min(ts - 1, tex_x))

            for row in range(row_start, row_end + 1):
                tex_y = int((row - row_start) * ts / max(1, sprite_h))
                tex_y = max(0, min(ts - 1, tex_y))

                colour = _KEY_TEXTURE[tex_y][tex_x]
                if colour is None:
                    continue    # transparent pixel

                # Apply distance fog to the sprite
                fog    = min(1.0, tz / _FOG_DIST)
                pixels[col, row] = _lerp(colour, _C_FOG, fog * 0.75)


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    solid: list[list[int]],
    items: list[tuple[float, float]],
    px: float, py: float,
    angle: float,
) -> Image.Image:
    """
    Raytrace a complete SCREEN_W × SCREEN_H frame and return a PIL Image.

    Pass 1 – column-by-column DDA: walls, doors, ceiling, floor.
    Pass 2 – sprite projection: key items anchored at floor level.
    """
    W, H     = SCREEN_W, SCREEN_H
    half_fov = FOV / 2.0

    img  = Image.new("RGB", (W, H))
    pix  = img.load()
    zbuf = [_FOG_DIST] * W      # depth buffer for sprite occlusion

    # ── Pass 1: walls & doors ────────────────────────────────────────────────
    for col in range(W):
        ray_angle          = angle - half_fov + FOV * col / W
        perp, side, ct, wx = _cast_ray(solid, px, py, ray_angle, angle)

        zbuf[col] = perp

        wall_h   = int(H / perp)
        wall_top = max(0, H // 2 - wall_h // 2)
        wall_bot = min(H - 1, H // 2 + wall_h // 2)

        wc = _door_color(side, perp, wx) if ct == _DOOR else _wall_color(side, perp)

        for row in range(H):
            if row < wall_top:
                # Ceiling: quadratic gradient (darker at top, lighter near horizon)
                t = (row / wall_top) ** 2 if wall_top > 0 else 0.0
                pix[col, row] = _lerp(_C_CEIL_TOP, _C_CEIL_HOR, t)
            elif row <= wall_bot:
                pix[col, row] = wc
            else:
                # Floor: square-root gradient (brightens toward player)
                span = H - 1 - wall_bot
                t    = ((row - wall_bot) / span) ** 0.5 if span > 0 else 1.0
                pix[col, row] = _lerp(_C_FLOOR_HOR, _C_FLOOR_BOT, t)

    # ── Pass 2: sprites ──────────────────────────────────────────────────────
    _render_sprites(pix, zbuf, items, px, py, angle, W, H)

    return img
