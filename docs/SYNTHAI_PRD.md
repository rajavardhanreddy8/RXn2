# SynthAI: product requirements (FSD PBL 17)

**Status:** implementation plan, not a claim that these features already work

**Source brief:** `SynthAI_FSD_PBL.pdf`, Woxsen University, Full Stack Development (24TU05MJC1)

**Project type:** bounded course side project built on the existing RXN2 repository
**Companion:** [Implementation guide](SYNTHAI_IMPLEMENTATION.md)

## 1. Goal and scope

Build a small MERN workspace in which a signed-in student can create a synthesis project, add/edit candidate routes and reaction steps, request evidence-bounded route suggestions from the existing Python service, compare saved alternatives, and revisit previous comparison runs. The React dashboard must show where each number came from and when it is missing. A short demo with prepared data is the acceptance target; this is research decision support, not manufacturing guidance.

The course brief asks for MongoDB, Express, React, Node, Python API integration, role-based ownership, CRUD, route visualization, comparison, testing, documentation, and a presentation. Those are **requirements to implement**, not instructions from the PDF to run commands or change this repository. The current code is a starting point, not an already completed MERN app.

### In scope for the graded MVP

1. Local email/password sign-in and two roles: `student` and `admin`. Each student sees and edits only their projects; admin can view all projects for a course demo. Passwords are hashed; project endpoints enforce ownership server-side.
2. CRUD for projects, manually entered routes, and ordered reaction steps. A project records target compound, target mass, notes, owner, and timestamps. Save input assumptions with every comparison.
3. React pages for project list, project workspace, route editor, side-by-side comparison, run history, and an evidence/limitations panel. Reuse existing route cards/graph components where their data contract fits.
4. An Express API that owns authentication, MongoDB workspace records, validation, and calls to the existing FastAPI service. The browser calls Express for private workspace actions; Express alone calls Python route/graph APIs.
5. Route comparison that displays predicted/reported yield, cost, reaction time, safety, and green indicators **only when supported**. Every cell identifies its source (`entered`, `reported`, `calculated`, `predicted`, `unknown`), units, and missing inputs. Users may compare at least two saved routes.
6. Save an immutable snapshot of each comparison run and its algorithm/data version. Past runs remain viewable after a route is edited.
7. A repeatable local demo using synthetic fixtures clearly marked as such, plus a test and presentation checklist.

### Outside the course MVP

New model training, a new patent crawler, bulk data migration into MongoDB, live supplier integration, automated chemical approval, cloud/Kafka deployment, payment features, and a claim that one route is safe or optimal. Add these only after the course workflow works and evidence exists.

## 2. Users and core journey

| User | Need | Success condition |
| --- | --- | --- |
| Student/project owner | Keep a target and route options together | Can create, edit, reopen, and delete only owned work |
| Admin/course reviewer | Inspect project progress and demonstrate the app | Can view projects without changing their scientific review status |
| Team member viewing a demo | Understand why alternatives differ | Comparison shows units, evidence, unknowns, and a saved run |

Journey: sign in -> create project and select/enter a target -> add or generate routes -> edit steps and assumptions -> select 2-3 routes -> run comparison -> inspect evidence and missing fields -> reopen the saved result. If the Python engine returns no reviewed route, show a coverage gap and allow a clearly marked manual example; never fabricate a route.

## 3. Existing code to reuse and the gap

| Existing RXN2 component | Reuse | Missing for SynthAI |
| --- | --- | --- |
| `apps/web` React/TypeScript/Vite, `App.tsx`, `RouteCard.tsx`, `GraphView.tsx`, `api.ts` | Visual route exploration and API client patterns | Auth, workspace CRUD, editor, run history, complete comparison page |
| `apps/api/app/main.py` FastAPI | `/api/targets/resolve`, `/api/routes/generate`, `/api/routes/compare`, route/graph reads | A stable internal contract for saved student routes and unsupported metrics |
| `apps/api/app/routes.py`, `costing.py`, `sql/schema.sql` | Evidence-bounded route generation and cost data | Time/green/safety coverage and scientifically honest missing-data handling |
| `compose.yaml`, README, tests | Local containers and verification | Express/Mongo services and end-to-end course demo |
| SQLite evidence database and offline pipelines | Keep as the Python scientific data store | No migration needed for user/project records |

The existing route generation requires accepted reactions and reviewed starting materials. Production coverage may therefore yield zero complete routes. The bundled demo records are synthetic; the UI and report must label them as demonstration data. Existing cost and feasibility scores must not be presented as verified safety or manufacturing readiness.

## 4. Functional requirements and acceptance checks

