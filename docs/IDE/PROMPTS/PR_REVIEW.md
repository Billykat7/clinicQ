Review the work done (only committed changes) in this branch and improve where needs be.
The review MUST be done following the best coding practices, highest industry standards,
the latest language (Python 3.14) docs and this project's rules defined in .cursor/rules.

Pay particular attention to this project's non-negotiables (docs/guideline.md):

- Money is integer minor units plus a currency, never float.
- The payments ledger is append-only; corrections are reversing entries, not edits.
- `unit.status` is written only by the clinicq service transition function.
- Active leases are immutable; changes are renewals or addenda.
- Cross-module calls use public service functions, never another module's models.
- Tenant documents are private: signed, expiring links only, and every download is audited.
- Wire-safe values are enums, not strings.
- Business datetimes are Africa/Johannesburg.
