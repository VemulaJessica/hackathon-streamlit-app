from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

try:
    import fitz
except ImportError:
    fitz = None

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None

SOURCE_LABELS = {"USER_PROVIDED": "User Provided", "REPORT_EXTRACTED": "Report Extracted", "AI_SUMMARY": "AI Summary", "NOT_AVAILABLE": "Not Available"}
STATUS_LABELS = {"LOW": "LOW", "NORMAL": "NORMAL", "HIGH": "HIGH", "NOT_DETERMINABLE": "NOT DETERMINABLE"}


def blank_record() -> dict[str, Any]:
    return {"patient": {"patient_id": "", "name": "", "age": None, "sex": "", "date_of_birth": "", "symptoms": [], "existing_conditions": [], "allergies": [], "medications": [], "medical_history": [], "additional_notes": [], "source": "USER_PROVIDED"}, "reports": [], "lab_results": [], "observations": [], "inconsistencies": [], "ai_summary": "", "summary_source": "AI_SUMMARY"}


def init_state() -> None:
    st.session_state.setdefault("record", blank_record())
    st.session_state.setdefault("page", "Dashboard")


def provided(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "Not provided"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "Not provided"
    return str(value)


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_reference(raw: str) -> tuple[float | None, float | None]:
    text = (raw or "").replace(",", "").replace("–", "-").replace("—", "-").strip()
    match = re.search(r"^\s*([<>≤≥]?)[ ]*([0-9]+(?:\.[0-9]+)?)\s*(?:-|to)\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if match:
        return float(match.group(2)), float(match.group(3))
    match = re.search(r"^\s*([<>≤≥])\s*([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        number = float(match.group(2))
        return (None, number) if match.group(1) in ("<", "≤") else (number, None)
    return None, None


def classify(value: float | None, raw_range: str) -> str:
    lower, upper = parse_reference(raw_range)
    if value is None or (lower is None and upper is None):
        return "NOT_DETERMINABLE"
    if lower is not None and value < lower:
        return "LOW"
    if upper is not None and value > upper:
        return "HIGH"
    return "NORMAL"


def extract_text(file_name: str, data: bytes) -> tuple[str, str]:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".txt":
        return data.decode("utf-8", errors="replace"), "Extracted from text file"
    if suffix == ".pdf":
        if fitz is None:
            return "", "PDF support is unavailable. Install PyMuPDF."
        try:
            document = fitz.open(stream=data, filetype="pdf")
            text = "\n".join(page.get_text() for page in document).strip()
            return (text, "Extracted from PDF text layer") if text else ("", "No readable text found in PDF")
        except Exception:
            return "", "Unable to read this PDF"
    if suffix in {".jpg", ".jpeg", ".png"}:
        if Image is None or pytesseract is None:
            return "", "OCR support is unavailable. Install Pillow and pytesseract."
        try:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(data))).strip()
            return (text, "Extracted with OCR") if text else ("", "No readable text found in image")
        except Exception:
            return "", "Unable to run OCR on this image"
    return "", "Unsupported file type"


def report_date(text: str) -> str:
    for pattern in (r"(?:date|report date)\s*[:=-]\s*(\d{1,2}[-/][A-Za-z0-9]{3,9}[-/]\d{2,4})", r"(?:date|report date)\s*[:=-]\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})"):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


def parse_report(file_name: str, text: str, uploaded_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parsed_date = report_date(text)
    report = {"file_name": file_name, "upload_date": uploaded_at, "report_date": parsed_date, "extraction_status": "Extracted", "extracted_text": text, "text_preview": text[:5000]}
    results: list[dict[str, Any]] = []
    for line in (item.strip() for item in text.splitlines() if item.strip()):
        if re.search(r"test\s+name|result\s+unit|reference|status\s*:", line, re.I):
            continue
        match = re.match(r"^(.{2,35}?)\s{2,}([<>]?[0-9][0-9,]*(?:\.[0-9]+)?)\s+([^\s]+(?:/[^\s]+)?)\s{2,}(.+?)\s*$", line)
        if not match:
            match = re.match(r"^([A-Za-z][A-Za-z ()/%.-]{1,34})\s+([<>]?[0-9][0-9,]*(?:\.[0-9]+)?)\s+([^\s]+)\s+((?:[<>≤≥]?\s*)?[0-9].*)$", line)
        if not match:
            continue
        test_name, raw_value, unit, raw_range = [part.strip() for part in match.groups()]
        try:
            value = float(raw_value.replace(",", "").replace("<", "").replace(">", ""))
        except ValueError:
            value = None
        lower, upper = parse_reference(raw_range)
        results.append({"test_name": test_name, "value": value, "value_raw": raw_value, "unit": unit, "reference_range": {"raw": raw_range, "lower": lower, "upper": upper}, "status": classify(value, raw_range), "date": parsed_date, "observation": "", "source": "REPORT_EXTRACTED", "report_file": file_name, "confidence": "HIGH", "verification_status": "UNVERIFIED"})
    report["test_count"] = len(results)
    if not text:
        report["extraction_status"] = "Unable to reliably extract information"
    return report, results


def detect_inconsistencies(record: dict[str, Any]) -> list[dict[str, str]]:
    conflicts = []
    reports = record.get("reports", [])
    dates = {item.get("report_date") for item in reports if item.get("report_date")}
    if len(dates) > 1:
        conflicts.append({"information": "Uploaded reports have different report dates.", "source_1": reports[0]["file_name"], "source_2": reports[1]["file_name"]})
    by_name: dict[str, list[dict[str, Any]]] = {}
    for result in record.get("lab_results", []):
        by_name.setdefault(normalize_name(result["test_name"]), []).append(result)
    for name, items in by_name.items():
        if len({item.get("value_raw") for item in items}) > 1:
            conflicts.append({"information": f"The test '{name}' has different reported values.", "source_1": items[0].get("report_file", "Report"), "source_2": items[1].get("report_file", "Report")})
    return conflicts


def deterministic_summary(record: dict[str, Any]) -> str:
    patient, reports, results = record["patient"], record.get("reports", []), record.get("lab_results", [])
    identity = patient.get("name") or patient.get("patient_id") or "The patient"
    lines = [f"Patient Overview\n{identity} has information entered in the MedLens record. Provided fields are labeled User Provided."]
    details = ", ".join(f"{item['file_name']} ({provided(item.get('report_date'))})" for item in reports)
    lines.append(f"Report Overview\nUploaded report(s): {details}." if details else "Report Overview\nNo report has been uploaded.")
    if results:
        findings = "; ".join(f"{item['test_name']}: {item.get('value_raw', provided(item.get('value')))} {item.get('unit', '')} ({STATUS_LABELS[item['status']]}, according to the report range)" for item in results)
        notable = [item["test_name"] for item in results if item["status"] in {"LOW", "HIGH"}]
        lines.append(f"Laboratory Findings\nThe uploaded report lists {findings}.")
        lines.append(f"Notable Information\n{', '.join(notable)} are marked outside their provided report ranges." if notable else "Notable Information\nNo results are marked LOW or HIGH by the provided report ranges.")
    else:
        lines.append("Laboratory Findings\nNo laboratory results were reliably extracted.")
    missing = [label for key, label in (("name", "name"), ("age", "age"), ("symptoms", "symptoms"), ("allergies", "allergies")) if not patient.get(key)]
    lines.append(f"Missing Information\nNot provided: {', '.join(missing) if missing else 'none of the listed intake fields'}.")
    lines.append("Source Notice\nGenerated from user-provided information and uploaded medical reports. This is an information organization tool, not medical diagnosis or treatment.")
    return "\n\n".join(lines)


def generate_summary(record: dict[str, Any]) -> tuple[str, str]:
    api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")
    if genai and api_key:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = "You are extracting and organizing medical information. Use only information present in the provided source. Never invent missing patient information, laboratory values, reference ranges, dates, diagnoses, medications, or observations. Do not diagnose, prescribe, or recommend medication changes. Return a concise patient-friendly summary with sections Patient Overview, Report Overview, Laboratory Findings, Notable Information, Missing Information, and Source Notice. Structured record JSON:\n" + json.dumps(record, default=str)
            return model.generate_content(prompt).text.strip(), "AI_SUMMARY"
        except Exception:
            pass
    return deterministic_summary(record), "AI_SUMMARY"


def source_badge(source: str) -> str:
    colors = {"USER_PROVIDED": "#117a65", "REPORT_EXTRACTED": "#1769aa", "AI_SUMMARY": "#7048a8", "NOT_AVAILABLE": "#667085"}
    return f'<span class="source-badge" style="background:{colors.get(source, "#667085")}">{SOURCE_LABELS.get(source, source)}</span>'


def render_patient_form() -> None:
    patient = st.session_state.record["patient"]
    st.header("Patient Information")
    st.caption("Only fields you enter are stored. Empty fields remain Not provided.")
    with st.form("patient_form"):
        col1, col2 = st.columns(2)
        patient_id = col1.text_input("Patient ID", value=patient.get("patient_id", ""))
        name = col2.text_input("Name", value=patient.get("name", ""))
        age = col1.number_input("Age", min_value=0, max_value=130, value=patient.get("age") or 0, step=1)
        sex_options = ["", "Female", "Male", "Intersex", "Prefer not to say"]
        sex = col2.selectbox("Sex", sex_options, index=sex_options.index(patient.get("sex", "")) if patient.get("sex", "") in sex_options else 0)
        dob = col1.date_input("Date of Birth", value=None if not patient.get("date_of_birth") else datetime.strptime(patient["date_of_birth"], "%Y-%m-%d").date())
        symptoms = col2.text_area("Symptoms", value=provided(patient.get("symptoms")) if patient.get("symptoms") else "", placeholder="Separate multiple items with commas")
        conditions = col1.text_area("Existing Conditions", value=provided(patient.get("existing_conditions")) if patient.get("existing_conditions") else "")
        allergies = col2.text_area("Allergies", value=provided(patient.get("allergies")) if patient.get("allergies") else "")
        medications = col1.text_area("Current Medications", value=provided(patient.get("medications")) if patient.get("medications") else "")
        history = col2.text_area("Relevant Medical History", value=provided(patient.get("medical_history")) if patient.get("medical_history") else "")
        notes = st.text_area("Additional Notes", value=provided(patient.get("additional_notes")) if patient.get("additional_notes") else "")
        if st.form_submit_button("Save patient information", type="primary"):
            patient.update({"patient_id": patient_id.strip(), "name": name.strip(), "age": int(age) if age else None, "sex": sex, "date_of_birth": dob.isoformat() if dob else "", "symptoms": [x.strip() for x in symptoms.split(",") if x.strip()], "existing_conditions": [x.strip() for x in conditions.split(",") if x.strip()], "allergies": [x.strip() for x in allergies.split(",") if x.strip()], "medications": [x.strip() for x in medications.split(",") if x.strip()], "medical_history": [x.strip() for x in history.split(",") if x.strip()], "additional_notes": [x.strip() for x in notes.split(",") if x.strip()]})
            st.success("Patient information saved.")


def render_upload() -> None:
    st.header("Upload Reports")
    st.caption("PDF, JPG, JPEG, PNG, and TXT files are accepted. Extracted information stays tied to its source file.")
    files = st.file_uploader("Choose one or more medical reports", type=["pdf", "jpg", "jpeg", "png", "txt"], accept_multiple_files=True)
    if files and st.button("Process uploaded reports", type="primary"):
        for uploaded in files:
            if any(item["file_name"] == uploaded.name for item in st.session_state.record["reports"]):
                continue
            text, status = extract_text(uploaded.name, uploaded.getvalue())
            report, results = parse_report(uploaded.name, text, datetime.now().strftime("%Y-%m-%d %H:%M"))
            report["extraction_status"] = status if not text else report["extraction_status"]
            st.session_state.record["reports"].append(report)
            st.session_state.record["lab_results"].extend(results)
        st.session_state.record["inconsistencies"] = detect_inconsistencies(st.session_state.record)
        st.success(f"Processed {len(files)} report(s). Review extracted values before using them.")
    for report in st.session_state.record["reports"]:
        with st.expander(f"{report['file_name']} · {report.get('test_count', 0)} extracted tests"):
            st.write(f"Upload date: {report['upload_date']}  |  Report date: {provided(report.get('report_date'))}")
            st.write(f"Extraction status: {report['extraction_status']}")
            st.text_area("Original extracted text", value=report.get("text_preview", "Not clearly available in source report"), height=180, disabled=True, key=f"text_{report['file_name']}")


def result_table(results: list[dict[str, Any]]) -> None:
    if not results:
        st.info("No laboratory results are available.")
        return
    rows = [{"Test": x["test_name"], "Value": x.get("value_raw", provided(x.get("value"))), "Unit": provided(x.get("unit")), "Reference Range": provided(x["reference_range"].get("raw")), "Status": STATUS_LABELS[x["status"]], "Date": provided(x.get("date")), "Source": SOURCE_LABELS[x["source"]], "Verification": x["verification_status"]} for x in results]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_record() -> None:
    record = st.session_state.record
    st.header("Structured Record")
    st.subheader("Patient Information")
    patient_rows = [{"Field": key.replace("_", " ").title(), "Value": provided(value), "Source": SOURCE_LABELS["USER_PROVIDED"]} for key, value in record["patient"].items() if key != "source"]
    st.dataframe(patient_rows, use_container_width=True, hide_index=True)
    st.subheader("Laboratory Results")
    result_table(record["lab_results"])
    st.subheader("Observations")
    observations = [{"Observation": provided(item.get("observation")), "Date": provided(item.get("date")), "Source": SOURCE_LABELS[item.get("source", "NOT_AVAILABLE")]} for item in record.get("observations", [])]
    st.dataframe(observations or [{"Observation": "Not provided", "Date": "Not provided", "Source": SOURCE_LABELS["NOT_AVAILABLE"]}], use_container_width=True, hide_index=True)
    if record.get("inconsistencies"):
        st.subheader("Possible Inconsistency")
        for item in record["inconsistencies"]:
            st.warning(f"Information: {item['information']}\n\nSource 1: {item['source_1']}\n\nSource 2: {item['source_2']}\n\nStatus: Needs Human Verification")


def render_verification() -> None:
    st.header("Verification")
    st.caption("AI Extracted — Verification Required. Changes are explicit and the original report text remains available.")
    results = st.session_state.record["lab_results"]
    for index, item in enumerate(results):
        with st.expander(f"{item['test_name']} · {item['verification_status']} · {item['confidence']} confidence"):
            st.write(f"Source: {item.get('report_file', 'Uploaded Report')} | Original value: {item.get('value_raw', provided(item.get('value')))}")
            col1, col2, col3 = st.columns(3)
            edited_name = col1.text_input("Test name", item["test_name"], key=f"name_{index}")
            edited_value = col2.text_input("Value", item.get("value_raw", ""), key=f"value_{index}")
            action = col3.selectbox("Action", ["Leave unverified", "Confirm", "Mark incorrect", "Delete"], key=f"action_{index}")
            if st.button("Apply", key=f"apply_{index}"):
                if action == "Delete":
                    results.pop(index)
                    st.rerun()
                item["test_name"] = edited_name
                item["value_raw"] = edited_value
                try:
                    item["value"] = float(edited_value.replace(",", ""))
                except ValueError:
                    item["value"] = None
                item["status"] = classify(item["value"], item["reference_range"].get("raw", ""))
                item["verification_status"] = "VERIFIED" if action == "Confirm" else "INCORRECT" if action == "Mark incorrect" else "UNVERIFIED"
                st.success("Verification status updated.")
    if not results:
        st.info("No extracted items require verification.")


def render_lab_results() -> None:
    st.header("Lab Results")
    results = st.session_state.record["lab_results"]
    col1, col2, col3, col4 = st.columns(4)
    query = col1.text_input("Search test name")
    status = col2.selectbox("Status", ["All", "LOW", "NORMAL", "HIGH", "NOT DETERMINABLE"])
    source = col3.selectbox("Source", ["All", "Report Extracted"])
    verification = col4.selectbox("Verification", ["All", "VERIFIED", "UNVERIFIED", "INCORRECT"])
    filtered = [item for item in results if (not query or query.lower() in item["test_name"].lower()) and (status == "All" or STATUS_LABELS[item["status"]] == status) and (source == "All" or SOURCE_LABELS[item["source"]] == source) and (verification == "All" or item["verification_status"] == verification)]
    result_table(filtered)


def render_history() -> None:
    st.header("Previous Reports")
    reports = st.session_state.record["reports"]
    if not reports:
        st.info("Upload at least two reports to compare report history.")
        return
    st.dataframe([{"Report": x["file_name"], "Report date": provided(x.get("report_date")), "Upload date": x["upload_date"], "Extracted tests": x.get("test_count", 0)} for x in reports], use_container_width=True, hide_index=True)
    if len(reports) < 2:
        return
    names = [x["file_name"] for x in reports]
    previous = st.selectbox("Previous report", names, key="previous_report")
    current = st.selectbox("Current report", names, index=1, key="current_report")
    if previous == current:
        st.info("Select two different reports to compare.")
        return
    previous_items = {normalize_name(x["test_name"]): x for x in st.session_state.record["lab_results"] if x.get("report_file") == previous}
    current_items = {normalize_name(x["test_name"]): x for x in st.session_state.record["lab_results"] if x.get("report_file") == current}
    rows = []
    for name in sorted(set(previous_items) | set(current_items)):
        old, new = previous_items.get(name), current_items.get(name)
        rows.append({"Test": (new or old)["test_name"], "Previous": provided(old.get("value_raw")) if old else "Not available in selected report", "Current": provided(new.get("value_raw")) if new else "Not available in selected report", "Previous Status": STATUS_LABELS[old["status"]] if old else "Not available in selected report", "Current Status": STATUS_LABELS[new["status"]] if new else "Not available in selected report"})
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_summary() -> None:
    st.header("AI Summary")
    st.caption("This summary describes available structured information. It does not diagnose or recommend treatment.")
    if st.button("Generate patient-friendly summary", type="primary"):
        summary, source = generate_summary(st.session_state.record)
        st.session_state.record["ai_summary"] = summary
        st.session_state.record["summary_source"] = source
    if st.session_state.record.get("ai_summary"):
        st.markdown(st.session_state.record["ai_summary"])
        st.markdown(source_badge("AI_SUMMARY"), unsafe_allow_html=True)
    else:
        st.info("Generate a summary after entering patient information or uploading a report.")


def render_export() -> None:
    st.header("Export")
    record = st.session_state.record
    json_data = json.dumps(record, indent=2, default=str)
    csv_buffer = io.StringIO()
    fields = ["test_name", "value_raw", "unit", "reference_range", "status", "date", "source", "verification_status"]
    writer = csv.DictWriter(csv_buffer, fieldnames=fields)
    writer.writeheader()
    for item in record["lab_results"]:
        row = dict(item)
        row["reference_range"] = item["reference_range"].get("raw", "")
        writer.writerow({field: row.get(field, "") for field in fields})
    col1, col2 = st.columns(2)
    col1.download_button("Download JSON", json_data, "medlens_record.json", "application/json", use_container_width=True)
    col2.download_button("Download CSV", csv_buffer.getvalue(), "medlens_lab_results.csv", "text/csv", use_container_width=True)
    st.caption("Exports include only entered, extracted, verified, or explicitly unavailable information in the current record.")


def render_dashboard() -> None:
    record = st.session_state.record
    st.header("MedLens Dashboard")
    st.write("AI-Powered Clinical Information Intelligence")
    metrics = st.columns(5)
    metrics[0].metric("Patient records", 1 if any(record["patient"].values()) else 0)
    metrics[1].metric("Uploaded reports", len(record["reports"]))
    metrics[2].metric("Extracted lab results", len(record["lab_results"]))
    metrics[3].metric("Need verification", sum(x["verification_status"] == "UNVERIFIED" for x in record["lab_results"]))
    metrics[4].metric("Inconsistencies", len(record["inconsistencies"]))
    st.subheader("Workflow")
    st.markdown("**Patient Information**  →  **Upload Report**  →  **Extract**  →  **Verify**  →  **Structured Record**  →  **AI Summary**")
    st.subheader("Data boundaries")
    st.info("User information comes only from the Patient Information form. Report information comes only from uploaded files. AI summaries use the collected structured record and never replace professional diagnosis or treatment.")
    if record["lab_results"]:
        st.subheader("Latest extracted results")
        result_table(record["lab_results"][:5])


def main() -> None:
    st.set_page_config(page_title="MedLens", page_icon="+", layout="wide", initial_sidebar_state="expanded")
    st.markdown("""<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@700;800&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    h1, h2, h3 { font-family: 'Manrope', sans-serif; color: #12343b; }
    [data-testid="stSidebar"] { background: #eaf4f2; border-right: 1px solid #c9dfdc; }
    .source-badge { color: white; padding: 3px 9px; border-radius: 999px; font-size: 0.78rem; font-weight: 600; display: inline-block; }
    .disclaimer { background: #fff8e7; border-left: 4px solid #e09f3e; padding: 0.8rem 1rem; color: #614a1c; }
    </style>""", unsafe_allow_html=True)
    init_state()
    with st.sidebar:
        st.markdown("# + MedLens")
        st.caption("AI-Powered Clinical Information Intelligence")
        pages = ["Dashboard", "Patient Information", "Upload Reports", "Structured Record", "Lab Results", "AI Summary", "Previous Reports", "Verification", "Export"]
        st.session_state.page = st.radio("Navigate", pages, index=pages.index(st.session_state.page), label_visibility="collapsed")
        st.markdown('<div class="disclaimer">MedLens organizes information and supports understanding. It does not replace professional medical diagnosis or treatment.</div>', unsafe_allow_html=True)
        if st.button("Clear current record"):
            st.session_state.record = blank_record()
            st.rerun()
    page = st.session_state.page
    {"Dashboard": render_dashboard, "Patient Information": render_patient_form, "Upload Reports": render_upload, "Structured Record": render_record, "Lab Results": render_lab_results, "AI Summary": render_summary, "Previous Reports": render_history, "Verification": render_verification, "Export": render_export}[page]()


if __name__ == "__main__":
    main()