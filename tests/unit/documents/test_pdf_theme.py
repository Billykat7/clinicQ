"""Unit tests for the shared branded-PDF theme (Issue 98 / M16).

Essential, isolated logic only (per ``docs/IDE/RULES/testing-strategy.mdc``): the pure pieces every
generator now shares — the aligned **money table** (right-justified amounts, currency, ruled total
from exact minor units), the **determinism** of the render given fixed metadata, and that the
themed render still emits real, multi-page PDF bytes. The per-document content of each generator is
covered by that generator's own test; here we pin only the shared theme.
"""

from datetime import date

from src.modules.documents.pdf_theme import (
    DocumentMeta,
    MoneyLine,
    StampKind,
    build_pdf,
    money_table,
    render_lines_pdf,
)

_META = DocumentMeta(
    title="Test Document",
    reference="REF-1",
    document_date=date(2026, 8, 11),
    stamp=StampKind.PAID,
)


def _cells(table) -> list[list[str]]:
    """Return a money table's rendered cell grid for assertions."""
    return table._cellvalues


def test_money_table_right_justifies_amounts_from_minor_units() -> None:
    """Each amount is the exact minor-unit value formatted to two decimals (never a float)."""
    table = money_table(
        [
            MoneyLine("Rent", 100000, "due 2026-01-01"),
            MoneyLine("Utilities", 2550),
        ],
        currency="ZAR",
    )
    rows = _cells(table)
    assert rows[0] == ["Description", "", "Amount (ZAR)"]  # currency in the header
    assert rows[1] == ["Rent", "due 2026-01-01", "1000.00"]
    assert rows[2] == ["Utilities", "", "25.50"]


def test_money_table_appends_a_bold_total_row() -> None:
    """A total row is appended with the exact minor-unit total when a label/total are given."""
    table = money_table(
        [MoneyLine("Rent", 100000), MoneyLine("Late fee", 5000)],
        currency="ZAR",
        total_label="Amount received",
        total_minor=105000,
    )
    assert _cells(table)[-1] == ["Amount received", "", "1050.00"]


def test_money_table_omits_total_row_when_not_requested() -> None:
    """Without a total label the table is header plus body rows only."""
    table = money_table([MoneyLine("Rent", 100000)], currency="ZAR")
    assert len(_cells(table)) == 2  # header + one body row


def test_render_lines_pdf_emits_pdf_bytes() -> None:
    """The themed line renderer turns text into real PDF bytes."""
    pdf = render_lines_pdf(["INSPECTION REPORT", "", "Body line"], meta=_META)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 500


def test_render_is_deterministic_for_fixed_metadata() -> None:
    """Identical content and metadata render byte-identical output (the determinism AC)."""
    lines = ["OWNER STATEMENT", "", "Owner: Thandi"]
    assert render_lines_pdf(lines, meta=_META) == render_lines_pdf(lines, meta=_META)


def test_render_paginates_and_stays_valid_across_pages() -> None:
    """A body long enough to overflow one page still renders a single valid PDF."""
    long_body = [f"Line {i} of the document body." for i in range(140)]
    pdf = build_pdf_from_lines(long_body)
    assert pdf.startswith(b"%PDF-")


def build_pdf_from_lines(lines: list[str]) -> bytes:
    """Render lines through :func:`build_pdf` for the pagination check."""
    from src.modules.documents.pdf_theme import lines_to_story, styles

    return build_pdf(lines_to_story(lines, styles()), meta=_META)