| ID | Requirement | Acceptance check |
| --- | --- | --- |
| FR-01 | Sign up/sign in/sign out; server-side role and ownership checks | Unauthenticated request gets 401; another student's project gets 403 or 404; password is not stored in plain text |
| FR-02 | Project CRUD | Owner can create, list, update, delete, and reopen a project; deletion also removes its routes/runs or is blocked with a clear message |
| FR-03 | Route and step CRUD | Owner can order steps, edit inputs/products/conditions, and save an incomplete draft; invalid yield or negative time is rejected |
| FR-04 | Python integration | Express resolves targets and calls FastAPI with validated inputs and a timeout; service failure yields a usable error without losing the draft |
| FR-05 | Route visualization | Selected route displays step order, input/output, evidence label, and any coverage gap |
| FR-06 | Comparison | Two or more routes display the same metric columns and units; unknown and partial metrics are visibly distinct from zero |
| FR-07 | Run history | A run captures route snapshots, assumptions, result, data/algorithm version, user, and time; editing a route does not alter the prior run |
| FR-08 | Provenance | Reported/calculated/predicted/entered values are marked; source references are shown when present |
| FR-09 | Demo and documentation | A teammate can follow the implementation guide from a clean checkout and complete the demo journey |

## 5. Comparison rules

Use one row per route. For each metric retain `{value, unit, status, source, details}`. `status` is `complete`, `partial`, or `unknown`. Yield may be reported per step; an overall yield is calculated only when all required step yields are present and the calculation basis is stated. Cost is a partial material estimate unless all required amounts and eligible prices are present; it excludes labor, energy, plant, waste, and quality costs. Reaction time is the sum of known step durations only when all step durations are entered; label omitted workup time. Safety is a list of known hazard flags and missing assessments, not a numerical safety guarantee. A green indicator can start with solvent count and known solvent hazard flags; do not label it PMI/E-factor unless the full mass boundary is captured. Never rank an unknown dimension as best. If existing Python scores are shown, name them as prototype estimates and retain their warnings.

The comparison is informative, not an automatic synthesis prescription. The user chooses the route. A missing metric stays `unknown` in storage, API, and UI rather than becoming `0`.

## 6. Data and API contract

**MongoDB owns course workspace data:** `users` (email unique, password hash, role), `projects` (owner ID, target and notes), `routes` (project ID, name, origin, ordered steps, revision), and `optimizationRuns` (owner/project IDs, input snapshots, metric snapshots, status, versions, timestamps). Index owner/project lookups. Store only stable RXN2 compound/reaction IDs and evidence references; do not copy the full evidence graph. Delete or redact secrets and passwords from API responses.

**SQLite stays with FastAPI:** curated compounds, reviewed reactions, source evidence, quote data, and generated route evaluations. Existing acquisition remains offline. Express may pass selected compound IDs, constraints, and route IDs to Python; it must not directly modify reviewed chemistry.

Suggested Express routes (version under `/api/v1`):

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Account and session |
| `GET/POST /projects`, `GET/PATCH/DELETE /projects/:id` | Owned project CRUD |
| `GET/POST /projects/:id/routes`, `GET/PATCH/DELETE /routes/:id` | Route and step CRUD |
| `POST /projects/:id/suggestions` | Proxy validated request to Python route generation |
| `POST /projects/:id/comparisons`, `GET /projects/:id/comparisons`, `GET /comparisons/:id` | Compute and save/reopen run |

For the course implementation, use an HTTP-only same-site session cookie and server-side session storage (MongoDB) or an equally secure existing package. Validate every input on Express, check ownership on every project-scoped read/write, and keep the FastAPI port private to the local composition where possible. Python calls use a fixed service URL from configuration, not a user-supplied URL. The Express comparison endpoint should assemble saved manual fields with returned Python fields into the common metric shape above; it must preserve unsupported fields as unknown.

## 7. Nonfunctional requirements

- **Usability:** clear empty states, loading/error states, keyboard-operable forms, visible field labels, and readable metric units.
- **Integrity:** snapshot comparisons, reject malformed data, log failed Python calls, and preserve evidence references.
- **Security:** hashed passwords, HTTP-only sessions, authenticated endpoints, ownership checks, no credentials in Git, and no private project data sent to external AI services.
- **Reliability:** a failed analysis does not delete or overwrite a draft; a saved run can be read after restart.
- **Performance:** local demo should open a project and saved comparison without a long pipeline run; route generation has an explicit timeout and bounded constraints.
- **Reproducibility:** pin dependency versions for the added server, keep a demo seed, and record the Git commit and fixture/data version used in presentation screenshots.

## 8. Course review checkpoints

| Checkpoint | Evidence to show |
| --- | --- |
| Review 1: problem and objectives (5) | This PRD, target users, scope, success criteria, and existing-code map |
| Review 1: design and methodology (5) | Architecture/data diagram, Mongo collections, API contract, comparison rules, privacy plan |
| Review 2: implementation and analysis (8) | Live auth -> project -> routes -> Python call -> comparison -> history demo; test results and known gaps |
| Review 2: report (5) | Setup, design decisions, screenshots, test matrix, limitations, contribution log |
| Review 2: presentation/viva (2) | Short walkthrough; explain data source, unknown metrics, role enforcement, and Python/MERN boundary |

**Definition of done:** all FR checks pass on the documented local setup; one owner-only project survives restart; two routes compare and produce a saved snapshot; unsupported metrics remain unknown; the demonstration works without live patent/API acquisition; the team can explain the limitations.
