"""
main.py – FastAPI service exposing the maze raycaster as a REST endpoint.

Endpoints
---------
POST /render
    Body  : JSON { "grid": "...", "facing": "up|down|left|right" }
    Return: 500×500 JPEG image

GET  /health
    Return: { "status": "ok" }

Interactive docs: /docs  (Swagger UI)
                  /redoc (ReDoc)

Grid format
-----------
Rows separated by ';', cells by ','.

    W,W,W,D,W,W,W;W,P,W,E,E,E,W;W,E,W,E,E,K,W;...

  W – wall         (solid stone)
  D – door         (solid at cell mid-plane, wood-coloured)
  E – empty space  (passable)
  P – player start (exactly one required)
  K – key on floor (passable; rendered as a gold sprite)

Example curl
------------
  curl -X POST https://<host>/render \\
       -H "Content-Type: application/json" \\
       -d '{
             "grid":   "W,W,W,D,W,W,W;W,P,W,E,E,E,W;W,E,W,E,E,K,W;W,E,W,W,W,E,W;W,W,W,W,W,W,W",
             "facing": "up"
           }' \\
       --output maze.jpg
"""

import io
import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from raycast import parse_grid, facing_to_angle, render, FACING_ANGLES

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("maze_api")

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Maze Raycast API",
    description=(
        "Render a first-person 3-D view of a text-defined maze using DDA "
        "raycasting.  Supports walls (W), doors (D), empty cells (E), a "
        "player start (P), and floor keys (K)."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── Request / response schemas ────────────────────────────────────────────────

_EXAMPLE_GRID = (
    "W,W,W,D,W,W,W;"
    "W,P,W,E,E,E,W;"
    "W,E,W,E,E,K,W;"
    "W,E,W,W,W,E,W;"
    "W,E,W,E,E,E,W;"
    "W,E,E,E,E,E,W;"
    "W,E,E,E,E,E,W;"
    "W,E,E,E,E,E,W;"
    "W,E,E,E,E,E,W;"
    "W,W,W,W,W,W,W"
)


class RenderRequest(BaseModel):
    grid: str = Field(
        ...,
        description=(
            "Maze grid.  Rows separated by ';', cells by ','.\n"
            "**W** wall · **D** door · **E** empty · **P** player (one) · **K** key"
        ),
        examples=[_EXAMPLE_GRID],
        min_length=3,
    )
    facing: str = Field(
        ...,
        description="Direction the player faces: `up` | `down` | `left` | `right`.",
        examples=["up"],
    )

    @field_validator("facing")
    @classmethod
    def _check_facing(cls, v: str) -> str:
        if v.strip().lower() not in FACING_ANGLES:
            valid = ", ".join(FACING_ANGLES)
            raise ValueError(f"Must be one of: {valid}.")
        return v.strip().lower()

    model_config = {
        "json_schema_extra": {
            "example": {"grid": _EXAMPLE_GRID, "facing": "up"}
        }
    }


# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    log.info("%s %s → %d  (%.0f ms)", request.method, request.url.path,
             response.status_code, ms)
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"], summary="Liveness probe")
def health():
    """Used by Render's health-check and load-balancer probes."""
    return {"status": "ok"}


@app.post(
    "/render",
    tags=["maze"],
    summary="Render a first-person maze view",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "500 × 500 JPEG image of the maze interior.",
        },
        400: {"description": "Malformed grid or invalid facing direction."},
        500: {"description": "Unexpected rendering error."},
    },
)
def render_maze(req: RenderRequest):
    """
    Parse the maze grid, raytrace a 500 × 500 first-person view from the
    player's position and direction, and return the result as a JPEG image.

    **Rendering pipeline**

    1. Parse the grid string into a solid map (walls/doors) and a sprite
       list (keys).
    2. For each screen column, cast a DDA ray to find the nearest wall or
       door, computing the perpendicular distance (fish-eye corrected).
    3. Draw ceiling/wall/floor strips per column; apply distance fog and
       face shading.
    4. Project key sprites onto the screen using a camera-plane transform;
       occlude them against the per-column depth buffer.
    5. Encode to JPEG and stream to the client.
    """
    # ── Parse grid ────────────────────────────────────────────────────────────
    try:
        solid, items, px, py = parse_grid(req.grid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid grid: {exc}") from exc

    if not solid:
        raise HTTPException(status_code=400, detail="Grid is empty.")

    grid_h = len(solid)
    grid_w = len(solid[0])

    # ── Validate player position is inside the grid ───────────────────────────
    if not (0 < px < grid_w and 0 < py < grid_h):
        raise HTTPException(
            status_code=400,
            detail=f"Player position ({px:.1f}, {py:.1f}) is outside the grid "
                   f"({grid_w}×{grid_h}).",
        )

    angle = facing_to_angle(req.facing)   # already validated by Pydantic

    log.info(
        "Render  grid=%dx%d  player=(%.1f,%.1f)  facing=%s  keys=%d",
        grid_w, grid_h, px, py, req.facing, len(items),
    )

    # ── Raytrace ──────────────────────────────────────────────────────────────
    try:
        image = render(solid, items, px, py, angle)
    except Exception as exc:
        log.exception("Render error")
        raise HTTPException(status_code=500, detail="Rendering failed.") from exc

    # ── Encode to JPEG and stream ─────────────────────────────────────────────
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92, optimize=True, subsampling=0)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/jpeg",
        headers={"Content-Disposition": 'inline; filename="maze.jpg"'},
    )
