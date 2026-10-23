# Anonymous Call QR

Owners get a QR sticker. Someone who scans it gets a pre-filled call to your Twilio number. Twilio asks for the owner's 6-digit code and the reason for calling, then forwards the call to the owner. The owner sees only the Twilio number as caller ID. The caller's number is stored only as a salted hash.

```
Scanner phone ──call──▶ Twilio number ──webhook──▶ /voice/incoming  (code: auto-sent ",,123456#" or typed)
                                                   /voice/menu      (1 vehicle · 2 lost item · 3 other)
                         Twilio <Dial callerId=TWILIO_NUMBER> ─────▶ owner's real phone
                                                   /voice/whisper   (reason read to the owner first)
                                                   /voice/status    (no answer / busy / failed → message + hangup)
```

The owner's real number appears in only two places: the `<Dial><Number>` TwiML sent to Twilio, and the admin edit form. It never appears in the QR code, the sticker, URLs, voice prompts or logs. The admin list shows it masked.

## Project layout

```
app/__init__.py        application factory, logging with phone-number redaction
app/config.py          all settings from environment variables
app/models.py          Owner, CallLog, Blocklist (SQLAlchemy)
app/security.py        caller hashing (HMAC-SHA256 + HASH_SALT), X-Twilio-Signature check
app/abuse.py           blocklist + per-caller / per-owner hourly limits
app/voice/routes.py    Twilio webhooks
app/admin/             password-protected admin (owners, QR/stickers, call log, blocklist)
app/services/codes.py  random, unique, non-trivial 6-digit codes
app/services/qr.py     tel: URI, QR PNG, sticker PNG + PDF
wsgi.py                entry point for gunicorn, flask and Vercel
tests/                 pytest suite
```

## Local setup

Requires Python 3.11+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Fill in .env. Generate secrets with:
python -c "import secrets; print(secrets.token_urlsafe(48))"
pytest -q
```

## Deploying to Vercel (recommended)

Vercel gives the app a stable https URL for Twilio. Vercel functions have no persistent disk, so the app needs a hosted Postgres database there. It refuses to start on Vercel with SQLite.

1. **Create a Postgres database.** In the Vercel dashboard, go to your project → Storage → Create → **Neon** (free tier). This sets `DATABASE_URL` for you. Any other Postgres works too; paste its connection string as `DATABASE_URL`. `postgres://` and `postgresql://` URLs are both accepted.
2. **Import the project.** Push this folder to a GitHub repo, then in Vercel choose Add New → Project → import the repo. Use the default settings: Vercel detects the Flask app in `wsgi.py` and installs `requirements.txt` automatically. Don't add a rewrite to a separate entry point, or Flask receives the wrong path and returns 404.
3. **Set environment variables** (Project → Settings → Environment Variables, Production):
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`
   - `SECRET_KEY`, `ADMIN_PASSWORD`, `HASH_SALT`
   - `PUBLIC_BASE_URL`: your production URL, e.g. `https://your-app.vercel.app`, with no trailing slash
   - `DATABASE_URL`, if you didn't use the Storage integration

   Redeploy after changing variables.
4. **Turn off Deployment Protection for production** (Settings → Deployment Protection). Otherwise Vercel puts a login wall in front of the webhooks and Twilio gets an auth page instead of TwiML.
5. **Point Twilio at it.** On your Twilio number, set "A call comes in" to Webhook, `https://your-app.vercel.app/voice/incoming`, HTTP POST.
6. Open `https://your-app.vercel.app/admin` and log in with `ADMIN_PASSWORD`.

Notes:
- Only the production domain works with Twilio. Preview deployments have different URLs, so their signature checks fail.
- Tables are created automatically on first start.
- After a quiet period, the first call may pause a second or two (cold start) before the greeting. Twilio waits up to 15 seconds.
- Logs are under Project → Logs. Phone numbers are never written there.

## Running locally

```bash
flask --app wsgi run --port 5001
```

