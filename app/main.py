import io
import json
import os
import re
from pathlib import Path
from typing import Any

import pdfplumber
import requests
import streamlit as st
from dotenv import load_dotenv

from pdf_filler import fill_tsow_pdf

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
TEMPLATE_PATH = PROJECT_DIR / "assets" / "TSOW_Template.pdf"
SCHEMA_PATH = PROJECT_DIR / "assets" / "tsow_schema.json"

LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1").rstrip("/")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "")
REQUEST_TIMEOUT = int(os.getenv("LM_STUDIO_TIMEOUT", "300"))

# These names are taken directly from the supplied TSOW PDF schema.
TSOW_FIELDS = [
    "Company Name",
    "Product Name",
    "BU",
    "Use Case",
    "AI Workload",
    "Intel Software Stack",
    "Model/s",
    "Model Optimizations",
    "OS",
    "Intel Target Hardware",
    "Intel",
    "ISV",
    "CRM",
    "Value Proposition",
    "Performance Metrics",
    "Success Criteria",
    "Dependencies",
]

SYSTEM_PROMPT = """
You are an information-extraction component for an Intel Technical Statement of Work (TSOW).

You will receive:
1. Text extracted from a completed client questionnaire PDF.
2. A user prompt containing extra project context, corrections, or instructions.
3. A fixed list of TSOW fields.

Return exactly one JSON object. Its keys MUST be exactly the supplied TSOW field names.
Each value must be a string or null. Do not return markdown fences or explanations.

Extraction rules:
- Use only facts explicitly supported by the questionnaire or user prompt.
- Do not invent names, numbers, contacts, hardware, performance results, or commitments.
- If information is not available, return null.
- The user prompt may provide additional facts or explicitly correct questionnaire details.
  If it clearly corrects a fact, use the corrected value.
- Preserve technical names, versions, quantities, model names, instance types, and distinctions
  between current implementation and planned/target technology.
- Keep answers concise enough to fit a one-page TSOW, but retain material technical details.
- Do not confuse the company with its product. For example, in a sentence like
  "Acme is building Product X", Acme is the company and Product X is the product.
- "Intel Software Stack" should include Intel software technologies when stated, and may
  mention the current framework separately from the Intel target stack.
- "Intel Target Hardware" must contain Intel hardware targets only, not AWS/GCP instance names.
- "Model/s" means AI/ML model architectures, not the product name alone.
- "Model Optimizations" means optimization techniques such as PEFT/LoRA, quantization,
  pruning, compilation, etc., only when stated.
- "Performance Metrics" should distinguish desired metrics from measured benchmark values.
  Do not invent numerical performance targets.
- "Success Criteria" should describe stated outcomes or goals; do not invent acceptance thresholds.
- "Dependencies" should include explicitly stated technical prerequisites or infrastructure.
- The fields Intel, ISV, and CRM are contact fields. Leave them null unless names or roles
  for those contact categories are actually supplied.
- A questionnaire answer can contain facts for multiple TSOW fields. Extract all relevant facts.
"""

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract selectable text from the uploaded questionnaire PDF."""
    pieces = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(layout=False) or ""
            if text.strip():
                pieces.append(f"[PAGE {page_no}]\n{text}")
            # Tables are sometimes extracted more clearly as rows.
            try:
                tables = page.extract_tables()
                for table in tables or []:
                    for row in table:
                        cells = [str(c).strip() for c in (row or []) if c and str(c).strip()]
                        if cells:
                            pieces.append(" | ".join(cells))
            except Exception:
                pass
    result = "\n\n".join(pieces).strip()
    if not result:
        raise ValueError(
            "No selectable text was found in this PDF. This demo expects a text-based PDF; "
            "a scanned PDF will need OCR."
        )
    return result

def parse_json_response(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"The model did not return JSON. Response starts with: {raw[:300]}")
        data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("The model response must be a JSON object.")
    return data

def get_model_name() -> str:
    """Use configured model name, otherwise ask LM Studio which model is loaded."""
    if LM_STUDIO_MODEL:
        return LM_STUDIO_MODEL
    response = requests.get(f"{LM_STUDIO_URL}/models", timeout=10)
    response.raise_for_status()
    models = response.json().get("data", [])
    if not models:
        raise RuntimeError("LM Studio returned no loaded models. Load a model in LM Studio first.")
    return models[0].get("id", "")

def _message_text(message: dict) -> str:
    """Handle common OpenAI-compatible message content formats."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts).strip()
    return ""

