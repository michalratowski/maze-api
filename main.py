"""
main.py – FastAPI service: Office Space 3-D maze raycaster.

Endpoints
---------
POST /render   → JSON { "image": "<base64-encoded JPEG>" }
GET  /health   → {"status": "ok"}
GET  /docs     → Swagger UI
GET  /redoc    → ReDoc UI

Grid format
-----------
Rows separated by ';', cells by ','.

  W         – wall  (beige drywall with chair rail)
  BD/RD/YD  – blue / red / yellow security door
  HD        – hidden door (looks identical to a plain wall)
  C         – office chair  (solid, blocks view)
  DC        – desk with computer  (solid, blocks view)
  DE        – empty desk  (solid, blocks view)
  E         – empty passable floor
  P         – player start (exactly one required)
  BK/RK/YK  – blue / red / yellow RFID keycard
  DOC       – document to collect (A4 paper sheet)

Example curl
------------
  curl -X POST https://<host>/render \\
       -H "Content-Type: application/json" \\
       -d '{
         "grid":   "W,W,W,BD,W,W,W;W,P,W,E,E,E,W;W,E,W,E,DOC,BK,W;W,E,RD,W,W,E,W;W,E,W,E,E,RK,W;W,E,W,YD,W,E,W;W,E,E,DOC,E,YK,W;W,W,W,W,W,W,W",
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
    title="Office Space Maze Raycast API",
    description=(
        "Render a first-person 3-D view of an office maze using DDA raycasting "
        "with an **Office Space** visual aesthetic.\n\n"
        "Supported grid tokens: `W` wall · `BD`/`RD`/`YD` security doors · "
        "`E` empty · `P` player · `BK`/`RK`/`YK` keycards · `DOC` document."
    ),
    version="4.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Example payload ───────────────────────────────────────────────────────────

_EXAMPLE_GRID = (
    "W,W,W,BD,W,W,W;"
    "W,P,W,E,E,E,W;"
    "W,E,W,E,DOC,BK,W;"
    "W,E,RD,W,W,E,W;"
    "W,E,W,E,E,RK,W;"
    "W,E,W,YD,W,E,W;"
    "W,E,E,DOC,E,YK,W;"
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
            "| `W`    | Drywall (beige, chair-rail) |\n"
            "| `BD`   | Blue security door |\n"
            "| `RD`   | Red security door |\n"
            "| `YD`   | Yellow security door |\n"
            "| `HD`   | Hidden door (visually identical to `W`) |\n"
            "| `C`    | Office chair (solid – blocks view) |\n"
            "| `DC`   | Desk with computer (solid – blocks view) |\n"
            "| `DE`   | Empty desk (solid – blocks view) |\n"
            "| `E`    | Empty floor |\n"
            "| `P`    | Player start |\n"
            "| `BK`   | Blue RFID keycard |\n"
            "| `RK`   | Red RFID keycard |\n"
            "| `YK`   | Yellow RFID keycard |\n"
            "| `DOC`  | Document (A4 paper sheet) |\n"
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

    1. **Parse** – grid string → solid map (walls/doors) + sprite list
       (keycards + documents).
    2. **DDA raytrace** – per screen column; perpendicular distance controls
       wall-strip height (fish-eye corrected).
    3. **Shading** – drywall uses a chair-rail divider and paint-streak texture.
       Doors show a wood-veneer lower panel, frosted-glass upper pane, and a
       coloured RFID badge reader.  Fog colour is bright (fluorescent ambient)
       so far surfaces wash out to cream, not black.
    4. **Ceiling** – drop-ceiling tile grid with fluorescent light-bay banding.
    5. **Floor** – blue-grey corporate carpet with a diagonal weave pattern.
    6. **Sprite projection** – keycards and document sheets projected via
       camera-plane transform, floor-anchored, z-buffer occluded.
    7. **JPEG encode** → base64 → JSON.
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
        "items=%d  [BK=%d RK=%d YK=%d DOC=%d]",
        grid_w, grid_h, px, py, req.facing,
        len(items),
        sum(1 for _, _, t in items if t == 10),
        sum(1 for _, _, t in items if t == 11),
        sum(1 for _, _, t in items if t == 12),
        sum(1 for _, _, t in items if t == 13),
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
