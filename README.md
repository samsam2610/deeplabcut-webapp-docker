# DeepLabCut WebApp

Browser-based pipeline orchestrator for [DeepLabCut](https://github.com/DeepLabCut/DeepLabCut) pose estimation and [Anipose](https://github.com/lambdaloop/anipose) 3D triangulation, with an active-learning frame curation UI.

Everything runs in Docker. You point it at a directory of videos and a DeepLabCut project, and drive extraction, labelling, training, analysis, and 3D triangulation from a browser.

---

## Two repositories, side by side

This repository is one half of the application. The other half — the 3D inline-analysis UI, the Lightning Pose integration, and the SAM/DINO frame-proposal service — lives in **[deeplabcut-webapp-docker-supports](https://github.com/samsam2610/deeplabcut-webapp-docker-supports)**.

They are coupled by **relative path**, not by submodule or package registry. `docker-compose.yml` reaches its sibling with `../deeplabcut-webapp-docker-supports/...` for both build contexts and around twenty bind mounts, so the only layout that works is:

```
<parent>/
├── deeplabcut-webapp-docker/           # this repo — run docker compose here
└── deeplabcut-webapp-docker-supports/  # the sibling — directory name is hard-coded
```

Rename either directory and the build fails on a path you never typed.

**Give that parent directory to this stack alone.** The `dlc-3d` service builds with `context: ../`, so every file beside the two repositories is packed up and sent to the Docker daemon. Sharing the parent with unrelated projects is the difference between a 4.5 GB build context and a 160 GB one, and nothing warns you.

---

## Quick start

### Prerequisites

- Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (the UI runs without a GPU; training and analysis do not)
- Docker Compose v2 (`docker compose`, not `docker-compose`)
- Disk for your video data and model checkpoints — tens of GB per project is normal

### Install

```bash
mkdir dlc-stack && cd dlc-stack
curl -O https://raw.githubusercontent.com/samsam2610/deeplabcut-webapp-docker/main/install.sh
chmod +x install.sh

./install.sh fetch      # clone both repositories, side by side
./install.sh doctor     # check prerequisites and report paths you must edit
./install.sh build      # build images (slow the first time — several GB of CUDA)
./install.sh up         # start everything
```

The UI is then at **http://localhost:5000**.

Components build and start independently, so a UI change does not mean rebuilding the TensorFlow worker:

```bash
./install.sh build dlc-3d
./install.sh up sam-training
./install.sh status
```

Plain `docker compose` works too — the script is a convenience, not a requirement.

### Configure your data paths

`docker-compose.yml` mounts host directories into every service. **These are absolute paths and will not match your machine** — edit them before the first start. `./install.sh doctor` prints the current list. They follow this shape:

| Host path | Mounted at | Holds |
|---|---|---|
| your data disk | `/user-data/Parra-Data/Disk` | projects and videos, read-write |
| your network/cloud share | `/user-data/Parra-Data/Cloud` | archived sessions |
| your NAS share | `/user-data/NAS-Data-Share` | shared datasets |
| `${HOST_DATA_DIR:-./data}` | `/app/data` | app state, caches, model weights |

Only `HOST_DATA_DIR` is parameterised today; the rest are literal and repeated per service. Set what you need in a `.env` file beside `docker-compose.yml`:

```
HOST_DATA_DIR=/srv/dlc-data
FLASK_SECRET_KEY=<a long random string>
APP_TOKEN=<the token the UI asks for>
```

The `sam-training` service additionally mounts a Hugging Face token read-only. SAM 3 and DINOv3 are gated model repositories, so without it that one service cannot fetch weights — the rest of the stack is unaffected.

---

## Services

| Service | Base image | Port | Role |
|---|---|---|---|
| `flask` | `python:3.10-slim` | 5000 | Web UI and API; reverse-proxies the module services |
| `redis` | `redis:7-alpine` | 6379 | Celery broker and shared state |
| `worker` | `pytorch:2.9.1-cuda13.0` | — | PyTorch DeepLabCut: training, analysis, triangulation |
| `worker-tf` | `tensorflow:2.13.0-gpu` | — | Legacy TensorFlow DeepLabCut models |
| `dlc-3d` | (supports repo) | — | 3D inline-analysis UI, served under `/dlc-3d/` |
| `dlc-3d-worker` | (supports repo) | — | Lightning Pose conversion, training, prediction |
| `sam-training` | (supports repo) | — | SAM 3 + DINOv3 candidate-frame proposals |
| `data-init` | — | — | One-shot permission fixup; exits immediately |

Only `flask` and `redis` publish ports. Everything else is reachable on the internal Docker network, and the module UIs appear under a path prefix on port 5000 rather than a second port.

---

## Repository layout

```
.
├── install.sh                  # fetch / build / up / status / doctor
├── docker-compose.yml          # every service, including the sibling repo's
├── Dockerfile.flask            # web tier, no GPU
├── Dockerfile.worker           # PyTorch + DeepLabCut
├── Dockerfile.worker-tf        # TensorFlow + legacy DeepLabCut
├── docs/
│   ├── ARCHITECTURE_MAP.md     # how the pieces fit together
│   ├── DATA_PIPELINE.md        # what happens to a video, end to end
│   └── UI_STATE_MAP.md
├── scripts/
├── src/
│   ├── app.py                  # Flask entry point and blueprint registration
│   ├── celery_app.py           # Celery instance and worker hooks
│   ├── tasks.py                # worker entry point
│   ├── dlc/                    # DeepLabCut blueprints and Celery tasks
│   ├── anipose/                # Anipose blueprints and Celery tasks
│   ├── anipose_src/            # triangulation implementations
│   ├── templates/              # Jinja2 templates
│   └── static/                 # frontend JS and CSS
└── tests/
```

---

## Tests

Most of the suite runs on the host:

```bash
python -m pytest tests -q
```

Two things are worth knowing before running the whole thing:

**Pin the temp directory.** Some tests write multi-gigabyte fixtures — one suite produced 14 GB in sixteen minutes. `pytest.ini` bounds what is retained between runs, but pinning `--basetemp` gives you something you can delete unconditionally:

```bash
python -m pytest tests -q --basetemp=/tmp/dlc-pytest
rm -rf /tmp/dlc-pytest
```

**Some tests need the stack running.** Anything touching Redis or a live endpoint will error rather than skip if the services are down. Start the stack first, or select the pure-logic modules.

The sibling repository has its own suites, including browser end-to-end tests that drive the running app:

```bash
cd ../deeplabcut-webapp-docker-supports/dlc-3D
python -m pytest tests -q --ignore=tests/e2e   # unit and wiring tests
python -m pytest tests/e2e -q                  # Playwright, needs the stack up
for f in tests/unit/*.mjs; do node --test "$f"; done   # one file at a time
```

The `.mjs` tests must be run one file at a time — Node 16 finds no tests when given a directory.

---

## Development notes

- **Source is bind-mounted**, so most edits reach the containers without a rebuild. Whole-directory mounts (`src/static`, `dlc_3d_bp`) pick changes up on reload.
- **Single-file mounts are the exception.** `src/app.py` and `src/tasks.py` are mounted individually, and an editor that replaces the file rather than writing in place breaks the mount. Those need `docker compose up -d --force-recreate <service>`, not `restart`.
- **Never recreate a worker while a job is running** — it kills the run without a message. Check the Jobs view first.
- **New template partials need a compose entry.** `dlc-3d` mounts its templates file by file, so a newly added `.html` does not exist inside the container until `docker-compose.yml` names it.
- **H5 writes go through an atomic rename** via a `.tmp` file. Do not bypass it; readers can otherwise see a half-written file.
- Treat source video directories as read-only and work on copies.

See `docs/ARCHITECTURE_MAP.md` before making structural changes.
