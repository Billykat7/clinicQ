# Testing the patient app as a patient would

How to walk the whole M9 patient journey on a local ClinicQ, as a patient and as the front desk: find a
clinic, join its queue, follow the ticket live, be told "you are next" and "please come in now", change
message settings, cancel, install the app and lose signal. Every step below was run on 15 September 2026;
the steps that were not are marked **not checked**.

## 0. Read this first: what a patient can and cannot do today

**There is no app to download and no store listing.** The patient app is a web app (PWA): the ticket page
at `/t/{token}` *is* the app, and "installing" means the browser's **Add to home screen**. It is offered on
the patient's own ticket page, not on the home page.

**The home page leads a patient to a ticket.** **Find a clinic** goes to `/discover`, then to the clinic's
page, whose **Join the queue** opens the join page (Issue 200). **Your ticket**, beside **Sign in** at the top
of every front-door page and in the footer, opens `/t/`: the patient's open ticket, or the phone sign-in.

| Step | Where | Works in the browser today? |
|---|---|---|
| Find a clinic, see its queues and waits | `/` → **Find a clinic** → `/discover` → `/discover/clinics/{slug}` | Yes |
| Press **Join the queue** | the clinic page | Yes, with `PATIENT_JOIN_ENABLED=true` (off by default: the button is greyed out with the reason) |
| Sign in with a phone number and a code, answer the messages question, choose a queue | `/discover/clinics/{slug}/join` | Yes |
| Follow the ticket, message settings, cancel, push, install, offline, reception code | `/t/{token}` | Yes |
| Get back to a ticket, or sign in to find one joined in another browser | **Your ticket** on the home page, or the installed app, both `/t/` | Yes |
| Front desk: board, *Call next*, walk-ins, Find ticket | `/dashboard` | Yes |
| **Book an appointment** for a time | | **Not built.** Appointments are M11 (Issues 80 and 81). Today a patient joins a queue that is running now |
| Join by USSD or WhatsApp | | **Not built.** M10 |

## 1. Set up a local ClinicQ with demo data

From a working checkout ([QUICKSTART.md](../QUICKSTART.md), section 2):

1. In `.env`, set these (all are in `.env.example`, commented out):

   ```dotenv
   ENVIRONMENT=development
   SMS_ENABLED=true
   PATIENT_JOIN_ENABLED=true
   ```

   - `ENVIRONMENT=development` serves `/dev/outbox`, where every "SMS" lands instead of a phone: the
     sign-in codes and the queue messages. No SMS account is needed, and nothing is sent.
   - `SMS_ENABLED=true` lets a patient ask for a sign-in code. Off (the default), the code request answers
     `503 patients.otp.sms_disabled` and the queue sends no SMS.
   - `PATIENT_JOIN_ENABLED=true` enables the clinic page's **Join the queue** button and the join page's
     form. Off (the default), the button is greyed out and the join page says joining from a phone is not
     switched on.

2. Optional, for the **Tell me on this phone when it is my turn** button: make a VAPID key pair and paste
   its three lines into `.env` ([WEB_PUSH.md](WEB_PUSH.md), section 1). Without the keys the button is not
   shown and messages fall back to SMS.

   ```bash
   python -m scripts.generate_vapid_keys --subject mailto:ops@clinicq.example
   ```

3. Start the database, create the schema, and seed the roles and the demo world:

   ```bash
   make db-up && make migrate-up && make seed-rbac && make seed-dev-data
   ```

4. Run the app:

   ```bash
   make run
   ```

The demo world is twenty-six real clinics in Gauteng, KwaZulu-Natal and Cape Town, with their queues and
opening hours. In Cape Town, search for Woodstock, Salt River, Delft or Khayelitsha, or use your location.
The walkthrough uses
**Hillbrow Community Health Centre** (`hillbrow-chc`, open 07:00 to 19:00) and its **General
consultation** queue, which takes remote joins. Outside its hours a join is refused with the reason; pick
a clinic whose page says **Open now**.

`make seed-dev-data` prints four staff accounts, all with the password `clinicq-dev-only`. All but the
platform admin work at Hillbrow:

| Account | Use it for |
|---|---|
| `reception@clinicq.example` | The board, *Call next*, walk-ins, **Find ticket** |
| `nurse@clinicq.example` | The consulting room view |
| `manager@clinicq.example` | The clinic's settings |
| `platform-admin@clinicq.example` | `/admin/notifications` (delivery by transport) and `/admin/notification-templates` |

## 2. Use two browser windows that do not share cookies

A patient and a receptionist are two different sessions. Use **two Chrome profiles**, or Chrome and
another browser. Not two incognito windows: Chrome's incognito windows share one session.

- **Patient window:** open DevTools and turn on the device toolbar (a phone such as a Pixel 7), so the
  ticket page is drawn at phone width.
- **Reception window:** an ordinary desktop window.

For a second patient, use a third profile or browser: each browser holds one patient's sign-in.

## 3. Journey: the patient finds a clinic and joins

