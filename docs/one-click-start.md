# One-Click Local Startup (Windows)

## Prerequisites
- Windows environment with PowerShell available.
- Node.js and npm in PATH.
- Python 3 in PATH via `py -3` or `python`.

## Start Services
1. Double-click `start-dev.bat` in repository root.
1. On first run, the script will auto-prepare dependencies:
   - Create `.venv` for backend and install `backend/requirements.txt`.
   - Run `npm ci` in `frontend` if `frontend/node_modules` is missing.
   - Create `.env` from `.env.example` if root `.env` is missing.
1. Browser opens automatically after frontend is ready.

## Stop Services
1. Double-click `stop-dev.bat` in repository root.
1. The script reads `.runtime/dev-processes.json` and stops backend/frontend process trees.

## Runtime Behavior
- Single-instance mode: if both services are already running, startup exits and prints existing URLs.
- Backend port starts at `8000` and auto-selects next available port on conflict.
- Frontend port starts at `3000` and auto-selects next available port on conflict.
- Frontend receives backend URL through `VITE_API_BASE_URL`.

## Logs
- `logs/backend.dev.out.log`
- `logs/backend.dev.err.log`
- `logs/frontend.dev.out.log`
- `logs/frontend.dev.err.log`

## Runtime State File
- `.runtime/dev-processes.json`
- Fields:
  - `backendPid`
  - `frontendPid`
  - `backendPort`
  - `frontendPort`
  - `startedAt`
  - `logPaths`

## Troubleshooting
- If `npm` or Python is missing, install the missing dependency and rerun `start-dev.bat`.
- If API authentication fails, update root `.env` with valid keys.
- If startup fails, inspect the `logs/*.log` files for exact errors.
