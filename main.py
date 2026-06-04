"""
main.py – FastAPI service: Doom-style 3-D maze raycaster.

Endpoints
---------
POST /render   → JSON { "image": "<base64-encoded JPEG>" }
GET  /health   → {"status": "ok"}
GET  /docs     → Swagger UI
GET  /redoc    → ReDoc UI

Grid format
-----------
Rows separated by ';', cells by ','.

  W         – stone wall
  BD/RD/YD  – blue / red / yellow door
  E         – empty passable floor
  P         – player start (exactly one required)
  BK/RK/YK  – blue / red / yellow keycard on the floor

Example curl
------------
  curl -X POST https://<host>/render \\
       -H "Content-Type: application/json" \\
       -d '{
         "grid":   "W,W,W,BD,W,W,W;W,P,W,E,E,E,W;W,E,W,E,E,BK,W;W,E,RD,W,W,E,W;W,E,W,E,E,RK,W;W,E,W,YD,W,E,W;W,E,E,E,E,YK,W;W,W,W,W,W,W,W",
         "facing": "right"
       }'
  # Response: { "image": "<base64 string>", "format": "jpeg", "encoding": "base64" }
"""

import base64
import io
import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
    title="Doom-style Maze Raycast API",
    description=(
        "Render a first-person 3-D maze view in the style of the original "
        "**DOOM (1993)** using DDA raycasting.\n\n"
        "Supported grid tokens: `W` wall · `BD`/`RD`/`YD` coloured doors · "
        "`E` empty · `P` player · `BK`/`RK`/`YK` coloured keycards."
    ),
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Example payload ───────────────────────────────────────────────────────────

_EXAMPLE_GRID = (
    "W,W,W,BD,W,W,W;"
    "W,P,W,E,E,E,W;"
    "W,E,W,E,E,BK,W;"
    "W,E,RD,W,W,E,W;"
    "W,E,W,E,E,RK,W;"
    "W,E,W,YD,W,E,W;"
    "W,E,E,E,E,YK,W;"
    "W,W,W,W,W,W,W"
)

# ── Request schema ────────────────────────────────────────────────────────────

class RenderRequest(BaseModel):
    grid: str = Field(
        ...,
        description=(
            "Maze grid. Rows separated by **;**, cells by **,**.\n\n"
            "| Token | Meaning |\n"
            "|-------|---------|\n"
            "| `W`   | Stone wall |\n"
            "| `BD`  | Blue door |\n"
            "| `RD`  | Red door |\n"
            "| `YD`  | Yellow door |\n"
            "| `E`   | Empty floor |\n"
            "| `P`   | Player start |\n"
            "| `BK`  | Blue keycard |\n"
            "| `RK`  | Red keycard |\n"
            "| `YK`  | Yellow keycard |\n"
        ),
        examples=[_EXAMPLE_GRID],
        min_length=3,
    )
    facing: str = Field(
        ...,
        description="Player's initial facing direction: `up` | `down` | `left` | `right`.",
        examples=["right"],
    )

    @field_validator("facing")
    @classmethod
    def _validate_facing(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in FACING_ANGLES:
            raise ValueError(
                f"Must be one of: {', '.join(FACING_ANGLES)}."
            )
        return key

    model_config = {
        "json_schema_extra": {
            "example": {"grid": _EXAMPLE_GRID, "facing": "right"}
        }
    }

# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1_000
    log.info(
        "%s %s → %d  (%.0f ms)",
        request.method, request.url.path, response.status_code, ms,
    )
    return response

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"], summary="Liveness probe")
def health():
    """Used by Render.com's health-check and load-balancer probes."""
    return {"status": "ok"}


@app.post(
    "/render",
    tags=["maze"],
    summary="Render a Doom-style first-person maze view",
    response_class=JSONResponse,
    responses={
        200: {
            "content": {"application/json": {}},
            "description": (
                "JSON object with a base64-encoded JPEG of the maze view.\n\n"
                "```json\n"
                '{ "image": "<base64>", "format": "jpeg", "encoding": "base64" }\n'
                "```"
            ),
        },
        400: {"description": "Malformed grid string or invalid facing direction."},
        500: {"description": "Unexpected rendering error."},
    },
)
def render_maze(req: RenderRequest):
    """
    Parse the maze grid and raytrace a 500 × 500 Doom-style first-person view.

    **Rendering pipeline**

    1. **Parse** – grid string → solid map (walls/doors) + sprite list (keys).
    2. **DDA raytrace** – for each screen column, cast a ray to find the nearest
       wall or door.  Perpendicular (fish-eye-corrected) distance determines
       wall-strip height.
    3. **Shading** – stone walls use a staggered brick shader (mortar lines +
       surface noise).  Coloured doors use a metallic shader with a glowing
       lock panel.  Distance fog and face darkening (N/S vs E/W) applied
       throughout.
    4. **Sprite projection** – keycards projected via camera-plane transform,
       floor-anchored, and occluded by the per-column depth buffer.
    5. **JPEG encode** – streamed to client.
    """
    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        solid, items, px, py = parse_grid(req.grid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid grid: {exc}") from exc

    if not solid:
        raise HTTPException(status_code=400, detail="Grid is empty.")

    grid_h = len(solid)
    grid_w = len(solid[0])

    if not (0 < px < grid_w and 0 < py < grid_h):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Player position ({px:.1f}, {py:.1f}) is outside "
                f"the grid bounds ({grid_w} × {grid_h})."
            ),
        )

    angle = facing_to_angle(req.facing)

    log.info(
        "Render  grid=%d×%d  player=(%.1f, %.1f)  facing=%s  "
        "keys=%d  [B=%d R=%d Y=%d]",
        grid_w, grid_h, px, py, req.facing,
        len(items),
        sum(1 for _, _, t in items if t == 10),
        sum(1 for _, _, t in items if t == 11),
        sum(1 for _, _, t in items if t == 12),
    )

    # ── Raytrace ──────────────────────────────────────────────────────────────
    try:
        image = render(solid, items, px, py, angle)
    except Exception as exc:
        log.exception("Render failed")
        raise HTTPException(status_code=500, detail="Rendering failed.") from exc

    # ── Encode to JPEG → base64 → JSON ───────────────────────────────────────
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=93, optimize=True, subsampling=0)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return JSONResponse(content={
        "image":    b64,
        "format":   "jpeg",
        "encoding": "base64",
    })