In the **patient window**:

1. Optional: to see people ahead of the patient, first issue two or three walk-ins from the reception
   window (section 4, steps 1 and 2). A walk-in takes the next number, so walk-ins issued after the patient
   joins stand behind them.
2. Open `http://127.0.0.1:8000`, press **Find a clinic**, search for Hillbrow and open the clinic. Check the
   page shows **Open now**, the queues and a wait range for each.
3. Press **Join the queue**. The join page opens at **Sign in with your phone number**, saying why the
   number is asked for.
4. Type any South African mobile number (for example `082 555 0101`; nothing is sent) and press **Send me a
   code**. The page moves to **Enter the code**, and **Send a new code** waits out a minute, counting down.
5. Open `http://127.0.0.1:8000/dev/outbox` in another tab: the newest message is `BK ClinicQ: your sign-in
   code is 123456 …`. That is the patient's "phone". Type the code and press **Sign in**.
6. **Messages about your turn** asks the notifications question in the same words as every channel. Choose
   **Yes, message me** and press **Continue**. Choosing **No, thank you** instead still gives a ticket, and
   no queue message is sent.
7. **Choose a queue** lists only the queues that take joins from a phone, each with its people waiting and
   wait range. Choose **General consultation**, optionally type a reason, and press **Join the queue**.
8. The ticket page opens at `/t/{token}`. **Check:** the ticket number, "You are number N in line", people
   ahead, an estimated wait range, **Live**, and "Updated … s ago" counting up. Below it: **Directions to
   the clinic**, **Share this ticket**, the **Keep your ticket one tap away** install box, **Cancel my
   ticket**, **Message settings**, and **At reception** with a QR and a spoken code such as `FPY-FC7`.
9. **The browser asked for nothing by itself:** no notification prompt and no install banner.

**Try the refusals too.** Each shows the API's own sentence under the form:

- A number that cannot be read (`123`): "Enter a mobile number such as 082 123 4567 or +27 82 123 4567."
- A wrong code: "That code is not right." with the tries left.
- **Use a different number**, then the same number again within a minute: "Too many code requests. Try again
  shortly.", with **Send me a code** held and counting down.

**Back on the join page, still signed in,** the page starts at **Choose a queue**. Joining the queue the
patient already holds opens that ticket, never a second one.

## 4. Journey: the front desk calls, the patient is told

In the **reception window**:

1. Open `http://127.0.0.1:8000`, press **Sign in** and sign in as `reception@clinicq.example`. `/dashboard`
   opens Hillbrow's dashboard.
2. Open **Walk-in** and issue walk-ins to General consultation as needed (no phone number needed).
3. Open the **Board** and press **Call next** on General consultation until the patient is next.

In the **patient window**, check each change arrives without a reload:

| When | The ticket page shows | `/dev/outbox` gets |
|---|---|---|
| One person ahead is called | **You are next** filling the top of the screen; the tab title is "You are next · A002" | `ticket A002 at …, you are next. Please be ready at …` |
| The patient is called | **Please come in now**, "Go to General consultation"; the tab title is "Come in now · A002" | `ticket A002, please come in now to …` |

Open `http://127.0.0.1:8000/dev/outbox` in any window to read the messages, newest first. Each is cut to
one SMS segment, so long clinic and queue names end in `...`: that is intended.

**Not live:** stop following updates (DevTools → Network → **Offline** for a few seconds, then back
online). The page shows **Not live** and the age of its data, then catches up.

## 5. Journey: what else the patient can do on the ticket

- **Share the link.** Press **Share this ticket** (or copy the address) and open it in a window with no
  patient session, as the person driving them would. They see the place in line and the calls, but **no**
  cancel, message settings, install box or push button: those belong to the patient who joined.
- **Message settings.** Open **Message settings**: stop all messages, a preferred channel, language, quiet
  hours and which messages to skip. Save, then have reception call again. In quiet hours, "you are next",
  "please come in now" and "called again" still come; the others are held. With **Stop all messages**, none
  come.
- **Notifications on this phone** (needs the VAPID keys from section 1). Press **Tell me on this phone when
  it is my turn** and allow notifications. Chrome allows web push on `localhost` and `https://` only, so
  use `localhost`, not a network address. When reception calls, the notification names only the ticket
  number and the clinic. Push delivery on a desktop browser was **not checked** in this walkthrough; a real
  Android phone is in [WEB_PUSH.md](WEB_PUSH.md), section 4.
- **Cancel.** Press **Cancel my ticket**, then **Yes, cancel my ticket**. The ticket shows **Ended**, "This
  ticket was cancelled and is no longer in the queue", and leaves the reception board.
- **At reception.** In the reception window open **Find ticket** (shortcut `f`) and type the patient's code
  (`FPY-FC7`), or scan the QR with a desk scanner ([RECEPTION_LOOKUP.md](RECEPTION_LOOKUP.md)). It opens
  that ticket. A code works only at its own clinic on its own day.

## 6. Journey: install the app and lose signal

