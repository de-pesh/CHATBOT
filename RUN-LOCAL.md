# Run Locally

No Docker for infra — bring your own SQL Server, Redis, and MongoDB.

---

## 1. Configure `.env`

Fill in the three connection strings in `.env`:

```env
DB_CONNECTION_STRING=Server=localhost,1433;UserID=sa;Password=YOUR_SA_PASSWORD;Database=chatbot
REDIS_URL=redis://localhost:6379/0
MONGO_CONNECTION_STRING=mongodb://localhost:27017
```

The database and schema are created automatically on first start.

---

## 2. Auth

Login is on by default (`DISABLE_AUTH=False`). Create a user account before starting:

```powershell
cd main-service-chatbot
.venv\Scripts\python scripts\create_superuser.py `
  --email you@example.com `
  --first-name Your `
  --last-name Name `
  --password yourpassword `
  --role admin
```

> To skip login for local dev, set `DISABLE_AUTH=True` in `.env`.

---

## 3. Run

```powershell
.\run-local.ps1
```

The script installs venv packages (once), checks all connections, optionally starts Tika if `CONVERTER_URL=http://localhost:9998` is set, then launches both services and the UI. Ctrl+C stops everything.

---

## 4. Stop

Press **Ctrl+C** in the terminal running `run-local.ps1`. It stops both services, the UI window, and the Tika container (if started).

---

## 5. UI Dependencies

Only **Node.js 22+** needs to be installed on the machine. `run-local.ps1` runs `npm install` automatically on first start — everything else (React, Vite, MUI, etc.) goes into `FE/node_modules`.

---

## 6. Ports & System Endpoints

| | |
|---|---|
| **http://localhost:3006** | Web UI |
| **http://127.0.0.1:8000** | API (`/docs` for Swagger) |
| **http://127.0.0.1:8000/prompts** | Prompt Management API (GET, PUT, reset) |
| **http://127.0.0.1:8000/files/{id}/status** | File Status API (`ACTIVE`, `DISABLE`, `INACTIVE`) |
