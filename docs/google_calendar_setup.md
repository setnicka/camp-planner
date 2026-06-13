# Google Calendar sync — setup

Camp Planner can sync a camp's schedule **two-way** with a Google calendar via
a **Google service account** (one per deployment).

Set the service account up once (Part A) and then connect each camp to the
selected calendar by sharing that calendar (in read+write mode) with the service
account and setting the calendar's ID in the camp settings (Part B).

Leave `GOOGLE_SERVICE_ACCOUNT_JSON` unset to disable the feature entirely.

## Part A — Deployment setup (once)

1. **Cloud project + API** — in the [Google Cloud Console](https://console.cloud.google.com/),
   create or pick a project and enable the **Google Calendar API** (APIs & Services → Library).
2. **Service account** — APIs & Services → Credentials → Create credentials → Service
   account. Note its email (like `camp-planner@<project>.iam.gserviceaccount.com`); it's
   needed in Part B. No consent screen, scopes, or domain delegation required.
3. **Key** — open the service account → Keys → Add key → JSON. Treat the downloaded file
   as a secret.
4. **Configure & restart** — point Camp Planner at the key (path or inline JSON; see
   [`.env.example`](../.env.example)), then restart:

   ```bash
   GOOGLE_SERVICE_ACCOUNT_JSON=/etc/camp-planner/service-account.json
   ```

5. **Run scheduled jobs** — queued changes are pushed by `flask sync-google`.
   Run it on a timer (drains once and exits) **or** as a single long-lived
   sidecar (not per gunicorn worker):

   ```bash
   * * * * *  flask --app wsgi sync-google      # cron / systemd timer, or…

   flask --app wsgi sync-google --loop 60       # one sidecar process
   ```

   Without it, outbound only happens when a user clicks **Sync now**. Inbound
   is always manual (Part B).

## Part B — Connect a camp (once per camp, as a user with edit rights)

1. **Create a dedicated secondary calendar** in
   [Google Calendar](https://calendar.google.com/), e.g. "Summer Camp 2026". Don't use your
   primary calendar — a service account can't write to it.
2. **Share it with the service account** — calendar Settings → *Share with specific people*
   → add the service-account email with **"Make changes to events"** (read-only
   access is not enough).
3. **Copy the Calendar ID** — calendar Settings → *Integrate calendar* → **Calendar ID**
   (`…@group.calendar.google.com`). Not the *Secret address in iCal format*, which is
   read-only and won't work.
4. **Connect** — camp → **Settings** (Nastavení) → **Google Calendar** tab → paste the
   Calendar ID → **Connect** (Připojit).

From then on, slot/activity edits push to Google automatically (via the scheduled
background job or **Sync now** button). The **Load changes from Google** button
(Načíst změny z Google) shows a reviewable checklist of changes made in Google
— new, moved, and deleted events plus organizer and category edits. This can
also be used to perform the initial import.

### Field mapping

| Camp Planner          | Google event                                                           |
| --------------------- | ---------------------------------------------------------------------- |
| activity title        | summary                                                                |
| garants / helpers     | location (in format `K+M, A, B, C` – first two garants, other helpers) |
| a slot's attendants   | description                                                            |
| category colour       | event colour (nearest of Google's 11; no category = default)           |
| slot start / end      | event start / end (camp time zone)                                     |

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Connect fails — "calendar not accessible" (*Kalendář není přístupný*) | Calendar not shared with the service account, or too few rights — re-share with **"Make changes to events"**. |
| Connected, but changes never reach Google (panel shows failed ops) | Calendar shared **read-only** (connect only checks read). Re-share with **"Make changes to events"**, then **Sync now**. |
| Connect rejects the pasted value | You pasted the iCal/secret address — use the **Calendar ID** instead. |
| Nothing syncs outbound | `flask sync-google` isn't scheduled (A5), or the key isn't configured (A4). |
| "Google Calendar" tab missing in settings | `GOOGLE_SERVICE_ACCOUNT_JSON` unset, or you're not an editor of the camp. |