def extract_tsow_fields(questionnaire_text: str, user_prompt: str) -> tuple[dict, str]:
    model_name = get_model_name()
    user_message = f"""
TSOW FIELDS (use these exact keys):
{json.dumps(TSOW_FIELDS, ensure_ascii=False)}

QUESTIONNAIRE TEXT:
{questionnaire_text}

USER PROMPT / ADDITIONAL CONTEXT:
{user_prompt.strip() if user_prompt.strip() else "No additional user prompt provided."}

Return ONLY one valid JSON object. Include every requested key. Use null when a value is not stated.
Do not explain your answer. Do not use markdown fences.
"""
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": 3000,
        "stream": False,
    }

    # Try ordinary chat completion first. Some local models return an empty
    # message when JSON-constrained output is requested but not supported well.
    attempts = [
        ("normal", dict(payload)),
        ("json_mode", {**payload, "response_format": {"type": "json_object"}}),
    ]
    diagnostics = []
    last_raw = ""
    for attempt_name, request_payload in attempts:
        response = requests.post(
            f"{LM_STUDIO_URL}/chat/completions",
            json=request_payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            diagnostics.append(
                f"{attempt_name}: HTTP {response.status_code}: {response.text[:500]}"
            )
            continue
        try:
            body = response.json()
        except ValueError:
            diagnostics.append(f"{attempt_name}: server did not return JSON: {response.text[:500]}")
            continue

        choices = body.get("choices") or []
        if not choices:
            diagnostics.append(f"{attempt_name}: response has no choices: {str(body)[:700]}")
            continue

        choice = choices[0]
        message = choice.get("message") or {}
        last_raw = _message_text(message)
        finish_reason = choice.get("finish_reason")

        if not last_raw:
            diagnostics.append(
                f"{attempt_name}: empty message content; finish_reason={finish_reason}; "
                f"message_keys={list(message.keys())}; usage={body.get('usage')}"
            )
            continue

        try:
            parsed = parse_json_response(last_raw)
        except (ValueError, json.JSONDecodeError) as exc:
            diagnostics.append(f"{attempt_name}: non-JSON output: {last_raw[:400]} ({exc})")
            continue

        normalized = {}
        for field in TSOW_FIELDS:
            value = parsed.get(field)
            if value is None:
                normalized[field] = None
            elif isinstance(value, (dict, list)):
                normalized[field] = json.dumps(value, ensure_ascii=False)
            else:
                text_value = str(value).strip()
                normalized[field] = text_value if text_value else None
        return normalized, model_name

    raise RuntimeError(
        "LM Studio responded, but the model did not produce a usable JSON object.\n\n"
        + "\n".join(diagnostics)
        + "\n\nCheck that a chat/instruct model is loaded, increase its context length in LM Studio, "
          "and try again. If using a reasoning model, choose a non-thinking instruct model for this demo."
    )

def main():
    st.set_page_config(page_title="TSOW V1 Demo", page_icon="📄", layout="wide")
    st.title("TSOW Generator — Version 1")
    st.caption("Questionnaire PDF + user prompt → structured JSON → completed TSOW PDF")

    with st.sidebar:
        st.subheader("LM Studio connection")
        st.code(LM_STUDIO_URL, language="text")
        st.caption("Start the LM Studio Local Server and load your chat model before processing.")
        if st.button("Test LM Studio connection"):
            try:
                model = get_model_name()
                st.success(f"Connected. Model: {model}")
            except Exception as exc:
                st.error(str(exc))

    uploaded_pdf = st.file_uploader("Upload completed technical questionnaire (PDF)", type=["pdf"])
    user_prompt = st.text_area(
        "User prompt / additional project context",
        height=130,
        placeholder="Example: The company name is Assessli. Please generate a TSOW for optimizing its LBM workload on Intel Xeon 6.",
    )

    if not TEMPLATE_PATH.exists() or not SCHEMA_PATH.exists():
        st.error("The bundled TSOW template or schema is missing from the assets folder.")
        return

    if st.button("Generate TSOW", type="primary", disabled=uploaded_pdf is None):
        try:
            with st.spinner("Reading questionnaire PDF..."):
                questionnaire_text = extract_pdf_text(uploaded_pdf.getvalue())
            with st.expander("Extracted questionnaire text (debug view)"):
                st.text(questionnaire_text)

            with st.spinner("Extracting TSOW fields with the local model..."):
                answers, model_name = extract_tsow_fields(questionnaire_text, user_prompt)

            st.success(f"Information extracted using: {model_name}")
            st.subheader("Extracted TSOW JSON")
            st.json(answers)

            json_bytes = json.dumps(answers, indent=2, ensure_ascii=False).encode("utf-8")
            st.download_button(
                "Download extracted JSON",
                data=json_bytes,
                file_name="tsow_extracted_data.json",
                mime="application/json",
            )

            with st.spinner("Filling the TSOW PDF template..."):
                output_bytes, report = fill_tsow_pdf(
                    str(TEMPLATE_PATH),
                    str(SCHEMA_PATH),
                    answers,
                    highlight_review=False,
                )

            st.subheader("PDF generation report")
            st.json(report)
            st.download_button(
                "Download completed TSOW PDF",
                data=output_bytes,
                file_name="Completed_TSOW.pdf",
                mime="application/pdf",
            )
            if report.get("overflow"):
                st.warning(
                    "Some fields may contain more text than their template cells can display. "
                    "Review the overflow list and the generated PDF before the demo."
                )
            if report.get("not_provided"):
                st.info("These template fields were left blank because no value was extracted: "
                        + ", ".join(report["not_provided"]))
        except requests.exceptions.ConnectionError:
            st.error(
                "Cannot connect to LM Studio. Open LM Studio → Developer → Start Server. "
                "Check that the server URL in .env matches the address shown there."
            )
        except Exception as exc:
            st.exception(exc)

if __name__ == "__main__":
    main()
