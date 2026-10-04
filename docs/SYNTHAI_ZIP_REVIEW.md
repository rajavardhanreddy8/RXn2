# SynthAI submission review

Reviewed: 5 October 2026. Source: `SYNTH AI.zip`. Destination: `rajavardhanreddy8/RXn2`, branch `fsd`.

## Verdict

The submission follows the course workspace architecture, but the ZIP was not ready to publish unchanged. The corrected implementation is suitable for a local synthetic course demonstration. Its original completion reports were not accepted as verification evidence.

## Plan coverage

| Requirement | Implementation and checkpoint |
| --- | --- |
| Reuse existing code | Existing React interface and FastAPI/SQLite scientific engine retained; Express/MongoDB workspace added. |
| Accounts and roles | Registration, sign-in, hashed passwords, student ownership, admin read access and logout revocation. Admin creation uses a private seed command. |
| Project and route CRUD | Project forms, ordered manual steps, inputs, yields, duration, solvents, conditions, hazards and evidence; saved draft revisions. |
| Comparison | Select 2–10 manual/generated routes; yield, reaction duration, material cost, hazard flags and solvent diversity with completeness/source labels. |
| Python integration | Authenticated workspace requests go through Express; generated comparison data is retrieved from Python rather than trusted from browser input. |
| History | Saved metric and step snapshots retain their original values after draft edits; ownership checks apply to history. |
| Startup and presentation | See [demo guide](SYNTHAI_DEMO.md) for cloning `fsd`, installation, environment setup, launch commands and presentation checkpoints. |

## Corrections before publication

- Fixed server TypeScript snapshot schema mismatch and invalid Docker package dependency.
- Corrected frontend proxy configuration and made hosted build metadata optional for local builds.
- Required a private signing key; removed disposable MongoDB fallback from application startup.
- Revoked old tokens on logout; checked current database roles; constrained admin access to reading other students' work.
- Deleted dependent history when a project is deleted.
- Rejected missing/duplicate generated routes and mismatched target, mass or currency. Python failures produce an error and a failed run without removing drafts.
- Added generated candidate selection and missing manual input fields; corrected stale sidebar route counts.
- Marked incomplete prices, unassessed safety and solvent screening honestly. Manual entries cannot claim patent provenance. Linear yield calculations disclose their assumption; reaction duration excludes workup.
- Added a synthetic local demo configuration and retained scientific source logic apart from exposing stored comparison basis.

## Verification evidence

| Check | Result |
| --- | --- |
| Express TypeScript production build | Passed |
| React TypeScript/Vite production build | Passed |
| Root JavaScript tests | 6 passed |
| Express API tests | 64 passed; zero failures |
| Full existing Python API suite | 39 passed, 1 failed, 1 deprecation warning |
| Docker Compose configuration | Validated successfully |
| Docker image build/container startup | Not verified: Docker engine unavailable on this machine |
| Browser journey | Registration, project creation, two manual drafts, comparison, generated candidate query and saved mixed manual/generated comparison verified |
| Restart persistence | Existing account, project, drafts and saved history retained after Express restart using the same persistent MongoDB database |

The existing Python failure is `test_schema_repair_keeps_source_and_failed_candidate_separate` in `apps/api/tests/test_relations.py`. It assumes Groq strict `json_schema`, while the baseline implementation defaults to `json_object` unless strict mode is enabled. Both the expectation and behavior predate this submission. Research-provider extraction was not changed to make the demo pass. This must be resolved before claiming the entire repository suite is green.

Browser sample: a two-step route with 85%/80% yields and 4 h/6 h durations displays 68% and 10 h; a one-step alternative displays entered 68% and 12 h. Manual material cost stays unknown. Missing hazards remain unknown; entered hazards and solvent diversity remain partial. Generated synthetic candidates were returned by Python with distinct route IDs and example costs.

## Presentation boundaries and remaining checkpoints

- [x] Run a native local demo with MongoDB, Express, Python and React.
- [x] Build frontend/server and verify workspace ownership, history, missing-data behavior and gateway failures through API tests.
- [x] Check branch instructions: use `fsd` and topic branches such as `codex/fsd-auth`; Git cannot also create `fsd/auth`.
- [ ] On the presentation computer, run the Docker startup checkpoint or the documented native startup and repeat the short walkthrough.
- [ ] Resolve the pre-existing Python research-provider test mismatch before an all-suite release claim.
- [ ] Use reviewed real evidence and quote data before demonstrating scientific conclusions. The bundled synthetic fixture is a teaching example.

No newly trained ML model is included. Yield arithmetic and the existing evidence engine implement this small subject project. Hazard flags are not a validated safety rating; solvent diversity is not PMI or E-factor. Historical runs use version labels, but local research datasets are not an immutable content-addressed registry. Database process-restart persistence and Docker runtime were not independently verified in this review; only Express restart persistence was exercised.

For planned future work, refer to [PRD](SYNTHAI_PRD.md) and [implementation guide](SYNTHAI_IMPLEMENTATION.md). Pushes publish source and documentation, not local demo accounts, databases, signing keys, dependencies or ZIP Git metadata.
