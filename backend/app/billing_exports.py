from __future__ import annotations

import csv
from io import StringIO
from typing import Any


def _csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def statement_csv(summary: dict[str, Any]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    user = summary["user"]
    writer.writerow(["Dars Manager", "Relevé mensuel"])
    writer.writerow(["Période", summary["period"]])
    writer.writerow(["Client", _csv_cell(user["display_name"])])
    writer.writerow(["E-mail", _csv_cell(user["email"])])
    writer.writerow(["Devise", summary["currency"]])
    writer.writerow([])
    writer.writerow(["Synthèse", "Montant"])
    writer.writerow(["Coût confirmé", summary["confirmed_cost"]])
    writer.writerow(["Coût estimé", summary["estimated_cost"]])
    writer.writerow(["Paiements", summary["paid"]])
    writer.writerow(["Financement communautaire", summary["community_funded"]])
    writer.writerow(["Solde", summary["balance"]])
    writer.writerow([])
    writer.writerow([
        "Projet",
        "Service",
        "Modèle",
        "Quantité",
        "Unité",
        "Statut",
        "Montant",
        "Date",
        "Job",
    ])
    for event in summary["usage"]:
        writer.writerow([
            _csv_cell(event["project_title"] or "Infrastructure"),
            _csv_cell(event["service"]),
            _csv_cell(event["model"]),
            event["quantity"],
            event["unit"],
            event["status"],
            event["amount"],
            event["occurred_at"].isoformat(),
            _csv_cell(event["job_id"] or ""),
        ])
    writer.writerow([])
    writer.writerow(["Paiement", "Date", "Méthode", "Référence", "Note"])
    for payment in summary["payments"]:
        writer.writerow([
            payment["amount"],
            payment["paid_at"].isoformat(),
            _csv_cell(payment["method"]),
            _csv_cell(payment["reference"]),
            _csv_cell(payment["note"]),
        ])
    writer.writerow([])
    writer.writerow(["Financement communautaire", "Projet", "Catégorie", "Date"])
    for allocation in summary["community_allocations"]:
        writer.writerow([
            allocation["amount"],
            _csv_cell(allocation["project_title"]),
            _csv_cell(allocation["category"]),
            allocation["created_at"].isoformat(),
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def statement_lines(summary: dict[str, Any]) -> list[str]:
    user = summary["user"]
    lines = [
        "DARS MANAGER - RELEVE MENSUEL",
        f"Periode : {summary['period']}",
        f"Client : {user['display_name']} ({user['email']})",
        f"Devise : {summary['currency']}",
        "",
        "SYNTHESE",
        f"Cout confirme : {summary['confirmed_cost']} {summary['currency']}",
        f"Cout encore estime : {summary['estimated_cost']} {summary['currency']}",
        f"Paiements enregistres : {summary['paid']} {summary['currency']}",
        f"Financement communautaire : {summary['community_funded']} {summary['currency']}",
        f"Solde : {summary['balance']} {summary['currency']}",
        "",
        "COUTS PAR PROJET",
    ]
    if not summary["projects"]:
        lines.append("Aucun cout pour cette periode.")
    for project in summary["projects"]:
        lines.append(
            f"- {project['project_title']} : {project['total_cost']} {summary['currency']} "
            f"({project['operations']} operations, "
            f"{project['community_funded']} finance par la communaute)"
        )
    lines.extend(["", "DETAIL DES OPERATIONS"])
    if not summary["usage"]:
        lines.append("Aucune operation.")
    for event in summary["usage"]:
        occurred = event["occurred_at"].strftime("%Y-%m-%d")
        lines.append(
            f"{occurred} | {event['project_title'] or 'Infrastructure'} | "
            f"{event['service']} / {event['model']} | {event['status']} | "
            f"{event['amount']} {event['currency']}"
        )
    lines.extend(["", "PAIEMENTS"])
    if not summary["payments"]:
        lines.append("Aucun paiement enregistre.")
    for payment in summary["payments"]:
        paid_at = payment["paid_at"].strftime("%Y-%m-%d")
        lines.append(
            f"{paid_at} | {payment['amount']} {payment['currency']} | "
            f"{payment['reference'] or payment['method']}"
        )
    lines.extend(["", "FINANCEMENT COMMUNAUTAIRE"])
    if not summary["community_allocations"]:
        lines.append("Aucune allocation communautaire.")
    for allocation in summary["community_allocations"]:
        allocated_at = allocation["created_at"].strftime("%Y-%m-%d")
        lines.append(
            f"{allocated_at} | {allocation['project_title']} | "
            f"{allocation['amount']} {allocation['currency']} | {allocation['category']}"
        )
    lines.extend([
        "",
        "Document genere par Dars Manager a partir du registre auditable.",
    ])
    return lines


def _pdf_text(value: str) -> bytes:
    encoded = value.encode("cp1252", errors="replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def statement_pdf(summary: dict[str, Any]) -> bytes:
    lines = statement_lines(summary)
    pages = [lines[index:index + 52] for index in range(0, len(lines), 52)] or [[]]
    objects: list[bytes] = []

    def add(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    pages_id = add(b"")
    page_ids: list[int] = []
    for page_number, page_lines in enumerate(pages, start=1):
        visible = [*page_lines, "", f"Page {page_number}/{len(pages)}"]
        commands = [b"BT", b"/F1 9 Tf", b"44 800 Td", b"13 TL"]
        for line in visible:
            commands.append(b"(" + _pdf_text(line[:110]) + b") Tj")
            commands.append(b"T*")
        commands.append(b"ET")
        stream = b"\n".join(commands)
        content_id = add(
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )
        page_id = add(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R "
                f"/MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        page_ids.append(page_id)
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
    ).encode()
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, payload in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_id} 0 obj\n".encode())
        document.extend(payload)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)
