# Setting up a waiting-room screen

This guide turns a small computer and a TV into a waiting-room board. The box starts by itself, shows the
board without anyone touching it, comes back after a power cut, and tells the team when it goes quiet. You
prepare the box once, before it leaves for the clinic. At the clinic, nobody types an address or a
password into it: the clinic manager types a six-character code into ClinicQ, and the board appears.

**You need:**

- a **Raspberry Pi 4 or 5** (2 GB or more) with its official power supply and a 16 GB+ microSD card, **or**
  any small PC that runs Ubuntu 24.04 (see [07](../PRODUCT/07-devices-and-bom.md) for prices);
- an HDMI cable and the TV (32 inches or larger for a room read from 5 metres,
  [BOARD_LEGIBILITY.md](BOARD_LEGIBILITY.md));
- the clinic's Wi-Fi name and password, or a network cable;
- ClinicQ's address: `https://clinicq.example` below stands for it. On staging it is the staging address.

Allow **30 minutes** to prepare a box, and **2 minutes** at the clinic.

---

## Part A: prepare the box (at the office)

### A1. Install the operating system

**Raspberry Pi.** On any computer, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
Choose **Raspberry Pi OS (64-bit)**, the desktop version, and your microSD card. Then press **Next** and
choose **Edit settings**:

- **Hostname:** `clinicq-board-01` (number each box);
- **Username:** `kiosk`, with a strong password. Write the password in the team's password manager, not
  on the box;
- **Wireless LAN:** the clinic's Wi-Fi, and **country `ZA`**;
- **Locale:** time zone `Africa/Johannesburg`;
- **Services:** enable SSH with password authentication, so the team can reach the box when it is at the
  office.

Write the card, put it in the Pi, connect the TV and power it on. The first boot takes a few minutes.

**Small PC.** Install **Ubuntu 24.04 Desktop**. Create the user `kiosk`, set the time zone to
`Africa/Johannesburg`, and join the clinic's network.

### A2. Log in automatically

The box must reach the desktop without anyone typing a password.

- **Raspberry Pi:** `sudo raspi-config` → *System Options* → *Boot / Auto Login* → **Desktop Autologin**.
- **Ubuntu:** *Settings* → *Users* → **Automatic Login** on for `kiosk`.

### A3. Never blank the screen

- **Raspberry Pi:** `sudo raspi-config` → *Display Options* → *Screen Blanking* → **No**.
- **Ubuntu:** *Settings* → *Power* → *Screen Blank* → **Never**, and *Automatic Suspend* → **Off**.

Then turn the TV's own sleep or eco timer off in its menu. Many TVs switch themselves off after four
hours without a remote press.

### A4. Install Chromium

```bash
sudo apt update && sudo apt install -y chromium
```

On Raspberry Pi OS, Chromium is installed already (the command may say `chromium-browser`; use whichever
name exists in the next step).

### A5. Start the board on every boot, and again if it ever closes

Create the start-up service. Replace the address with ClinicQ's:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/clinicq-board.service <<'SERVICE'
[Unit]
Description=ClinicQ waiting-room board
After=graphical-session.target network-online.target
PartOf=graphical-session.target

[Service]
ExecStart=/usr/bin/chromium --kiosk --noerrdialogs \
  --disable-session-crashed-bubble --disable-features=Translate \
  --autoplay-policy=no-user-gesture-required --check-for-update-interval=31536000 \
  --overscroll-history-navigation=0 https://clinicq.example/display
Restart=always
RestartSec=5

