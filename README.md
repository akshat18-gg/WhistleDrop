# WhistleDrop

A backend for confidential reporting, built for the GDG on Campus SRM recruitment task. Anyone can report a problem without an account and without saying who they are. They get a case code to follow up with, and moderators review and manage the reports. There's no frontend. You use it through Swagger at `/docs`, or with curl.

Live docs: https://whistledrop.onrender.com/docs. It's on Render's free plan, so the first request after a quiet spell takes about a minute while it wakes up. The free plan also wipes the disk whenever the service sleeps, restarts or redeploys, so the database starts empty each time.

## Setup

You need Python 3.11 or newer.

```bash
git clone https://github.com/akshat18-gg/whistledrop.git
cd whistledrop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in `JWT_SECRET` and `CASE_CODE_SECRET`. Run this once for each and paste the output:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then fill in `ENCRYPTION_KEY` with the output of this:

```bash
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

The two secrets need at least 32 characters, and the key has to be a valid Fernet key. If any of them is missing or wrong, the app refuses to start and tells you which one. Don't lose the key. Without it, the stored reports can't be read.

Create a moderator. It asks for a password of at least 10 characters:

```bash
python -m app.cli create-moderator alice
```

Run the server and open http://localhost:8000/docs:

```bash
uvicorn app.main:app --no-access-log --no-server-header
```

Keep both flags. Uvicorn's access log records every client's IP address, and the `server` header advertises what the server is running. To lock a moderator out, run `python -m app.cli deactivate-moderator alice`. Any token they already have stops working too.

To deploy your own copy, go to New > Blueprint in the Render dashboard and pick this repo. [`render.yaml`](render.yaml) generates the secrets and the encryption key, and Render asks you for `MODERATOR_PASSWORD`. The free plan has no shell, so the start command creates the `demo` moderator from that password every time the service boots.

## Tests and the demo script

Run `pytest`. Every test gets its own fresh SQLite file, so the tests never touch your real database.

With the server running, open a second terminal and run:

```bash
./scripts/demo.sh
```

It tells the whole story with curl. A report is submitted, the reporter checks on it, a moderator logs in, filters, reviews it and leaves an internal note, tries a move that isn't allowed, resolves the report and closes the case, and the reporter looks one last time. It pretty-prints with `jq` if you have it and falls back to Python otherwise.

The OpenAPI spec is saved in [`docs/openapi.json`](docs/openapi.json), so you can read it without running anything. If you change a route, regenerate it. A test fails if the saved spec doesn't match the code.

```bash
python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" > docs/openapi.json
```

## Endpoints

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | `/health` | anyone | Returns `{"status": "ok"}` |
| GET | `/api/categories` | anyone | Lists the five categories |
| POST | `/api/reports` | anyone | Submits a report and returns the case code, once |
| GET | `/api/reports/status` | anyone with a code | Status and the updates meant for the reporter. The code goes in the `X-Case-Code` header |
| POST | `/api/reports/evidence` | anyone with a code | Attaches a JPEG, PNG or PDF of up to 5 MB, sent as the raw body. Up to 5 per report |
| POST | `/api/auth/login` | moderators | Returns a bearer token that lasts 60 minutes |
| GET | `/api/moderator/reports` | moderators | Lists reports. Filters: `status`, `category`, `q`, `from`, `to`, `sort`, `page`, `page_size` |
| GET | `/api/moderator/reports/{id}` | moderators | The full report and every update, internal notes included |
| PATCH | `/api/moderator/reports/{id}` | moderators | Changes the status, with an optional note |
| POST | `/api/moderator/reports/{id}/updates` | moderators | Adds a note, either for the reporter or internal |
| POST | `/api/moderator/reports/{id}/close` | moderators | Closes a RESOLVED or DISMISSED case for good |
| GET | `/api/moderator/reports/{id}/evidence/{file_id}` | moderators | Downloads an attached file |
| GET | `/api/moderator/stats` | moderators | Counts by status and category, how many are open and how many are closed |

## How anonymity is kept

The main idea is that the database never holds anything that points to a person. If it isn't stored, it can't leak, and no moderator can look it up.

