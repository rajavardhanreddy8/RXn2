# SynthAI implementation guide and checkpoints

**Read first:** [SynthAI PRD](SYNTHAI_PRD.md). This guide describes work to do; the current branch contains planning documents, not a finished MERN implementation.

## 1. Implementation choice

Keep the existing React app and FastAPI/RXN2 evidence engine. Add one small Express service and MongoDB for accounts, projects, route drafts, and saved comparison runs. The flow is:

```text
Browser (existing React/Vite)
   |  authenticated /api/v1 requests
   v
Express (new: sessions, ownership, CRUD, comparison snapshots)
   |  private, bounded HTTP calls
   v
FastAPI (existing: target, route, evidence, cost logic) -> SQLite evidence store

MongoDB <- Express (users, projects, routes, optimization runs)
Offline acquisition/curation -> SQLite (outside the interactive request path)
```

This satisfies the MERN requirement without porting chemistry logic or moving the patent corpus. Add no new queue, data pipeline, or ML model for the course demo. Keep the project self-contained under this repository.

## 2. What is available today

| Path | Current responsibility | Planned change |
| --- | --- | --- |
| `apps/web/src/App.tsx`, `api.ts`, `RouteCard.tsx`, `GraphView.tsx` | Route explorer, visualization, and local Python API calls | Add account/workspace/comparison views; route private calls through Express |
| `apps/api/app/main.py`, `models.py`, `routes.py`, `costing.py` | FastAPI target resolution, route generation/comparison, evidence/cost | Keep core; add only a narrow internal endpoint or adapter if saved manual routes require it |
| `sql/schema.sql`, `data/curated/*.sqlite` | Scientific evidence and generated candidates | Do not migrate to MongoDB |
| `compose.yaml` | Runs Python API and Vite web | Add MongoDB and Express; make web proxy point to Express |
| `apps/api/tests`, `test`, `apps/web/package.json` | Existing checks | Add focused Express ownership/comparison checks and one browser journey |

The current UI is not authenticated. `POST /api/routes/generate` currently accepts a compound ID or query, target mass, currency, and constraints; it returns `coverage_gap: true` when no complete evidence-bounded route exists. `POST /api/routes/compare` accepts 2-10 generated route IDs. The route IDs and evaluations live in the Python/SQLite side. Neither endpoint is a persistent per-user project workspace.

## 3. Clone the repository and work on `fsd`

The shared course branch is `fsd`, based on `rxn2-knowledge-graph-foundation` and containing these planning documents. Its local creation does not make it available on GitHub; the branch owner must publish it before teammates can clone it. Once `origin/fsd` exists, a new teammate can use PowerShell:

```powershell
git clone --branch fsd --single-branch https://github.com/rajavardhanreddy8/RXn2.git
Set-Location RXn2
git branch --show-current
```

The last command should print `fsd`. In an existing clone, use `git fetch origin` followed by `git switch --track origin/fsd` if the local `fsd` branch does not exist, or `git switch fsd` and `git pull --ff-only origin fsd` if it does.

Make each feature on a short branch from the latest `fsd`, for example:

```powershell
git switch fsd
git pull --ff-only origin fsd
git switch -c fsd/auth
# Edit and test the authentication feature.
git add apps/server
git commit -m "Add course app authentication"
git push -u origin fsd/auth
```

Open a pull request from `fsd/auth` into `fsd` after the feature checks pass. Use another name such as `fsd/workspace`, `fsd/dashboard`, or `fsd/comparison` for other work. Do not commit `.env`, database files, generated data, or secrets. Before a new feature, switch back to `fsd` and pull its latest changes. If `origin/fsd` has not been published yet, continue on the already created local `fsd` branch; the clone commands above will work after publication.

## 4. Prepare and start the current baseline