On macOS, port 5000 is taken by AirPlay Receiver, so use 5001. Locally you can use the admin pages, but Twilio can't reach `localhost`. For real calls, deploy to Vercel or use a tunnel such as ngrok, where your device allows it. If you open the admin over `http://localhost`, set `SESSION_COOKIE_SECURE=0`.

## Running with ngrok

1. Start the app:
   ```bash
   flask --app wsgi run --port 5001
   ```
2. In another terminal, start the tunnel:
   ```bash
   ngrok http 5001
   ```
3. Copy the `https://….ngrok-free.app` URL into `.env` as `PUBLIC_BASE_URL` (no trailing slash), then restart Flask. Signature validation rebuilds the URL from `PUBLIC_BASE_URL`, so this must match exactly what Twilio calls.
4. Open `PUBLIC_BASE_URL/admin` and log in with `ADMIN_PASSWORD`. The session cookie is `Secure` by default, so use the https ngrok URL. To use `http://localhost:5001` instead, set `SESSION_COOKIE_SECURE=0`.

### Configure the Twilio number

Go to Twilio Console → Phone Numbers → Manage → Active numbers → (your number) → Voice Configuration:

- **A call comes in:** Webhook, `PUBLIC_BASE_URL/voice/incoming`, **HTTP POST**.
- **Call status changes** (optional): leave empty, or point it at a logging endpoint. The app does not need it. Call outcomes come from the `<Dial action="/voice/status">` callback, which Twilio calls automatically when the forwarded leg ends. That callback is where "owner did not answer" is handled and duration is recorded.

You don't configure `/voice/menu`, `/voice/whisper` or `/voice/status` anywhere. They are referenced from the TwiML as relative URLs.

## Call flow details

- The QR encodes `tel:<TWILIO_NUMBER>,,<code>%23`. `%23` is the URI-encoded `#`. A literal `#` starts a URI fragment, and many Android dialers silently drop everything after it. `,,` makes the dialer pause about 4 seconds before sending the digits.
- `/voice/incoming` first checks the blocklist and the caller rate limit. It then waits for digits with `<Gather input="dtmf" numDigits="6" finishOnKey="#" timeout="6">`. If no digits arrive, it says "Enter the 6-digit code printed on the sticker" up to 2 more times, then hangs up politely.
- A wrong or inactive code plays "This code is not valid" and ends the call, so each call gets one guess. Combined with 3 calls per hour per caller, that is at most 3 guesses per hour from one number.
- A valid code is followed by the owner rate-limit check, then the reason menu. An invalid choice gets a re-prompt. After 2 failed tries the reason defaults to "other".
- The scanner hears "Connecting you to the owner". Twilio dials the owner with `callerId=TWILIO_NUMBER timeout=30`. The owner hears the whisper ("…about a vehicle problem") before the calls are bridged.
- If the owner is busy or doesn't answer, the scanner hears "The owner is not available right now" and the call hangs up. If the call fails, the scanner hears "We could not connect your call". Any unexpected server error plays "Something went wrong" and hangs up.
- Every webhook checks `X-Twilio-Signature` and returns 403 if it is missing or invalid.

## Admin

`/admin` is protected by `ADMIN_PASSWORD`, a session cookie that expires after 8 hours, and CSRF tokens on every form.

- **Owners:** add, edit, activate or deactivate owners. "New code" replaces the code, so the old sticker stops working.
- **QR / sticker:** download the QR PNG, sticker PNG, or A6 sticker PDF. The sticker shows the QR code, "Scan to call the owner. Your number stays private.", and the Twilio number plus code as a fallback.
- **Call log:** shows time, owner, caller hash, reason, status and duration. "Block caller" adds that hash to the blocklist.
- **Blocklist:** block by phone number (hashed immediately, never stored) or by hash, and unblock.

Abuse limits: 3 calls per hour per caller and 5 per hour per owner. Change them with `MAX_CALLS_PER_CALLER_PER_HOUR` and `MAX_CALLS_PER_OWNER_PER_HOUR`. Rejected calls are counted too, so a caller who keeps redialling stays limited.

