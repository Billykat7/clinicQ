# Web push: keys, platforms and the Android check

Web push tells a patient "you are next" and "please come in now" on their phone for free. It is the first
transport ClinicQ tries (Issue 63). A patient who has not allowed notifications, or whose phone cannot show
them, gets an SMS instead. This page covers setting up the keys, what each kind of phone really does, and
the check on a real Android phone that is still to be done.

## 1. The keys

Web push signs every message with a **VAPID key pair**. Generate one pair **per environment** (staging,
production), once:

```bash
python -m scripts.generate_vapid_keys --subject mailto:ops@clinicq.example
```

It prints three lines. Put them where that environment keeps its secrets (the server's `.env` or the
GitHub environment's secrets), and nowhere else:

| Setting | What it is | Secret? |
|---|---|---|
| `WEB_PUSH_VAPID_PUBLIC_KEY` | Given to every browser that subscribes | No (it is in every subscription) |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | Signs every message | **Yes.** Never commit it, paste it in a chat or log it |
| `WEB_PUSH_VAPID_SUBJECT` | How a push service contacts the operator: `mailto:` or `https:` | No |

The three are set together or not at all; the app refuses to start with only some of them. With none set,
web push is off: the ticket page offers no button and every patient message goes by SMS.

**Keep the pair.** A new key pair invalidates every subscription made with the old one: every patient would
have to allow notifications again. Replace it only if the private key has leaked. After that, the push
services answer `404` or `410` to the old subscriptions, and ClinicQ removes each one at its first failed send.

`WEB_PUSH_ALLOWED_HOSTS` lists the push services the server may send to (Google, Mozilla, Apple, Microsoft).
The server POSTs to whatever endpoint a browser gives it, so no other host is accepted. Leave it alone unless
a browser vendor adds a push service. `WEB_PUSH_REQUIRE_HTTPS` must stay `true` outside local tests.

## 2. What a patient sees

1. The patient joins a queue and lands on their ticket page.
2. **Nothing asks for permission on its own.** The page shows a button: *Tell me on this phone when it is
   my turn*. The browser's permission prompt appears only when the patient presses it.
3. If the patient allows it, the phone is subscribed. It shows *You are next* and *Please come in now* even
   when the page is closed, and tapping either opens the ticket page.
4. If the patient declines, the page says *we will send you an SMS instead*, and that is what happens.

A push says only the ticket number and the clinic's name, for example *Please come in now: Ticket A043 at
Zola Community Clinic.* The queue name, room, reason and patient name are never in it, because a lock screen
is read by whoever picks the phone up.

## 3. What each platform does

| Platform | Web push | Notes |
|---|---|---|
| **Android, Chrome** | Yes, in a normal tab and from a Home Screen app | The main target. Some phone makers' battery savers (for example "sleeping apps" or aggressive optimisation) can delay notifications for Chrome. See the check in section 4 |
| **Android, Firefox or Samsung Internet** | Yes | Same flow; not yet checked on a phone |
| **Desktop Chrome, Edge, Firefox** | Yes | Useful for testing; patients rarely wait at a desktop |
| **iPhone and iPad (iOS/iPadOS 16.4 or later)** | **Only from a web app added to the Home Screen** | See below |
| **iPhone, Safari tab, or iOS before 16.4** | **No** | The page says to add it to the Home Screen; until then the patient gets SMS |

**iPhones, honestly.** ClinicQ does not claim the same behaviour on iOS as on Android:

- Apple allows web push only for a site the patient has **added to the Home Screen** and opened from there.
  Opening the ticket link in Safari is not enough.
- Adding a site to the Home Screen as a web app needs the **web app manifest**, which the PWA shell added
  (Issue 69, [PATIENT_APP.md](PATIENT_APP.md)). WebKit documents a Home Screen web app as keeping its own
  storage, apart from Safari's, so the app may open without the patient's sign-in or the ticket they followed
  in Safari, and the push button is offered only to a signed-in patient on their own ticket. **Web push from
  an iPhone Home Screen app has not been tried on a device**; until it has, plan on SMS for iPhone patients.
- Adding to the Home Screen is a manual step in Safari's share menu. Many patients will not do it, so SMS
  stays the realistic channel for most iPhone users.
- iOS decides when a notification is shown: Focus modes, Low Power Mode and notification summaries can hold
  it back. ClinicQ cannot tell whether a push was **seen**. A push service accepting a message is recorded as
  `sent`, and web push has no delivery receipt.
- iOS ignores some notification options the service worker sets (for example repeated alerts for the same
  ticket), so "please come in now" may not buzz a second time the way it does on Android.
- Apple has changed Home Screen web app rules for particular regions before. Re-check Apple's release notes
  before relying on iOS push in a new market.

## 4. The check on a real Android phone (not done yet)

The automated tests use a local push service and prove the message is encrypted, signed, sent within 5
seconds of *Call next* and readable only by the browser. They cannot prove that a phone buzzes. That needs a
person, a phone and a deployment reachable over HTTPS, because Chrome allows push only on `https://` or
`localhost`.

**You need:** an Android phone with Chrome, on mobile data (not the clinic Wi-Fi, to be realistic); staging
with `WEB_PUSH_VAPID_*` set; a receptionist account on staging; a stopwatch.

1. On the phone, open staging, join a queue at a test clinic and sign in with the phone's number when
   asked. Agree to notifications about your place in the queue.
2. On the ticket page, check that **no permission prompt appeared by itself**, then press *Tell me on this
   phone when it is my turn* and allow notifications.
3. Lock the phone and put it in a pocket.
4. On a computer, sign in as the receptionist and press **Call next** on that queue. Start the stopwatch at
   the press.
5. Stop it when the phone buzzes. Unlock and check the notification's words, then tap it and check that the
   ticket page opens.
6. Repeat steps 4 and 5 five times, and once more with the phone's battery saver on.
7. Remove the site's notification permission in Chrome's settings, call again, and check that an SMS
   arrives and that the admin delivery viewer shows the web push row `dead` and an SMS row after it.

**Record** (fill in; do not tick the acceptance criterion until this is filled in by the person who did it):

| Date | Tester | Phone and Android version | Chrome version | Network | Seconds from Call next to buzz (5 runs) | Battery saver run | Declined, then SMS arrived? |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

---

**Refs:** [Issue 64](../GITHUB/ISSUES/M9/ISSUE_64_web_push_vapid.md) · the transport:
`src/modules/notifications/transports/webpush.py` · the sender and subscriptions:
`src/modules/notifications/webpush.py` · the page: `src/static/js/push.js`, `src/static/patient-sw.js`
