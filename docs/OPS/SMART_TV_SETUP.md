# Putting the board on a Smart TV

This guide is for the second way to run a waiting-room board: **the television is the computer**. The
first way — a Raspberry Pi or small PC on the TV's HDMI port — is in
[KIOSK_SETUP.md](KIOSK_SETUP.md), and it is still the one to choose when you can. It works with every
television ever made, needs nothing from the TV but an HDMI socket, survives a power cut and needs no
permission from anybody.

Read this one when the clinic's television has **Chromecast built in** (most Android TV and Google TV
sets do) and you would rather not put a box behind it.

---

## What this actually does

1. A clinic manager opens **Clinic settings → Display boards** and presses **Search the network**.
2. The *server* asks its own network which screens are there, and lists what answered.
3. The manager picks one and presses **Send the board**.
4. The server holds a row for that screen, connects to it, and hands it a one-time code.
5. The screen opens ClinicQ, spends the code, and shows the clinic's board.

Nobody types a code, and nobody carries one across the room.

---

## The three things it needs

### 1. The server has to be on the clinic's network

This is the one that decides whether the feature is possible at all.

Finding a screen is a question shouted at the local network (mDNS multicast). Only a process **on
that network** hears the answers. The manager's browser cannot ask — a web page has no multicast — so
the server asks, and the server only hears the clinic's televisions when it is in the clinic.

| Where ClinicQ runs | Can it find the clinic's TV? |
|---|---|
| A box at the clinic (on-prem) | Yes |
| A laptop on the clinic's Wi-Fi (a demo) | Yes |
| A cloud instance | **No, and never** — it shares no network with the clinic |

For that reason the feature is **off** unless a deployment turns it on:

```
SMART_TV_DISCOVERY_ENABLED=true
```

While it is off, the **Screens on this network** section is not rendered at all — no button that
could only ever fail. The other two ways in are unaffected: pair the board with the code on its
screen ([KIOSK_SETUP.md](KIOSK_SETUP.md)), or make an address for the screen to open
([below](#where-the-address-comes-from)).

Also check the network itself. A guest SSID, a VLAN, or "AP isolation" / "client isolation" on the
access point will each keep the server and the television from hearing one another even when both say
they are on the clinic's Wi-Fi.

### 2. The optional package

```
pip install PyChromecast==14.0.10
```

It is in `requirements.txt` and is imported only at the moment a manager searches, so a deployment
that never turns this on never loads it. If the setting is on and the package is missing, the page
says exactly that.

### 3. A registered Cast receiver

**This is what makes the board appear**, and it is a one-off piece of paperwork with Google.

A Chromecast is not a browser you can point at a URL. It runs a *receiver application*: a page that
has been registered at [cast.google.com/publish](https://cast.google.com/publish) under an
application id, and a sender may launch a registered id and nothing else. ClinicQ serves that page at
`/display/cast`; Google has to be told it exists.

1. Sign in at [cast.google.com/publish](https://cast.google.com/publish) (there is a one-off
   registration fee, about US$5, for a Cast developer account).
2. Register a **Custom Receiver**. Its URL is this deployment's `/display/cast` — for example
   `https://clinicq.example/display/cast`.
3. Register the television as a **test device** (its serial number is in the TV's Cast settings)
   while the application is unpublished. An unpublished receiver runs only on registered devices.
4. Put the application id you are given into the deployment's environment:

   ```
   CAST_RECEIVER_APP_ID=XXXXXXXX
   ```

Until that id is set, everything else still works: a manager can find screens and press **Send the
board**, and the page reports that the screen was *reached* but has no ClinicQ page it is allowed to
open. That is a useful answer — it proves the network, the address and the television — and it names
the one thing still missing.

Google requires a receiver to be served over **HTTPS** before it is published. For a demo on a
laptop, register the laptop's address and keep the application unpublished and the TV registered as a
test device.

---

## Checking it from the machine itself

When the button finds nothing, go to the server and run:

```bash
python -m scripts.find_screens
```

It runs the same search the button runs, with nothing in the way, and prints what answered. To check
that one screen is reachable — this pairs nothing and changes nothing:

```bash
python -m scripts.find_screens --connect 192.168.3.106
```

### On macOS, check the local-network permission first

macOS (Sequoia and later, including macOS 26) requires a program to be allowed onto the local network,
and a program that has not been allowed is **given no error**: multicast answers never arrive and
connections to devices on the LAN fail with *No route to host*. An empty search and a network with no
televisions look exactly alike.

To tell them apart, ask the system's own daemon, which is exempt:

```bash
dns-sd -B _googlecast._tcp local.
```

If that lists a screen and `python -m scripts.find_screens` does not, the permission is the problem:
**System Settings → Privacy & Security → Local Network**, and allow the program that runs the server
(the Python binary, or the terminal you start it from). A clinic's own box runs Linux, where there is
no such gate.

---

## How a screen becomes a clinic's screen

Worth knowing, because it is what makes the feature safe to hand a clinic manager.

The dashboard holds a row for the screen **before** it contacts it, then sends the screen a one-time
claim code over the clinic's own network. The screen hands the code straight back at
`/display/claim`, and that is what proves it is the screen that was chosen. In return it gets the same
httpOnly device secret every kiosk box gets, and its clinic's board.

* The code is **single-use** and expires with the row (`DISPLAY_PAIRING_CODE_MINUTES`, ten by default).
* A reservation is **not a credential**: the row's stored secret is a placeholder until a screen
  claims it, so a row sitting in the database opens nothing.
* A screen that never answers leaves a row reading **pairing** in the clinic's list, which a manager
  can remove like any other screen. The attempt is audited either way.
* The claim codes of the two flows cannot be spent on each other: a code held for a chosen screen is
  not one a person may type, and a code shown on a box is not one a screen may claim.

## A television with a browser but no Chromecast

The same one-time code works as a link:

```
https://clinicq.example/display/claim?code=XXXXXX
```

Opening it in the television's own browser pairs that screen and goes straight to the board. The code
is spent the instant it is opened, so the address in a history opens nothing afterwards.

### Where the address comes from

**Clinic settings → Display boards → Open the board on the screen itself.** Choose what the device
is, name it if you like, and press **Make an address**. The page shows the whole address and the code
inside it, with a **Copy the address** button and how long it has left.

This is the path that needs nothing else to be true. It contacts no screen and searches no network,
so unlike **Screens on this network** it is offered by every deployment — including a cloud one,
where finding a clinic's television is impossible and this is the only way in. Reach for it when:

* the television browses the web but will not be cast to;
* `CAST_RECEIVER_APP_ID` is not registered yet, so a cast reports *reached, but no ClinicQ page to
  open* — the **Screens on this network** section offers the same address there, under the failure,
  because the row it held is still waiting for a screen;
* you are **trying the board out without a television at all**: open the address in a second browser
  window, or another browser, and that window becomes the clinic's board. This works against a
  laptop on `localhost`, which nothing else here does.

The address is a credential for as long as it lives, so treat it like the code it contains: it is
shown only when asked for, it is single-use, and whoever opens it first becomes that screen —
including you, if you open it in the tab you made it in.

To undo a screen made this way, remove it in the list below, exactly like one paired with a code.

---

## What to do when it will not work

Nothing here is a prerequisite for a waiting-room board. If the television has no Chromecast, the
network keeps its devices apart, or the deployment is in the cloud, use
[KIOSK_SETUP.md](KIOSK_SETUP.md): a small box on the HDMI port, a six-character code, and two minutes
at the clinic.
