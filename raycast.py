"""
raycast.py – DDA raycasting engine.

Coordinate system
-----------------
  X increases to the right  (column index)
  Y increases downward      (row index)
  angle = 0      → facing right  (+X)
  angle = π/2    → facing down   (+Y)
  angle = π      → facing left   (−X)
  angle = −π/2   → facing up     (−Y / toward row 0)
"""

import math
from PIL import Image

# ── Constants ────────────────────────────────────────────────────────────────

SCREEN_W = 500
SCREEN_H = 500
FOV      = math.pi / 3        # 60° horizontal field of view

FACING_ANGLES: dict[str, float] = {
    "up":    -math.pi / 2,
    "down":   math.pi / 2,
    "left":   math.pi,
    "right":  0.0,
}

# Wall colour (R, G, B) per face type – tweaked for a stone-dungeon feel
_WALL_BASE  = (210, 175, 120)   # E/W face (bright)
_WALL_DARK  = (145, 115,  78)   # N/S face (shadowed)

_CEIL_TOP   = (12,  12,  25)    # ceiling at top of screen
_CEIL_HOR   = (40,  35,  70)    # ceiling near horizon

_FLOOR_HOR  = (28,  18,   8)    # floor near horizon
_FLOOR_BOT  = (80,  55,  25)    # floor at bottom of screen

_FOG_DIST   = 14.0              # distance at which walls are fully fogged
_FOG_COLOR  = (18,  14,   9)    # ambient fog colour


# ── Grid parser ──────────────────────────────────────────────────────────────

def parse_grid(text: str) -> tuple[list[list[int]], float, float]:
    """
    Parse a grid string where rows are separated by semicolons and cells by
    commas.  Returns (maze, px, py).

    maze  – list[list[int]]   1 = wall, 0 = open
    px/py – float player position (centre of the P cell)
    """
    maze: list[list[int]] = []
    px: float | None = None
    py: float | None = None

    for row_idx, line in enumerate(text.strip().split(";")):
        line = line.strip()
        if not line:
            continue
        cells = [c.strip().upper() for c in line.split(",")]
        row: list[int] = []
        for col_idx, cell in enumerate(cells):
            if cell == "W":
                row.append(1)
            elif cell == "P":
                row.append(0)
                px = col_idx + 0.5
                py = row_idx + 0.5
            else:
                row.append(0)
        maze.append(row)

    if px is None:
        raise ValueError("Grid contains no player cell 'P'.")

    return maze, px, py


def facing_to_angle(facing: str) -> float:
    """Convert 'up' / 'down' / 'left' / 'right' to a camera angle in radians."""
    key = facing.strip().lower()
    if key not in FACING_ANGLES:
        valid = ", ".join(FACING_ANGLES)
        raise ValueError(f"Invalid facing '{facing}'. Must be one of: {valid}.")
    return FACING_ANGLES[key]


# ── DDA ray caster ───────────────────────────────────────────────────────────

def _lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _cast_ray(
    maze: list[list[int]],
    px: float, py: float,
    ray_angle: float,
    camera_angle: float,
) -> tuple[float, int]:
    """
    Return (perp_dist, side) for the nearest wall hit.
    side 0 = E/W face  (vertical grid line crossed)
    side 1 = N/S face  (horizontal grid line crossed)
    """
    cos_a = math.cos(ray_angle)
    sin_a = math.sin(ray_angle)

    mx, my = int(px), int(py)
    rows   = len(maze)
    cols   = len(maze[0]) if rows else 0

    ddx = abs(1.0 / cos_a) if cos_a != 0.0 else 1e30
    ddy = abs(1.0 / sin_a) if sin_a != 0.0 else 1e30

    step_x = 1 if cos_a > 0.0 else -1
    step_y = 1 if sin_a > 0.0 else -1

    sdx = (mx + 1 - px) * ddx if cos_a > 0.0 else (px - mx) * ddx
    sdy = (my + 1 - py) * ddy if sin_a > 0.0 else (py - my) * ddy

    side = 0
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
            return _FOG_DIST, side          # out-of-bounds → fog distance
        if maze[my][mx]:
            break

    raw = (
        (mx - px + (1 - step_x) / 2.0) / cos_a if side == 0
        else (my - py + (1 - step_y) / 2.0) / sin_a
    )

    # Fish-eye correction: perpendicular distance removes barrel distortion
    perp = max(0.01, raw * math.cos(ray_angle - camera_angle))
    return perp, side


# ── Renderer ─────────────────────────────────────────────────────────────────

def render(
    maze: list[list[int]],
    px: float, py: float,
    angle: float,
) -> Image.Image:
    """
    Render a SCREEN_W × SCREEN_H first-person view and return a PIL Image
    in RGB mode.
    """
    w, h     = SCREEN_W, SCREEN_H
    half_fov = FOV / 2.0

    img     = Image.new("RGB", (w, h))
    pixels  = img.load()

    for col in range(w):
        ray_angle       = angle - half_fov + FOV * col / w
        perp, side      = _cast_ray(maze, px, py, ray_angle, angle)

        # ── Wall slice geometry ──────────────────────────────────────────
        wall_h   = int(h / perp)
        wall_top = max(0, h // 2 - wall_h // 2)
        wall_bot = min(h - 1, h // 2 + wall_h // 2)

        # ── Wall colour ─────────────────────────────────────────────────
        fog_t      = min(1.0, perp / _FOG_DIST)           # 0 = near, 1 = far
        wall_base  = _WALL_BASE if side == 0 else _WALL_DARK
        wall_color = _lerp_color(wall_base, _FOG_COLOR, fog_t ** 1.5)

        # ── Fill column ─────────────────────────────────────────────────
        for row in range(h):
            if row < wall_top:
                # Ceiling: interpolate from top colour to horizon colour
                t = (row / wall_top) ** 2 if wall_top > 0 else 0.0
                pixels[col, row] = _lerp_color(_CEIL_TOP, _CEIL_HOR, t)

            elif row <= wall_bot:
                pixels[col, row] = wall_color

            else:
                # Floor: interpolate from horizon colour to bottom colour
                span = h - 1 - wall_bot
                t    = ((row - wall_bot) / span) ** 0.5 if span > 0 else 1.0
                pixels[col, row] = _lerp_color(_FLOOR_HOR, _FLOOR_BOT, t)

    return img
