import streamlit as st

st.set_page_config(
    page_title="MedLens",
    page_icon="🏥"
)

st.title("🏥 MedLens")
st.write("AI-Powered Clinical Information Intelligence")

st.header("👤 Patient Information")

name = st.text_input("Patient Name")
age = st.number_input("Age", min_value=0, max_value=120, step=1)

sex = st.selectbox(
    "Sex",
    ["Select", "Male", "Female", "Other"]
)

symptoms = st.text_area("Symptoms")
allergies = st.text_area("Allergies")
conditions = st.text_area("Existing Conditions")
medications = st.text_area("Current Medications")

st.header("📄 Medical Report")

report = st.file_uploader(
    "Upload Medical Report",
    type=["pdf", "txt", "png", "jpg", "jpeg"]
)

if st.button("Process Report"):
    st.success("Report uploaded successfully!")

    st.header("📋 Patient Record")

    st.write("**Name:**", name)
    st.write("**Age:**", age)
    st.write("**Sex:**", sex)
    st.write("**Symptoms:**", symptoms)
    st.write("**Allergies:**", allergies)
    st.write("**Existing Conditions:**", conditions)
    st.write("**Current Medications:**", medications)

    st.info(
        "This is an informational summary and not a medical diagnosis "
        "or treatment recommendation."
    )