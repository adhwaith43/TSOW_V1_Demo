import io
import json
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT

PAD = 4
MAX_FONT = 9
MIN_FONT = 4.5
FONT = "Helvetica"

def _to_markup(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "<br/>".join("&bull; " + escape(str(v)) for v in value if str(v).strip())
    if isinstance(value, dict):
        return "<br/>".join(
            f"<b>{escape(str(k))}:</b> {escape(str(v))}" for k, v in value.items()
        )
    return escape(str(value)).replace("\n", "<br/>")

def _fit_paragraph(markup, width, height):
    size = MAX_FONT
    last = None
    while size >= MIN_FONT:
        style = ParagraphStyle(
            "tsow_cell",
            fontName=FONT,
            fontSize=size,
            leading=size * 1.15,
            alignment=TA_LEFT,
            spaceAfter=0,
            spaceBefore=0,
        )
        paragraph = Paragraph(markup, style)
        _, rendered_height = paragraph.wrap(width, height)
        last = (paragraph, rendered_height, size)
        if rendered_height <= height:
            return (*last, True)
        size -= 0.5
    return (*last, False)

def _normalize_input(data):
    """
    Accept:
      1) flat template JSON: {"Company Name": "Assessli", ...}
      2) friend's legacy format: {"questionnaire_answers": [{"standard_field": "...", "answer": "..."}]}
      3) nested format with an "answers" object.
    """
    if not isinstance(data, dict):
        raise ValueError("The extracted data must be a JSON object.")

    if isinstance(data.get("answers"), dict):
        data = data["answers"]

    if isinstance(data.get("questionnaire_answers"), list):
        flat = {}
        for item in data["questionnaire_answers"]:
            if not isinstance(item, dict):
                continue
            field = item.get("standard_field")
            value = item.get("answer")
            if field and value is not None and str(value).strip():
                flat[str(field)] = value
        return flat

    return {str(k): v for k, v in data.items() if not str(k).startswith("_")}

def fill_tsow_pdf(template_path, schema_path, data, highlight_review=False):
    with open(schema_path, "r", encoding="utf-8") as file:
        schema = json.load(file)

    flat = _normalize_input(data)
    field_lookup = {key.strip().casefold(): key for key in schema["fields"]}
    normalized_values = {}
    missing_in_template = []

    for key, value in flat.items():
        canonical = field_lookup.get(key.strip().casefold())
        if canonical:
            normalized_values[canonical] = value
        elif str(value).strip():
            missing_in_template.append(key)

    reader = PdfReader(template_path)
    page_count = len(reader.pages)
    page_buffers = {}

    for page_info in schema["pages"]:
        page_no = int(page_info["page"])
        buffer = io.BytesIO()
        page_canvas = canvas.Canvas(
            buffer,
            pagesize=(float(page_info["width"]), float(page_info["height"])),
        )
        page_buffers[page_no] = (page_canvas, buffer, float(page_info["height"]))

    report = {
        "filled": [],
        "empty": [],
        "not_provided": [],
        "missing_in_template": missing_in_template,
        "overflow": [],
    }

    for field_name, field_info in schema["fields"].items():
        page_no = int(field_info["page"])
        value = normalized_values.get(field_name)

        if value is None or not str(value).strip():
            report["empty"].append(field_name)
            report["not_provided"].append(field_name)
            continue

        if page_no not in page_buffers:
            report["overflow"].append(f"{field_name}: schema page {page_no} is missing")
            continue

        x0, top, x1, bottom = [float(v) for v in field_info["bbox"]]
        cell_width = (x1 - x0) - 2 * PAD
        cell_height = (bottom - top) - 2 * PAD
        if cell_width <= 0 or cell_height <= 0:
            report["overflow"].append(f"{field_name}: invalid cell dimensions")
            continue

        markup = _to_markup(value)
        paragraph, paragraph_height, font_size, fits = _fit_paragraph(
            markup, cell_width, cell_height
        )

        page_canvas, _, page_height = page_buffers[page_no]
        # Convert pdfplumber top-origin coordinates to ReportLab bottom-origin.
        cell_bottom_y = page_height - bottom
        if highlight_review:
            page_canvas.saveState()
            page_canvas.setFillColorRGB(1, 0.94, 0.55)
            page_canvas.rect(x0, cell_bottom_y, x1 - x0, bottom - top, stroke=0, fill=1)
            page_canvas.restoreState()

        # Clip text to the target cell so it cannot overwrite neighboring cells.
        page_canvas.saveState()
        clip_path = page_canvas.beginPath()
        clip_path.rect(x0, cell_bottom_y, x1 - x0, bottom - top)
        page_canvas.clipPath(clip_path, stroke=0, fill=0)
        paragraph.drawOn(
            page_canvas,
            x0 + PAD,
            page_height - top - PAD - paragraph_height,
        )
        page_canvas.restoreState()

        report["filled"].append({"field": field_name, "font_size": font_size})
        if not fits:
            report["overflow"].append(
                f"{field_name}: text exceeds cell even at {MIN_FONT}pt; clipped in output"
            )

    writer = PdfWriter(clone_from=template_path)
    for page_index, page in enumerate(writer.pages, start=1):
        if page_index not in page_buffers:
            continue
        page_canvas, buffer, _ = page_buffers[page_index]
        page_canvas.save()
        buffer.seek(0)
        overlay = PdfReader(buffer).pages[0]
        page.merge_page(overlay)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue(), report