Still in the **patient window**, on the patient's own ticket page:

1. **Install.** Press **Add to home screen** in the install box (Chrome also puts an install icon in the
   address bar). The app opens in its own window. **Not now** hides the box on that browser for good; clear
   the site's data to see it again.
2. **Open it again from the installed app.** It opens at `/t/`, which goes straight to the open ticket.
3. **Check the worker.** DevTools → **Application** → **Service workers** shows `/patient-sw.js` active for
   scope `/t/`. **Cache storage** shows `clinicq-patient-shell-<version>` and `clinicq-patient-tickets`.
4. **Lose signal.** Tick **Offline** under Service workers (or stop `make run`) and reload. The page becomes
   **Offline**: "No connection. This is how your ticket stood the last time this phone could check", the
   ticket number, the last place in line, the reception code and QR, and "Last updated … ago" counting up.
5. **Get signal back.** Untick Offline (or start the app again). The page returns to the live ticket by
   itself within 15 seconds, or at once on **Try again**.

Checked on 15 September 2026: steps 3 to 5 in Chromium (the server stopped, then the ticket reloaded to the
offline page). Pressing the install button was **not checked** by hand; Chromium's installability check and
Lighthouse 11.7.1's PWA category are covered in [PATIENT_APP.md](PATIENT_APP.md), section 4.

## 7. On a real phone

The service worker, install and push need a secure page: `https://`, or `localhost` on the phone itself.
Opening the laptop's network address (`http://192.168.x.x:8000`) on a phone shows the ticket page, but
**nothing installs, nothing works offline and no push arrives** there.

**Android, over USB (recommended).**

1. Turn on USB debugging on the phone and connect it.
2. On the laptop, open `chrome://inspect/#devices` in Chrome → **Port forwarding** → add port `8000` to
   `localhost:8000`, and tick **Enable port forwarding**.
3. On the phone, open `http://localhost:8000` in Chrome. It is now a secure page.
4. Sign in and join on the phone, as in section 3. Read the code from `http://localhost:8000/dev/outbox` on
   the laptop.
5. Follow sections 4 to 6 on the phone, with reception on the laptop. For "lose signal", turn on aeroplane
   mode.

**Not checked:** this USB route end to end. The first real Android install and push are still to be
recorded in [PATIENT_APP.md](PATIENT_APP.md) and [WEB_PUSH.md](WEB_PUSH.md).

**iPhone.** Needs a real `https://` address, for example a staging deployment or a tunnel to the laptop.
Open the ticket in Safari → Share → **Add to Home Screen**. A Home Screen app keeps its own storage, apart
from Safari's, so it opens at "No open ticket on this phone". Its **Sign in with your phone number**, with
the number the patient joined with, opens their ticket inside the app. **Not checked** on an iPhone; the same
start-page sign-in is checked in Chromium (`tests/e2e/patient/test_join_page.py`).

## 8. When something does not work

| You see | Why, and what to do |
|---|---|
| "Sign-in by SMS code is not available here." on the join page | `SMS_ENABLED` is off. Set `SMS_ENABLED=true` and restart |
| `404` on `/dev/outbox` | `ENVIRONMENT` is not `development` |
| "Too many code requests. Try again shortly." | Within 60 seconds of the last code for that number, or too many codes from this address. Wait for the countdown, or use another number |
| A refusal after **Join the queue** on the join page | The clinic closed, the queue filled, or too many joins came from this address: the sentence says which |
| **Join the queue** is greyed out, or the join page says you cannot join from your phone | `PATIENT_JOIN_ENABLED` is off, the clinic is closed, or no queue takes joins from a phone (the reason is on the page) |
| "This page needs JavaScript to sign you in" | JavaScript is off in that browser. The front desk can still issue a walk-in |
| No install box, push button or cancel | Not the patient who joined (a shared link or another window), or **Not now** was pressed. No push button also means the VAPID keys are not set |
| "Notifications are off for this site, so we will send you an SMS instead" | The browser has blocked notifications for the site. Allow them in the address bar's site settings and reload |
| No **You are next** message in `/dev/outbox` | The patient answered **No, thank you** to the messages question, chose **Stop all messages**, or the message is held by quiet hours |
| An edited CSS or JavaScript file does not show on a `/t/` page | The shell is served from cache first. Reload twice, or tick **Update on reload** in DevTools ([PATIENT_APP.md](PATIENT_APP.md), section 3) |

---

**Refs:** [PATIENT_APP.md](PATIENT_APP.md) · [WEB_PUSH.md](WEB_PUSH.md) · [SMS_GATEWAY.md](SMS_GATEWAY.md) ·
[RECEPTION_LOOKUP.md](RECEPTION_LOOKUP.md) · [Release v0.9.0](../GITHUB/RELEASES/RELEASE_v0_9_0.md) ·
`src/web/join.py` · `src/web/ticket.py` · `src/web/discover.py` · `src/modules/queue/router.py` ·
`src/modules/patients/router.py`