[Install]
WantedBy=graphical-session.target
SERVICE
systemctl --user daemon-reload
systemctl --user enable --now clinicq-board.service
sudo loginctl enable-linger kiosk
```

- `--kiosk` fills the screen with no address bar, tabs or menus.
- `--autoplay-policy=no-user-gesture-required` lets the board's chime play without anyone clicking.
- `Restart=always` opens the board again five seconds after the browser closes for any reason.
- Do **not** add `--incognito`: the box keeps its pairing in the browser's cookies, and an incognito
  window forgets it at every start.

If your system calls the browser `chromium-browser`, change the path in `ExecStart`
(`which chromium chromium-browser` shows it).

### A6. Come back after a power cut

- **Raspberry Pi:** nothing to do. A Pi starts as soon as it has power.
- **Small PC:** in the BIOS or UEFI settings (usually F2 or Del while it starts), set **Restore on AC
  Power Loss** (sometimes *After Power Failure*) to **Power On**.

Test it now: pull the power, wait ten seconds, plug it back in. Without touching anything, the box should
start and show the pairing code (Part B) or, once paired, the board.

### A7. Check before it leaves

- [ ] The box starts to a full-screen ClinicQ page with **no** address bar, pointer or screen-saver.
- [ ] Pulling the power and plugging it back in returns to the same page by itself.
- [ ] The time in the corner of the board, once paired, is the clinic's time.
- [ ] The TV's sleep timer is off.
- [ ] The box's hostname and password are in the team's records.

---

## Part B: pair it at the clinic (2 minutes)

1. Connect the box to the TV and to power, and switch both on. Once the box has started (about a minute) the TV shows
   **"Pair this screen with your clinic"** and a code such as `K7M 4PQ`.
2. The **clinic manager**, on any computer signed in to ClinicQ, opens the dashboard's **Clinic settings**
   link. It opens on the *Profile* tab: choose the **Display boards** tab, the last one. (The
   *Waiting-room screen* tab next to it sets what every board looks like, such as names or numbers; it is
   not where screens are paired.)
3. They type the code into **Code on the screen**. Case, spaces and dashes do not matter. They add a name
   such as *TV by reception*, optionally tick the queues this TV should show, and press **Pair**.
4. The page reloads and the screen appears in the list below, with the status **Showing the board**.
   There is no other message. Within a few seconds the TV shows the waiting-room board by itself.

A code works for ten minutes, and the screen says so. After that, or whenever the box restarts, the
screen shows a new code: type the one on the screen now.

**Nobody types an address or a password into the box at the clinic.** The board's address is not a
secret, and it is not a key either: opened anywhere other than a paired screen, it shows a pairing code,
not the queue.

---

## Part C: living with it

**It is watched.** The board reports to ClinicQ every minute. If a paired screen is not heard from for
**10 minutes**, the team channel gets one message naming the screen and its clinic, and another when it is
back. [RUNBOOK_ALERTS.md](../CICD/RUNBOOK_ALERTS.md) says what to do. Operators see every screen at
`https://clinicq.example/admin/display-devices`, and a clinic manager sees their own under **Display
boards**. "Operators" here means the ClinicQ team: people whose role has the platform-wide
(*business*) permission to read clinics. A clinic manager opening that address gets *Access denied*,
which is expected.

**It updates itself.** When ClinicQ is upgraded, the board reloads by itself at a quiet moment (no ticket
being called). Nothing needs doing on the box.

**Moving or retiring a screen.** In **Display boards**, click the screen's row. A panel opens beside the
list with the screen's name and queues, and **Remove this screen** at its bottom edge. Press it and confirm
with **Yes, go ahead**. Within about half a minute the TV goes back to a pairing
code by itself. The screen stays in the list with the status **Removed**, and still counts in the
"N screens" line, so the clinic keeps a record of it. A removed box can be paired again, at the same
clinic or another; it then gets a new row.

**Power cuts.** The box comes back by itself. While the network is down the board keeps what it last
showed and says it is reconnecting. A small UPS
([07](../PRODUCT/07-devices-and-bom.md)) keeps it on through short cuts.

---

## When it does not work

| What you see | What to do |
|--------------|-----------|
| The TV says *No signal* | The box is off or the HDMI cable is loose. Check the Pi's red power light, and the TV's input (HDMI 1 or 2). |
| A desktop, not the board | The start-up service is not running: `systemctl --user status clinicq-board.service` over SSH, or repeat A5. |
| *This site can't be reached* | The box has no network. Check the Wi-Fi (A1) or cable; the page retries by itself. |
| The code never turns into the board | The manager typed it for the wrong clinic, or the code changed. Check **Display boards** at the right clinic, and type the code now on the screen. |
| The board shows *⟳ Reconnecting to the clinic…* | The network dropped. It recovers by itself; if it stays for more than a few minutes, check the clinic's internet. |
| The board went back to a code by itself | The screen was removed in **Display boards**. Pair it again. |
| The screen goes black after some hours | The TV's sleep or eco timer (A3), or the operating system's screen blanking (A3). |
| No chime | The TV's volume, and the `--autoplay-policy` flag in A5. The clinic can also have turned announcements off. |

---

## Trying it without a box

To see the whole flow on an ordinary computer (for training, or to check this guide):

Below, `http://localhost:8000` is a ClinicQ running on your own computer (`make dev`); use
`https://clinicq.example` or the staging address instead to try it against a real one.

1. Open Chromium or Google Chrome with its own profile, in kiosk mode, at the start address. On macOS:

   ```bash
   open -na "Google Chrome" --args --user-data-dir="$HOME/clinicq-kiosk-test" --kiosk http://localhost:8000/display
   ```

   For Chromium on macOS, write `"Chromium"` instead of `"Google Chrome"`. On Linux:
   `chromium --user-data-dir="$HOME/clinicq-kiosk-test" --kiosk http://localhost:8000/display`. On Windows:
   `chrome.exe --user-data-dir=%TEMP%\clinicq-kiosk-test --kiosk http://localhost:8000/display`.
   Leave kiosk mode with Cmd+Q or Alt+F4.
2. In a different browser (or a private window), sign in to the same ClinicQ as a clinic manager at the
   clinic you want the board for: your own manager account, or on a local database the clinic manager
   that `make seed-dev-data` prints ([QUICKSTART](../QUICKSTART.md)). Pair the kiosk window as in Part B.
3. Close the kiosk window and open it again with the same command (a "power cut"): it goes straight to the
   board.
