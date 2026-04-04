# routes/

Standalone Flask Blueprint modules that don't belong to the DLC or Anipose packages.

## Modules

| File | Blueprint | Routes |
|------|-----------|--------|
| `annotate.py` | `annotate` | `GET /annotate/video-info`, `GET /annotate/video-frame/<n>`, `GET /annotate/csv`, `POST /annotate/create-csv`, `POST /annotate/save-row` — video annotation UI backend |
| `custom_script.py` | `custom_script` | `POST /custom-script/run`, `GET /custom-script/status/<job_id>` — runs user-supplied Python scripts in a sandboxed subprocess |

Both modules read shared state from `current_app.config` (set by `app.py`'s `before_request` hook).