Prerequisites: Git, Docker Desktop with Compose, and Node 22.5 or newer for the root CLI. Have enough local space for Docker images. The optional Drive-mounted raw-data paths in `.env.example` are for acquisition; the course demo should use local fixture paths and should not require a cloud account.

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
```

In `.env`, point `RXN2_RAW_ROOT`, `RXN2_CLOUD_RESULTS_ROOT`, and `RXN2_OCR_ROOT` to accessible local folders if the example Drive paths are not mounted. Set `RXN2_DB_PATH=/workspace/data/curated/rxn2-demo.sqlite` and `RXN2_SEED_DEMO=true` **only for a synthetic demonstration**. Keep production settings separate. Create the folders if the bind mounts do not exist. Then:

```powershell
docker compose up --build
```

Open `http://localhost:5173` for the current UI and `http://localhost:8000/docs` for FastAPI. `http://localhost:8000/api/health` should respond. Stop with `Ctrl+C`; add `-d` to run in the background. If Docker cannot mount the paths, fix `.env` before debugging application code. The current app may show no real routes because the evidence store requires accepted reactions and reviewed starting materials.

Baseline checks before making changes:

```powershell
npm test
npm --prefix apps/web run build
docker compose run --rm api pytest -q apps/api/tests
```

Record any pre-existing failure in the project report. Do not count a synthetic fixture as validated chemistry or claim model accuracy from a successful UI test.

## 5. Build order

### Checkpoint 0 - baseline and design (Review 1)

1. Run the baseline commands above; take a screenshot of the existing route explorer and note the Git commit.
2. Choose one demonstrable target in the synthetic fixture. Check `/api/targets/resolve` and `/api/routes/generate` in FastAPI docs. Record whether routes exist; plan the manual-route fallback if not.
3. Sketch the four Mongo collections from the PRD. Agree field names, ownership rule, metric status values, and screen flow before coding.
4. Draw the request/data diagram in section 1 for Review 1. Show the PRD's requirements and scope.

**Exit check:** all teammates can explain which records go to Mongo versus SQLite and why a route can have missing metrics.

### Checkpoint 1 - Express, MongoDB, and login

1. Create `apps/server` with an Express app, versioned `/api/v1` router, MongoDB connection, environment validation, and a health route. Use the official MongoDB driver or an already selected project dependency; avoid duplicating domain models in two layers.
2. Add `mongo` and `server` services to `compose.yaml`; keep Mongo data in a named volume. Give Express a fixed internal Python base URL such as `http://api:8000` and a connection string from environment variables. Change the Vite `/api` proxy to Express. Expose Python to the host only if useful for local debugging.
3. Add register/login/logout/me. Normalize email, hash password with a reputable password hashing library, use HTTP-only same-site cookies and server-side sessions, and rate-limit login. Seed the admin account through a local script or environment setup, not through a public role field at registration.
4. Add middleware that loads the session and checks ownership on every project-scoped route. Reject client-supplied `ownerId` and `role` changes.

**Exit check:** a student can log in; an unauthenticated request fails; student B cannot read, edit, or delete student A's project. Mongo data survives a container restart.

### Checkpoint 2 - project and route workspace

1. Implement project create/list/read/update/delete. Fields: owner ID, title, target compound ID/name, target mass and unit, notes, created/updated timestamps. Validate nonempty title and positive mass. Deletion policy must cover child routes and runs consistently.
2. Implement routes nested under a project and ordered steps. A draft step needs a name, reactants/input labels, product label, and position. Optional fields: yield %, duration and unit, solvent, conditions, hazard notes, evidence reference, and data source. Reject yield outside 0-100 and negative duration; allow a clearly incomplete draft.
3. Add React list, form, route editor, empty state, and save/error messages. Reuse current visual components where possible. Keep manual route IDs separate from Python-generated route IDs and mark the origin.

**Exit check:** owner creates a project with two routes, reorders a step, refreshes the page, and sees the same records. A missing target or incomplete step is clearly indicated.

### Checkpoint 3 - Python bridge and comparison

1. Add an Express client for existing FastAPI calls with a fixed URL, bounded timeout, error mapping, and request validation. First integrate target resolution and generation using the current FastAPI request shapes; do not expose an arbitrary proxy URL.
2. Preserve `coverage_gap` and source labels. When no reviewed route exists, show the gap and let users enter a **manual example**. Do not change `needs_review` evidence into accepted chemistry to make a demo look complete.
3. Map both saved manual routes and generated routes into a common comparison view. For each metric store value, unit, status (`complete`/`partial`/`unknown`), origin (`entered`/`reported`/`calculated`/`predicted`), evidence and warnings. Calculate overall yield only from complete step data; sum reaction duration only when every step has a duration. Use Python cost outputs only with their completeness warning. Display safety as known flags plus unknowns; use a modest green indicator with an explicit boundary.
4. Save an `optimizationRuns` snapshot on every comparison: selected route revisions, target and constraints, full metric result, method/data version, actor, start/end time, and status (`completed`/`failed`). List and reopen runs in React. A failed Python call must not erase route edits.

