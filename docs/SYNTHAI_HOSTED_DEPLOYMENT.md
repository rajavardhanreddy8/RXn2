# Hosted deployment checkpoint

The Sites project serves the React dashboard. The Express workspace API and Python chemistry API must run as separate web services because they need Atlas and the local SQLite evidence database.

## 1. Deploy the backend services

1. In Render, select **New → Blueprint** and choose this repository and its `fsd` branch. Render reads `render.yaml` and creates `synthai-python-api` and `synthai-workspace-api`.
2. Set the workspace service secrets in Render. Do not put them in GitHub or Sites:
   - `MONGODB_URI`: the verified Atlas URI.
   - `FASTAPI_URL`: the deployed `synthai-python-api` URL.
   - `CLIENT_ORIGIN`: `https://synthai-rxn2.rajavreddy-g.chatgpt.site`.
3. Wait for both health checks to pass. Open `<workspace-url>/api/v1/health` and `<python-url>/api/health`.

## 2. Publish the Sites dashboard

Build the `apps/web` source with these public build values. They contain only URLs, never Atlas or account credentials:

```text
VITE_EXPRESS_API_URL=https://<workspace-url>
VITE_PYTHON_API_URL=https://<python-url>
```

Publish the built dashboard to the existing **RXN2 SynthAI Dashboard** Sites project. Its existing custom audience remains in place; application login then controls projects, route comparisons, and research tools.

## 3. Grant the first account

Run the account provisioning command against the hosted workspace service environment:

```text
SYNTHAI_ADMIN_EMAIL=<your email>
SYNTHAI_ADMIN_PASSWORD=<private password of 12+ characters>
npm --prefix apps/server run seed:admin
```

Then sign in on the Sites URL and create a project, save two routes, run a comparison, refresh the page, and confirm the saved project remains.

## Completion checkpoints

- [x] Atlas authenticated ping passed.
- [x] Backend deployment blueprint prepared.
- [x] Frontend supports separate hosted API URLs.
- [ ] Python and Express hosting URLs assigned.
- [ ] Sites published with those URLs.
- [ ] Hosted login and saved project verified.