- **No identity columns.** The reports table has no column for a name, email, phone number, IP address, user agent or device. A test checks the exact list of columns.
- **No accounts, cookies or sessions** for reporters.
- **The case code is never stored.** I keep only `HMAC-SHA256(secret, code)`. The raw code is in the submit response once, and is never logged or shown again, to moderators either. A test searches the raw database file for it.
- **The code goes in a header**, not the URL. URLs end up in server logs, proxy logs and browser history.
- **Dates, not times.** `submitted_on` is a UTC date. `updated_at` stays empty until a moderator changes something, so it can't hold the submission time either.
- **Random ids.** Reports use UUID4. The table is also `WITHOUT ROWID`, because SQLite otherwise keeps a hidden counter on every row that records insert order. Lists sort by date and then by the random id, so the order within a day means nothing.
- **Extra fields are rejected.** If someone sends `"email": "..."` with their report, they get a 422. Nothing is quietly accepted.
- **The request log is minimal.** Each request gets one line: method, route template, status and duration, like `POST /api/reports 201 4ms`. There's no IP, header, body, query string or timestamp. Unknown paths are logged as `(no matching route)`, in case someone pastes their code into the URL. SQLAlchemy runs with `hide_parameters=True`, so report text can't end up in an error traceback.
- **The reporter view is small.** It doesn't include the description, so someone who finds a code sees a status, not the report. Updates don't say which moderator wrote them, and internal notes don't show at all.
- **Headers.** API responses send `Cache-Control: no-store`. Every response sends `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`.
- **Encrypted at rest.** The description and evidence link are encrypted with Fernet before they reach the database, so a copied database file is useless without `ENCRYPTION_KEY`. Fernet normally stamps every token with the time it was made, in plain text. That would put the exact submission time back into the database, so I stamp every token with 0 instead.
- **Evidence files lose their metadata.** Photos can carry the GPS location, the phone model and the time they were taken. Instead of deleting those tags one by one, I rebuild each image from its pixels alone, so nothing else comes along. The file type is decided by its first bytes, not its name. Files are stored encrypted, under random names, outside any public folder, with the modified time set to 1970, and only moderators can download them.
- **Rate limits without keeping IPs.** A client can submit 10 reports an hour, check status 30 times a minute and try logging in 10 times a minute. The limiter is the one place that reads the client's IP. It hashes the IP with a random key that exists only in memory and changes on every restart, and the counters live in memory too. Nothing about the client is logged or stored. Guessing an 80-bit code is already hopeless, so the limits are mostly about spam. They also count failed status lookups, so nobody can make unlimited guesses. On Render the app sits behind a proxy, so uvicorn takes the client address from `X-Forwarded-For`. A determined spammer could fake that header, so the limits only stop casual spam.

Some things the backend can't protect:

- **What the reporter writes.** If the description says "I'm the only TA in the lab on Tuesday nights", hashing doesn't help. The submit response reminds them of this.
- **Where the evidence link points.** A Google Drive or OneDrive link can show the owner's name and email to anyone who opens it. Reporters should use a link that isn't tied to their account.
- **PDF metadata, and what's in the picture.** Pillow can't clean PDFs, and a PDF can carry the author's name. The upload response warns about this and suggests screenshots instead. No cleaning helps if the photo itself shows your desk or your reflection.
- **Logs kept by the host or the network.** Apart from the rate limiter, my code never reads `request.client` or `X-Forwarded-For`. But a hosting provider, reverse proxy or college network in front of it can keep its own logs with IP addresses and exact times. Most hosts also timestamp everything an app prints. Someone who needs strong anonymity should report from a network that isn't theirs, or through Tor.

## Example requests and responses

Submit a report:

```bash
curl -X POST localhost:8000/api/reports -H "Content-Type: application/json" \
  -d '{"category": "security", "description": "The server room door in Tech Park is left unlocked every night after 9pm."}'
```
```json
{
  "case_code": "WD-EDAF-FPA0-SQKR-XJB5",
  "status": "SUBMITTED",
  "submitted_on": "2026-10-06",
  "note": "Save this code now. It's the only way to check on your report and it can't be recovered.",
  "privacy_tip": "We don't know who you are, but details in your description that only you would know can still point back to you."
}
```

Check on it. Lowercase or no dashes works too:

```bash
curl localhost:8000/api/reports/status -H "X-Case-Code: WD-EDAF-FPA0-SQKR-XJB5"
```
```json
{
  "category": "SECURITY",
  "status": "UNDER_REVIEW",
  "submitted_on": "2026-10-06",
  "closed": false,
  "updates": [
    {"status": "UNDER_REVIEW", "message": "Thanks for reporting this. We have started looking into it.", "date": "2026-10-06T20:33:52Z"}
  ]
}
```

Log in as a moderator:

```bash
curl -X POST localhost:8000/api/auth/login -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "your-password"}'
```
```json
{"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer", "expires_in": 3600}
```

List SECURITY reports that are still SUBMITTED and mention "server room":

```bash
curl "localhost:8000/api/moderator/reports?category=SECURITY&status=SUBMITTED&q=server%20room" \
  -H "Authorization: Bearer $TOKEN"
```
```json
{
  "items": [
    {
      "id": "2770cbd8-e85c-471f-b01d-b1206ae7cac1",
      "category": "SECURITY",
      "status": "SUBMITTED",
      "submitted_on": "2026-10-06",
      "closed": false,
      "description_preview": "The server room door in Tech Park is left unlocked every night after 9pm.",
      "has_evidence_url": false
    }
  ],
  "page": 1, "page_size": 20, "total": 1
}
```

Move it to UNDER_REVIEW with a note the reporter will see. Add `"visible_to_reporter": false` to keep the note internal. The response is the full report with all its updates.

```bash
curl -X PATCH localhost:8000/api/moderator/reports/2770cbd8-e85c-471f-b01d-b1206ae7cac1 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "UNDER_REVIEW", "note": "Thanks for reporting this. We have started looking into it."}'
```

The reporter can attach a photo or PDF with their code. Images come back from the server without their metadata:

