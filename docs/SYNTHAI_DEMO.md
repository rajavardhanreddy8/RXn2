# Start and present the SynthAI course demo

This is a local educational demonstration. Its example routes and prices are synthetic. Yield and duration calculations use entered/reported values; no trained prediction model is bundled. Read [the ZIP review](SYNTHAI_ZIP_REVIEW.md) for checked behavior and remaining limits.

## 1. Clone and prepare

Install Git, Node 24, and Docker Desktop with its Linux engine running. From PowerShell:

```powershell
git clone --branch fsd --single-branch https://github.com/rajavardhanreddy8/RXn2.git
Set-Location RXn2
Copy-Item .env.example .env
$synthSecret = node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('hex'))"
(Get-Content .env) -replace '^JWT_SECRET=.*$', "JWT_SECRET=$synthSecret" | Set-Content .env
```

Keep `.env` private. The server requires a random secret of at least 32 characters and fails clearly if it is absent. There is no shared default password or signing key.

## 2. Preferred container start

```powershell
docker compose -f compose.yaml -f compose.demo.yaml up --build
```

Open `http://localhost:5173/#workspace`. The override selects `data/curated/rxn2-demo.sqlite`, enables synthetic seeding, and uses local folders rather than requiring Drive-mounted acquisition data. MongoDB stores workspace records in the `mongo-data` named volume. Stop with Ctrl+C or `docker compose -f compose.yaml -f compose.demo.yaml down`; do not add `-v` unless you intend to delete the workspace database.

If Docker reports a missing `dockerDesktopLinuxEngine` pipe, its engine is not running. Start Docker Desktop and wait until its engine is ready. This host could not run the container engine during review; the live demo was verified using native services below, and the Compose configuration was checked separately.

## 3. Native start when Docker is unavailable

Use Python 3.12+ with the project requirements, Node 24, and a local MongoDB 7 executable. From the repository root, install once:

```powershell
npm --prefix apps/server ci --ignore-scripts
npm --prefix apps/web ci --ignore-scripts
python -m pip install -r apps/api/requirements.txt -r requirements-pipeline.txt
npm --prefix apps/server run build
```

Run these commands in four separate terminals, all starting in the repository root:

**Terminal 1 - persistent MongoDB:**

```powershell
New-Item -ItemType Directory -Force data/cache/synthai-mongo | Out-Null
mongod --dbpath data/cache/synthai-mongo --bind_ip 127.0.0.1 --port 27017
```

**Terminal 2 - Python evidence engine:**

```powershell
$env:SCALEUP_DB_PATH = "$PWD/data/curated/rxn2-demo.sqlite"
$env:RXN2_SEED_DEMO = 'true'
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 3 - Express workspace:**

```powershell
npm --prefix apps/server start
```

**Terminal 4 - React:**

```powershell
npm --prefix apps/web run dev
```

The server loads the root `.env`, including MongoDB at `127.0.0.1:27017` and Python at `127.0.0.1:8000`. It never falls back to disposable storage. If a port is occupied, choose another and set `PORT`, `MONGODB_URI`, `FASTAPI_URL`, `VITE_EXPRESS_PROXY`, and `VITE_API_PROXY` consistently. The reviewed native run used 27019, 4100, 8100, and frontend 5180 to avoid other running work.

## 4. A 3-5 minute presentation

1. Provision the presentation account with `seed:user` or `seed:admin` as described in [Atlas setup](SYNTHAI_ATLAS_SETUP.md), then open **Projects & Workspace** and sign in. Explain that access is granted by the administrator, passwords are hashed, and each user is restricted to their own projects.
2. Click **+ New Project**, choose **Demo benzamide target**, title it **SynthAI Presentation Demo**, and use 1000 g. Put **Synthetic course example** in the notes.
3. Add a manual route with two synthetic steps: yields 85% and 80%, reaction durations 4 h and 6 h, and solvents Water and Ethanol. Use evidence labels such as `SYNTHETIC-DEMO-A`, not invented patent numbers. Save it.
4. Add a one-step alternative with yield 68% and duration 12 h. Leave price and safety inputs missing. Save it.
5. Open **Compare Routes & Snapshots** and run the comparison. The first route has a calculated linear yield of 68% and reaction duration of 10 h. The alternative has entered yield 68% and reaction duration 12 h. Manual material cost stays unknown. Solvent diversity is a partial screening indicator, not a green performance score.
6. Query Python for candidate routes and select an available generated route. Generated results are fetched again by Express from Python when comparison runs; the browser cannot assign an authoritative price or evidence label. Generated multi-step overall yield stays unknown when branch topology has not been accounted for.
7. Open **Run History** and a saved snapshot. Edit the source draft, then reopen the old run: its original numbers must remain unchanged.
8. Sign out and sign in again. Reopen the project. If demonstrating persistence, restart MongoDB and Express using the same data folder and signing key, then reopen it again.

For an unavailable target, show the coverage gap and the manual draft option. For a Python outage, explain the visible error and show that drafts still exist. Keep **Show research tools** closed during the short course presentation unless you have the research datasets loaded.

## 5. Administrator and user provisioning

For native startup, set `SYNTHAI_ADMIN_EMAIL` and a private `SYNTHAI_ADMIN_PASSWORD` of at least 12 characters in your terminal, then run `npm --prefix apps/server run seed:admin`. To grant a standard account, set `SYNTHAI_USER_EMAIL`, `SYNTHAI_USER_PASSWORD`, and optional `SYNTHAI_USER_NAME`, then run `npm --prefix apps/server run seed:user`. Sign in with the provisioned account. Admin viewing does not grant permission to edit another student's project. Public registration is disabled by default.

## 6. Checks before presenting

```powershell
npm --prefix apps/server run build
npm --prefix apps/web run build
npm test
python -m pytest -q apps/api/tests
```

Server tests use the existing `mongodb-memory-server` test dependency and may download its MongoDB binary on first use. If MongoDB is already installed, set `MONGOMS_SYSTEM_BINARY` to the absolute `mongod` executable path before testing. This test-only database is separate from the persistent demo database.

For team work, create branches such as `codex/fsd-auth` from `fsd`, and target pull requests at `fsd`. Do not use `fsd/auth`: Git cannot keep both a branch called `fsd` and a nested branch called `fsd/auth`.