**Exit check:** compare two routes, view consistent units and unknown labels, edit one route, and reopen the old unchanged result. Simulate Python being down and verify a clear error and preserved draft.

### Checkpoint 4 - tests, report, presentation (Review 2)

1. Test ownership on all CRUD methods, validation boundaries, no invented metric value, snapshot immutability, and the Python failure path. Add one end-to-end journey from login through saved comparison. Retain the existing Python and web checks.
2. Restart all containers and rerun the demo. Verify that secrets and `.env` are not committed. Use a synthetic-data banner in screenshots and the presentation.
3. Write the course report: problem, goals, existing-code reuse, architecture, ER/collection design, API list, UI screenshots, test results, limitations, each teammate's contributions, and next work. Keep a brief progress log for faculty updates.
4. Prepare a 3-5 minute demonstration and viva: login/ownership, project and route editing, Python call, comparison/history, unknown metric behavior, and Mongo-versus-SQLite rationale.

**Exit check:** PRD definition of done is met; the demo succeeds from a clean local start; each team member can explain one end-to-end part.

## 6. Suggested minimal record shapes

```json
{
  "project": {"_id": "...", "ownerId": "...", "title": "Demo target", "targetCompoundId": "...", "targetMassG": 1000, "notes": "", "createdAt": "...", "updatedAt": "..."},
  "route": {"_id": "...", "projectId": "...", "origin": "manual", "revision": 1, "steps": [{"position": 1, "name": "Example step", "yieldPercent": null, "durationHours": null, "evidenceRef": null}]},
  "metric": {"value": null, "unit": "h", "status": "unknown", "source": "entered", "details": "No duration entered"},
  "run": {"_id": "...", "projectId": "...", "createdBy": "...", "routeSnapshots": [], "metrics": {}, "methodVersion": "course-mvp-v1", "status": "completed", "createdAt": "..."}
}
```

These are design examples, not files to paste directly into MongoDB. Define one canonical server-side schema, then type the client response to match it. Do not accept `ownerId`, `createdBy`, or a metric's trusted source label from arbitrary client input.

## 7. Verification matrix

| Scenario | Expected result |
| --- | --- |
| Wrong password or missing cookie | No private records returned |
| Student B guesses student A's IDs | No read/write/delete access |
| Invalid yield, mass, or duration | 4xx response and no saved mutation |
| Python returns no route | Coverage gap and manual draft option; no fabricated evidence |
| Python timeout or 5xx | Clear error, existing project unchanged, failed run recorded if one began |
| One route lacks a yield/time/price | Corresponding comparison metric is unknown/partial, never zero or “best” |
| Route changed after comparison | Old run reproduces the original route snapshot |
| Container restart | Accounts, projects, routes, and runs remain in MongoDB |
| Synthetic fixture | Visible synthetic/demo label in UI and report |

## 8. Practical team split and submission checklist

The four students named in the assignment brief can divide work by feature boundary: one owns Express auth/ownership, one owns Mongo workspace APIs, one owns React screens, and one owns the Python bridge/comparison and test/report integration. Each feature owner should pair-review the adjacent API/UI contract. Record actual contributions rather than assuming this split was followed.

- [ ] Review 1: PRD, architecture, collection sketch, scope, and baseline result ready.
- [ ] Review 2: all FR-01 through FR-09 checks demonstrated; report and screenshots included.
- [ ] Run `npm test`, web build, Python tests, new server tests, and the end-to-end journey.
- [ ] Show failure cases: unauthorized access, no reviewed route, incomplete metric, Python unavailable.
- [ ] Save demo data/version and screenshots; label synthetic examples.
- [ ] Publish `fsd` when authorized, then submit each tested feature branch as a pull request into `fsd`; this branch is a planning baseline until implementation lands.

## 9. Known limits and next decision

The present RXN2 production evidence is not guaranteed to yield a complete reviewed route, and current cost/feasibility scores do not cover the full process. The course MVP should demonstrate the application workflow with labelled fixtures and honest gaps. Real-route claims, reliable safety/green scoring, and model improvements require reviewed evidence and separate validation after the subject project.