```bash
curl -X POST localhost:8000/api/reports/evidence -H "X-Case-Code: WD-EDAF-FPA0-SQKR-XJB5" \
  -H "Content-Type: application/octet-stream" --data-binary @photo.jpg
```

Every error has the same shape. Here's a report trying to skip review:

```json
{
  "error": {
    "code": "INVALID_STATUS_TRANSITION",
    "message": "Can't move a report from SUBMITTED to RESOLVED. It has to be UNDER_REVIEW first.",
    "details": null
  }
}
```

For validation errors, `details` lists each problem, like `[{"field": "description", "problem": "Must be at least 20 characters, not counting spaces at the ends."}]`.

## Status workflow

```
SUBMITTED ──> UNDER_REVIEW ──┬──> RESOLVED  ──> closed
                             └──> DISMISSED ──> closed
```

These three moves are the only ones allowed, and they live in one dict, `ALLOWED_MOVES` in `app/models.py`. Anything else gets a 409. That includes skipping review, going backwards, setting the same status again, and changing a RESOLVED or DISMISSED report. The tests try all 16 from/to pairs.

Every status change adds an entry to the report's history. If the moderator doesn't write a note, the entry says something like "Status changed to Under review." The change itself is a conditional update, `UPDATE ... WHERE id = ? AND status = <what I just read>`. If two moderators act on the same report at the same moment, the second one gets a 409 instead of silently overwriting the first.

Closing is a separate step. Once a report is RESOLVED or DISMISSED, a moderator can close it. After that nothing on it can change: no status change, no new notes. The reporter still sees the final status, with `closed: true`.

## Design decisions and assumptions

- **HMAC instead of plain SHA-256.** With a plain hash, anyone who copied the database file could test guesses offline. With HMAC they also need the secret, which lives in the environment and not in the database. I didn't use a slow hash like argon2 for the codes. Lookups need the same output every time, and slow hashes exist to protect weak passwords, while an 80-bit random code isn't weak.
- **80-bit codes.** 16 characters from Crockford base32, generated with `secrets`. There are about 10^24 possible codes, so guessing one isn't realistic. The alphabet leaves out I, L, O and U so nobody misreads them, and the lookup forgives lowercase, missing dashes, spaces, a missing `WD-`, and O typed instead of 0.
- **The code goes in a header** because it works like a password, and passwords don't belong in URLs.
- **Date only, in UTC.** An exact time can be matched to whoever was at their desk at 10:42. I used UTC so the date doesn't depend on the server's timezone. One side effect: a report sent at 2am in India is dated the previous day.
- **UUIDs.** Sequential ids would show how many reports exist and in what order they arrived.
- **No signup endpoint.** An open signup would let anyone become a moderator and read every report. Moderators are created from the command line by whoever runs the server.
- **The same login error for everything.** A wrong username, a wrong password and a deactivated account all return the same 401. For a username that doesn't exist, I still check the password against a dummy hash, so the response time doesn't give it away either.
- **The description isn't shown back to the reporter.** They wrote it, so they don't need it. If someone else finds their code, they learn the status and nothing more.
- **409 for moves that aren't allowed.** The request is well formed, so 422 doesn't fit, and the report exists, so 404 doesn't fit either. It conflicts with the report's current state, which is exactly what 409 means.
- **Case-insensitive categories and statuses.** `"security"` works and is stored as `SECURITY`. The same goes for status values and list filters.
- **No shortcut from SUBMITTED to DISMISSED.** Even obvious spam goes through UNDER_REVIEW first. It costs one extra step, but every dismissal means someone actually looked at the report.
- **Closing adds a visible update**, "This case is now closed.", which also records which moderator closed it.
- **Search runs in Python.** The database can't search encrypted text. So the status, category and date filters run in SQL, and the text search decrypts what's left and checks it in Python. That's fine for a campus-sized list of reports. At a much bigger scale, I'd need a search index, and that would leak some of the text.
- **SQLite.** It's one file and needs no setup. The conditional updates mean two moderators can't overwrite each other's changes. On Render's free plan the file doesn't last, which is fine for a demo but not for real use.

## Screenshots

![Swagger UI](docs/screenshots/01-swagger.png)
![Submitting a report](docs/screenshots/02-submit-report.png)
![Reporter checking status](docs/screenshots/03-reporter-status.png)
![Moderator login](docs/screenshots/04-moderator-login.png)
![Filtered report list](docs/screenshots/05-filtered-list.png)
![Changing a report's status](docs/screenshots/06-status-change.png)
![A refused status change](docs/screenshots/07-invalid-transition-409.png)
![Tests passing](docs/screenshots/08-tests-passing.png)

## Not done yet

- Key rotation. If `ENCRYPTION_KEY` leaked, I'd want to re-encrypt everything with a new key, using Fernet's `MultiFernet`.
- Cleaning PDF metadata too, which needs a PDF library. Also HEIC photos from iPhones, which Pillow can't open on its own.
- Postgres instead of SQLite if this ever had real traffic, with Alembic migrations instead of `create_all`.
