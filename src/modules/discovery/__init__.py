"""Discovery: finding a clinic before thinking about a queue (M5).

Read the files in this order:

* :mod:`.service` — :func:`~.service.find_nearby_sites`, the one search the web page, the map, the
  USSD menu and the WhatsApp bot all call. It returns plain data, never HTML, so a channel adapter
  renders it and never re-implements it;
* :mod:`.travel` — the rough travel time next to each result, and the three numbers behind it;
* :mod:`.schemas` — the web API's rendering of the service's dataclasses;
* :mod:`.router` — ``GET /api/v1/clinics/nearby``, a thin, public adapter over the service.

The module **reads** clinics and never writes them. What a clinic is and whether a patient may see
it belong to :mod:`src.modules.sites` (Issues 23 and 29); how long its queues are belongs to
:mod:`src.modules.queues.live`. Discovery combines the two, and that is all it does.
"""
