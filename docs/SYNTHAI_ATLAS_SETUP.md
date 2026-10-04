# MongoDB Atlas checkpoint

The application uses the existing Express/Mongoose backend. Atlas stores application accounts, projects, route drafts and comparison history. Existing scientific evidence remains in Python/SQLite; configuring Atlas does not migrate or approve scientific evidence.

## Create the free database

1. Sign in to https://cloud.mongodb.com using your own Atlas account.
2. Create or choose a project named SynthAI. Create a **Free (M0)** cluster; verify the selected plan is free before submitting. Skip sample datasets.
3. Under Database Access, create an application database user with `readWrite` permission on the `synthai` database. This credential is separate from application login.
4. Under Network Access, add the current development machine's public IP. For deployment, add the backend host's outbound addresses when that host is configured.
5. Choose Connect → Drivers → Node.js and copy the connection string. Set the private root `.env` `MONGODB_URI` to that URI with the database username and URL-encoded password. Set `MONGODB_DB_NAME=synthai`. Keep the existing signing key. Do not post the URI/password in chat or commit `.env`.

## Verify

From the `fsd` checkout:

```powershell
npm --prefix apps/server run db:check
```

This connects and sends an authenticated ping without changing records. Success is required before marking Atlas configured. If it fails, check cluster readiness, database credentials and IP access. Never treat a local MongoDB success as Atlas verification.

After Atlas passes, provision the application administrator using private `SYNTHAI_ADMIN_EMAIL` and `SYNTHAI_ADMIN_PASSWORD` values and run:

```powershell
npm --prefix apps/server run seed:admin
```

Existing local demo records do not appear in Atlas automatically. An explicit export/import is required if they are needed. Hosted MongoDB credentials belong in backend secrets, never React environment variables or browser bundles. Sites must call a hosted backend capable of MongoDB connections.

## Completion checkpoints

- [ ] Free cluster created in the owner's account.
- [ ] Dedicated database user and allowed backend IPs configured.
- [ ] Private Atlas URI installed.
- [ ] Atlas authenticated ping passed.
- [ ] Administrator provisioned and application sign-in checked.
- [ ] A project and comparison saved and recovered after backend restart.

Connection support and a check command are prepared in source. A live Atlas connection is not complete until the owner's cluster and private credentials are available.