Keep `HASH_SALT` stable. Changing it makes existing blocklist entries and rate-limit history stop matching.

## Deployment notes

- Use HTTPS. Twilio signs the exact URL, so `PUBLIC_BASE_URL` must be the public https origin.
- Docker:
  ```bash
  docker build -t anon-call-qr .
  docker run -d --env-file .env -p 8000:8000 -v anoncall-data:/app/instance anon-call-qr
  ```
- Without Docker: `gunicorn --bind 0.0.0.0:8000 --workers 2 wsgi:app` behind a reverse proxy with TLS.
- SQLite is fine for a single server with a persistent volume. For Vercel or multiple instances, set `DATABASE_URL` to Postgres. The `psycopg` driver is already in `requirements.txt`.
- Keep `.env` out of git. Rotate `TWILIO_AUTH_TOKEN` in the Twilio console and update the env var together. Rotating `SECRET_KEY` logs out admins.
- Back up the database. It contains owners' real numbers.

## India-specific notes (read before launch)

- **Trial accounts:** Twilio trial accounts can only call verified numbers, and they play a trial message at the start of calls. Every owner number must be verified in the console while testing. Upgrade before real use.
- **Use an Indian number if possible.** Scanners should ideally dial an Indian number, so it is a normal local or domestic call for them. Indian numbers on Twilio require a regulatory bundle with business KYC documents, and availability and allowed use (inbound voice, forwarding) vary by number type. **Check Twilio's current India number types for voice, and the TRAI rules on call forwarding and caller ID, before you launch. This decides whether this setup is viable at all.**
- **Foreign numbers:** if you use a non-Indian Twilio number, scanners pay international rates. Indian carriers may also flag, spam-label or block those calls.
- **Who pays:** the scanner pays their own carrier for the call to the Twilio number. The project pays Twilio for the inbound minutes and for the forwarded leg to the owner's Indian mobile.
- **QR scanner quirks:** some QR scanner apps and dialers ignore the `,,` pause and the digits in `tel:` URIs. That is why the IVR asks for the code and why the code is printed on the sticker.
- The default voice is `Polly.Raveena` with `en-IN`. Change it with `TTS_VOICE` and `TTS_LANGUAGE`, for example `Polly.Aditi` with `hi-IN` for Hindi prompts. The prompt text itself would also need translating.

## Manual testing checklist

1. `curl -X POST PUBLIC_BASE_URL/voice/incoming` returns **403** because the request is unsigned.
2. In the admin, add an owner with your verified phone (E.164, e.g. `+9198…`). Note the code and download the sticker PDF. Check that it shows the Twilio number and code, and not the owner's number.
3. Scan the QR with a phone camera. The dialer should open with the Twilio number and `,,code#`. Tap Call.
4. The owner's phone rings with the **Twilio number** as caller ID. On answer, the owner hears the reason whisper, then is connected.
5. Call the Twilio number directly without digits. You should hear the code prompt. Stay silent and confirm it hangs up after the retries.
6. Enter a wrong code. You should hear "This code is not valid" and the call ends.
7. Deactivate the owner and call with the right code. You should hear "This code is not valid".
8. Let the owner's phone ring out. The caller should hear "The owner is not available right now".
9. Make 4 calls within an hour from one phone. The 4th should hear the rate-limit message. (Rate-limited calls count too; wait an hour or clear `call_log` to reset.)
10. Block the caller from the call log. The next call should hear the polite blocked message.
11. Check the call log for status, reason and duration. Check the server logs for phone numbers; there should be none.

## Tests

```bash
pytest -q
```

The tests cover code generation and lookup, signature rejection (unsigned, forged and tampered requests), rate limits and the blocklist, TwiML for every step and failure path, and privacy checks. The privacy checks confirm the owner's number appears only inside `<Dial><Number>` and never in other responses, the QR, the sticker or the logs. The Twilio request validator is mocked. No outbound Twilio REST client is needed, because the app only answers inbound calls with TwiML.
