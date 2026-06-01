"""
main.py – FastAPI service that renders a first-person 3-D maze view.

POST /render
  Body (JSON):
    grid   – maze string; rows separated by ';', cells by ','
             W = wall, E = empty, P = player
    facing – player direction: "up" | "down" | "left" | "right"

  Response: JPEG image  (500 × 500)

GET /health
  Returns {"status": "ok"}

Example curl:
  curl -X POST http://localhost:8000/render \\
       -H "Content-Type: application/json" \\
       -d '{"grid":"W,W,W,W,W;W,P,E,E,W;W,E,W,E,W;W,E,E,E,W;W,W,W,W,W",
            "facing":"up"}' \\
       --output maze.jpg
"""

import io
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from raycast import parse_grid, facing_to_angle, render

# ── App ───────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Maze Raycast API",
    description="Render a first-person 3-D maze view via DDA raycasting.",
    version="1.0.0",
)


# ── Schema ────────────────────────────────────────────────────────────────────

class RenderRequest(BaseModel):
    grid: str = Field(
        ...,
        description=(
            "Maze grid. Rows separated by ';', cells by ','. "
            "W = wall, E = empty, P = player (exactly one required)."
        ),
        examples=["W,W,W,W,W;W,P,E,E,W;W,E,W,E,W;W,E,E,E,W;W,W,W,W,W"],
    )
    facing: str = Field(
        ...,
        description="Direction the player faces: up | down | left | right.",
        examples=["up"],
    )

    model_config = {"json_schema_extra": {
        "example": {
            "grid": (
                "W,W,W,W,W,W,W;W,E,W,E,E,E,W;W,E,W,E,E,E,W;"
                "W,E,W,W,W,E,W;W,E,W,E,E,E,W;W,E,E,E,E,E,W;"
                "W,E,E,E,E,E,W;W,P,E,E,E,E,W;W,E,E,E,E,E,W;"
                "W,W,W,W,W,W,W"
            ),
            "facing": "up",
        }
    }}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post(
    "/render",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Rendered maze view"},
        400: {"description": "Invalid grid or facing value"},
    },
    tags=["maze"],
)
def render_maze(req: RenderRequest):
    """
    Parse the maze grid, raytrace a first-person view from the player's
    position and direction, and return a 500 × 500 JPEG image.
    """
    try:
        maze, px, py = parse_grid(req.grid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid grid: {exc}") from exc

    try:
        angle = facing_to_angle(req.facing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log.info("Rendering maze %dx%d  player=(%.1f, %.1f)  facing=%s",
             len(maze[0]) if maze else 0, len(maze), px, py, req.facing)

    image = render(maze, px, py, angle)

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92, optimize=True)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/jpeg",
        headers={"Content-Disposition": 'inline; filename="maze.jpg"'},
    )
