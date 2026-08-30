# 07 - Devices to buy - cheapest first

ClinicQ needs far less hardware than ElimuKadi (no cards/readers) or
UmojaNet (no radios/dishes) - the whole product is software plus **one
screen per clinic**. Global retail prices (AliExpress/Amazon/manufacturer store listings, Aug 2026
planning numbers) - add South African import VAT/duty where relevant; always re-check live prices before
ordering.

## Waiting-room display monitor - ranked cheapest first

| # | Device | Job | ~USD (retail) | Verdict |
|---|--------|-----|-----------------|---------|
| 1 | **Reuse an existing TV/monitor + old PC/laptop** | Show the display board ([04](04-display-monitor.md)) in a kiosk browser tab | **$0** | Cheapest possible start - many clinics already have a spare TV or an old office PC doing nothing |
| 2 | **Raspberry Pi 4/5 (2-4GB) as a signage box** | Drives any HDMI monitor/TV, runs a kiosk-mode browser | **$45-80** (board) | Cheapest dedicated always-on signage box; low power draw, survives load-shedding better with a small UPS |
| 3 | **Amazon Fire TV Stick / Android TV stick (dev-mode kiosk browser)** | Same job, plugs straight into a TV's HDMI port | **$35-60** | Slightly less flexible than a Pi but zero assembly |
| 4 | **Budget 32-43" TV/monitor** | The actual waiting-room screen, if the clinic has none | **$120-220** | Only needed if the clinic genuinely has no spare screen |
| 5 | **Commercial digital signage player (e.g. BrightSign-class)** | Same job, sturdier/managed remotely at scale | **$150-300** | Only worth it once running signage across many clinics (Stage 2+, see [12](12-upscaling-24-months.md)) |

<div></div>

**Cheapest way to get one pilot clinic's display live this week:** reuse the clinic's existing TV/monitor
+ a **$45-80 Raspberry Pi** running a kiosk-mode browser pointed at the clinic's display URL. Total new
hardware spend: **under $100**, often **$0** if a spare screen is already on-site.

## Reception / front-desk hardware

| # | Device | Job | ~USD (retail) | Verdict |
|---|--------|-----|-----------------|---------|
| 1 | **Reuse the clinic's existing PC/laptop** | Runs the dashboard ([05](05-clinic-dashboard.md)) in any modern browser | **$0** | Most clinics already have at least one office PC |
| 2 | **Budget Android tablet (8-10")** | Portable dashboard for a nurse moving between rooms | **$80-150** | Optional - only if a fixed PC per room isn't practical |
| 3 | **Thermal receipt printer (58mm, USB)** | Print a physical ticket stub for walk-ins who want a paper number instead of trusting they'll hear their name | **$25-45** | Optional convenience - the display monitor is the real "source of truth", the stub is just a memory aid |
| 4 | **Small UPS/battery backup** | Keep the dashboard + display box running through short outages | **$60-120** | Same load-shedding resilience logic as ElimuKadi |

## Networking (reuse what the clinic has)

| Item | Cost | Notes |
|------|------|-------|
| Clinic's existing Wi-Fi/LAN or a basic 4G/LTE router if no fixed line | **$0-40/month data** | The display box and dashboard just need normal internet access - no special network build |
| Mobile data fallback (LTE dongle/router) for clinics with no fixed line | **$25-45** device + data plan | Common in smaller/rural clinics - keeps the pilot possible without waiting on fibre/ADSL installation |

## Total Phase 0 hardware estimate (one pilot clinic)

| Scenario | Total new hardware spend |
|----------|-----------------------------|
| Clinic has a spare TV/monitor and a working PC | **$0-100** (just the signage box, maybe a UPS) |
| Clinic has neither | **$250-450** (screen + signage box + reused/cheap PC + UPS) |

This is the **lowest hardware bar of the three shortlisted-and-elaborated capstone directions** - see the
earlier team comparison of [effort/spend across the shortlist](../PROJECTS/Top_6_Final_Year_Project_Ideas.md)
- because there is no card stock, no NFC readers, and no radio/networking gear to buy.

## Read more - reference product pages

| Device | Official/reference page |
|--------|---------------------------|
| Raspberry Pi 4/5 | [raspberrypi.com/products](https://www.raspberrypi.com/products/) |
| Amazon Fire TV Stick | [amazon.com/fire-tv](https://www.amazon.com/all-new-fire-tv-stick) |
| BrightSign signage players | [brightsign.biz](https://www.brightsign.biz/) |

Markdown: this is [07-devices-and-bom.md](07-devices-and-bom.md); see stage-by-stage quantities in
[13-tech-implementation.md](13-tech-implementation.md#7-device-counts-per-stage).
