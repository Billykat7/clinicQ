"""Patients: identity by phone number and one-time code, never by password (Issue 17).

A patient is a mobile number. On the web they prove it with a 6-digit code sent by SMS
(``POST /api/v1/patients/otp/request``, then ``/otp/verify``), and the verified number *is* the
patient record: created on first verification, found again on every later one, whichever way the
number is written. There is no password on any channel, and no account to create.

Files, in the shape of ``src/modules/widgets``: the model is
:mod:`src.database.models.patient`; :mod:`.schemas`, :mod:`.service`, :mod:`.sessions`,
:mod:`.router` and :mod:`.rbac_manifest` are here. Phone numbers are normalised in exactly one
place, :func:`src.commons.phone.normalize_phone`. Codes come from the kernel's one OTP store
(:mod:`src.core.otp_store`, shared with the staff email sign-in) and leave through the notification
service's SMS path (:func:`src.modules.notifications.service.send_sms`), whose logging provider
works before any SMS account exists. A code never reaches a log or a database row.

Decision: the USSD path trusts the gateway MSISDN without a second OTP
-----------------------------------------------------------------------

A USSD session arrives from the aggregator with the caller's MSISDN, which the mobile network
authenticated when it connected the call: a USSD session cannot be started from someone else's SIM.
So :func:`.service.patient_for_gateway` resolves (or creates) the patient from that number with **no
code**, for USSD and for WhatsApp (whose ``wa_id`` Meta verified when the number registered).
Asking a USSD caller for an SMS code would add a second channel, a cost per session and a failure
point, to prove something the network already proved, and would exclude feature phones on patchy
coverage, the population USSD exists for.

What this relies on, and therefore what M10 must hold (Issues 73, 75):

* the gateway's webhook is authenticated (a shared secret or signature, and an IP allow-list), so
  nobody but the aggregator can claim an MSISDN;
* the trust ends with the channel: it never issues a **web** session, which only a code can do;
* a lost or stolen phone is the one risk this accepts, the same one an SMS code accepts.

Recorded as decision 9 in ``docs/GITHUB/ISSUES/README.md``.
"""
