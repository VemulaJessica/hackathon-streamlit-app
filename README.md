# MedLens

MedLens is a Streamlit application for organizing patient-entered information and uploaded medical reports into a traceable, reviewable record. Missing information stays missing, extracted values retain their source, and laboratory status is calculated only from a usable reference range present in the same report.

## Features

- Patient intake with explicit `User Provided` provenance.
- PDF text extraction, TXT extraction, and optional image OCR.
- Conservative laboratory parsing with `LOW`, `NORMAL`, `HIGH`, or `NOT DETERMINABLE` status.
- Multiple reports, history comparison, search and filters.
- Verification workflow for confirm, edit, incorrect, or delete actions.
- Original extracted text alongside structured values.
- Deterministic safe summary fallback, with optional Gemini enhancement.
- JSON and CSV export with source and verification status.
- Possible inconsistency detection without choosing a “correct” source.

## Architecture

`app.py` contains the Streamlit UI, an in-session structured JSON record, local extraction/parsing functions, optional Gemini integration, and export helpers. Uploaded report bytes are processed in memory. The application does not log patient data or persist it to a database.

## Installation on Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For image OCR, install the Tesseract executable separately and ensure `tesseract.exe` is on `PATH`. PDF text extraction works through PyMuPDF without Tesseract.

## Gemini API key setup

Gemini is optional. Set the environment variable in PowerShell:

```powershell
$env:GEMINI_API_KEY = "your-key"
```

For Streamlit secrets, copy `.streamlit\secrets.toml.example` to `.streamlit\secrets.toml` and replace the placeholder. Never commit a real key. If the key or Gemini package is unavailable, MedLens uses its safe local summary generator.

## Run

```powershell
streamlit run app.py
```

## Example workflow

1. Enter only the patient information you want to store.
2. Upload a PDF, TXT, JPG, JPEG, or PNG report.
3. Process it and inspect the original extracted text.
4. Open Verification and confirm or correct extracted values.
5. Review Structured Record and Lab Results.
6. Generate the summary and export JSON or CSV.

The included `sample_lab_report.txt` is a safe local demonstration input. It includes report-provided ranges so the application can show comparable statuses.

## Safety and responsible AI

MedLens is an information organization and understanding tool. It does not diagnose diseases, prescribe treatment, recommend medication changes, or replace a qualified clinician. AI prompts explicitly prohibit invention of patient details, values, ranges, dates, diagnoses, medications, and observations. AI output is labeled `AI Summary`; report facts are labeled `Report Extracted`.

## Limitations

OCR depends on local Tesseract installation and source quality. Free-form report layouts may not parse into laboratory rows; those details remain in the original extracted text for human review. Reference ranges that are absent or ambiguous are never inferred. This demonstration stores data only in the active Streamlit session and is not a production medical-record system.
