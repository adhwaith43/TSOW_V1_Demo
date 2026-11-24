# TSOW Generator — Version 1 (Demo)

This is the integrated Version 1 demo made from the two uploaded projects.

## What Version 1 does

1. Accepts a completed questionnaire PDF.
2. Accepts a user prompt / extra project context.
3. Extracts the questionnaire's selectable text with `pdfplumber`.
4. Sends the text and prompt to a local model served by **LM Studio**.
5. Requests a JSON object whose keys match the actual fields in the supplied TSOW template.
6. Uses the existing PDF overlay approach (ReportLab + pypdf) to fill the original template.
7. Lets you download both the extracted JSON and completed PDF.

Version 1 intentionally does NOT include meeting transcript processing, RAG, embeddings, or a vector database.

## Folder structure

```text
TSOW_V1_Demo/
├── app/
│   ├── main.py                 # Streamlit interface + extraction workflow
│   └── pdf_filler.py           # PDF overlay / filling module
├── assets/
│   ├── TSOW_Template.pdf       # Supplied Intel TSOW template
│   └── tsow_schema.json        # Field positions extracted from that template
├── .env.example
├── requirements.txt
└── README.md
```

## 1. Install Python

Use Python 3.10 or 3.11 if possible. Create a virtual environment in this folder.

### Windows PowerShell

```powershell
cd TSOW_V1_Demo
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
cd TSOW_V1_Demo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Configure LM Studio

1. Open LM Studio.
2. Load the chat model you downloaded (for example, Qwen 2.5 Coder 3B).
3. Open the **Developer** tab.
4. Start the **Local Server**.
5. Note the server address and port shown by LM Studio. The usual default is `http://localhost:1234`.
6. Keep LM Studio running while using the app.

The app uses the OpenAI-compatible LM Studio endpoints:
- `GET /v1/models`
- `POST /v1/chat/completions`

Create a `.env` file by copying `.env.example`.

```env
LM_STUDIO_URL=http://localhost:1234/v1
LM_STUDIO_MODEL=
LM_STUDIO_TIMEOUT=300
```

Leave `LM_STUDIO_MODEL` blank initially. The app will automatically use the first model currently loaded in LM Studio. If automatic detection does not work, copy the exact model ID returned by `/v1/models` into `LM_STUDIO_MODEL`.

**Important:** If your LM Studio server shows port `1235` or another port, change `LM_STUDIO_URL` to that exact address. The app does not assume the port from your LM Studio installation.

## 3. Run the app

From the project root (the folder containing `requirements.txt`):

```powershell
streamlit run app/main.py
```

Your browser should open the Streamlit app. If it does not, open the local URL printed in the terminal, normally `http://localhost:8501`.

## 4. Run the demo

1. Make sure the LM Studio server is running and your model is loaded.
2. Upload `Technical Questionnaire - Assessli.PDF`.
3. Enter a prompt such as:

   `Generate a TSOW for Assessli. The target platform is Intel Xeon 6. Use the questionnaire as the source of technical details. Do not invent missing contacts, performance numbers, or commitments.`

4. Click **Generate TSOW**.
5. Review the extracted JSON.
6. Download `Completed_TSOW.pdf`.
7. Open the PDF and inspect every filled cell before presenting it.

## 5. Notes and known limitations

- The supplied questionnaire is a text-based PDF. A scanned/image-only PDF will require OCR; this demo intentionally keeps dependencies small.
- The supplied PDF template and coordinate schema are paired. If the template changes, the schema must be regenerated and reviewed.
- Long answers may shrink to a small font or be clipped because the template has fixed-size cells. Check the PDF generation report's `overflow` list.
- The local model can make extraction mistakes. Always review the JSON and PDF before treating them as final.
- Qwen 2.5 Coder 3B can be used for the demo if that is the model already downloaded, but a similarly sized instruct model may follow extraction instructions better.
- If the model returns invalid JSON, the app displays the error. Retry once or use a stronger instruct model / increase the model context length in LM Studio.

## Why some of the friend's code was changed

- The original backend used Docling and LlamaParse even though this demo only needs to read the supplied questionnaire PDF. This introduced heavy dependencies and a LlamaCloud API key requirement. The Version 1 demo uses `pdfplumber` instead.
- The friend's extraction JSON did not match the PDF filler fields one-to-one. This version asks the model for the exact 17 field names in `tsow_schema.json`.
- The original frontend expected a transcript and had a different response key. This demo removes transcript upload and puts extraction and PDF generation in one Streamlit workflow.
- The friend's semantic matcher depended on Ollama embeddings. That is unnecessary for this Version 1 scope, so it is not included.
- The PDF overlay method is retained and made to return PDF bytes for the Streamlit download button.

## Troubleshooting

### Cannot connect to LM Studio
- Start the Local Server in LM Studio.
- Confirm the URL and port in `.env`.
- Test in a browser: `http://localhost:1234/v1/models` (replace port if needed).

### Model is not listed
- Load the model in LM Studio before starting the app.
- Leave `LM_STUDIO_MODEL` blank for automatic detection, or set the exact model ID.

### The model takes too long
- Smaller local models may take time, especially on CPU.
- Keep the questionnaire text modest and ensure the model context window is sufficient.
- For the demo, avoid repeatedly pressing Generate while a request is still running.

### Text is missing from questionnaire extraction
- Open the "Extracted questionnaire text" expander.
- If it is empty, the PDF may be scanned or image-only and will need OCR.
