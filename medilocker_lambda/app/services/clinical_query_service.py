"""
Clinical query service - answers doctor's questions about patient medical history.

Uses patient context (built from stored documents) and GPT to provide
concise, factual answers.
"""
import json
from typing import Dict, Any

from openai import OpenAI

from app.config import CLAIM_REASONING_MODEL
from app.logger import get_logger
from app.utils.openai_errors import to_http_exception

logger = get_logger(__name__)


def answer_clinical_query(
    patient_context: Dict[str, Any],
    question: str,
    client: OpenAI,
) -> Dict[str, Any]:
    """
    Answer doctor's clinical question using patient history.

    Args:
        patient_context: Patient context built by context_service.build_patient_context()
        question: Doctor's natural-language question
        client: OpenAI client instance

    Returns:
        Dict with 'answer' key containing the response text
    """
    logger.info("[CLINICAL_QUERY] Generating answer")

    has_question = bool(question and question.strip())

    QUESTION_PROMPT = """
        You are a senior clinical assistant helping a physician interpret patient medical records.

        You will receive:
        - patient medical context
        - a doctor's clinical question

        Your task is to answer the doctor's question using ONLY the provided data.

        RULES:
        - Answer the doctor's question directly.
        - Use only the provided patient information.
        - Do NOT hallucinate diagnoses, medications, or lab values.
        - If the data does not support the answer, say the information is unavailable.
        - Be concise and clinically precise.
        - Write for a physician audience.
    """

    FULL_ANALYSIS_PROMPT = """
        You are a senior clinical medical assistant helping a physician analyze a patient's full medical record.

        You will receive structured medical history extracted from real medical documents including:
        - lab reports
        - imaging
        - prescriptions
        - vitals
        - clinical notes
        - patient demographics

        Your job is to produce a structured **full clinical case analysis** that helps a doctor quickly understand the patient.

        CRITICAL RULES:
        - Use ONLY the provided information.
        - NEVER hallucinate diagnoses, medications, or lab values.
        - If data is missing, explicitly state that it is not available.
        - Prefer newer documents when conflicts exist.
        - Base reasoning strictly on available medical evidence.
        - Be concise but clinically informative.
        - Write in a tone appropriate for physicians.
        - Do NOT include safety disclaimers.

        The goal is to assist a doctor in reviewing a patient's entire medical record quickly.

        ---

        OUTPUT FORMAT (STRICT MARKDOWN)

        ---

        # Executive Clinical Summary
        Provide a concise but comprehensive overview of the patient's current clinical situation including:
        - major findings
        - suspected disease processes
        - key abnormalities
        - relevant comorbidities

        Limit to 5–8 sentences.

        ---

        # Key Clinical Findings
        Summarize the most important findings from the reports.

        ### Laboratory Findings
        List important lab abnormalities and their significance.

        ### Imaging Findings
        Summarize notable imaging observations.

        ### Vital / Clinical Indicators
        Include any important vitals, measurements, or clinical observations.

        ---

        # Abnormal Results
        List clearly abnormal findings detected in the reports.

        Format:

        - **Test / Parameter**
        - Result
        - Reference if available
        - Clinical significance (1 sentence)

        Only include abnormal or clinically significant findings.

        ---

        # Clinical Interpretation
        Explain what the findings collectively suggest about the patient's condition.

        Focus on:
        - disease mechanisms
        - organ systems involved
        - severity indicators
        - relationships between abnormal findings

        Avoid speculation beyond available data.

        ---

        # Differential Diagnosis

        Provide the most plausible diagnoses supported by the findings.

        ### 1. <Diagnosis Name>
        **Confidence:** High | Moderate | Low

        Clinical reasoning explaining how the findings support this diagnosis.

        ### 2. <Diagnosis Name>
        **Confidence:** High | Moderate | Low

        Explanation based strictly on evidence.

        ### 3. <Diagnosis Name>
        **Confidence:** High | Moderate | Low

        Explanation.

        Only include diagnoses supported by available evidence.

        If no diagnosis can be inferred say:

        "No clear differential diagnosis available from the provided data."

        ---

        # Risk Factors & Comorbidities
        List known or inferred patient risk factors based on the data.

        Examples:
        - diabetes indicators
        - metabolic risk
        - age related risks
        - organ dysfunction indicators

        If none available, say so.

        ---

        # Missing or Unclear Information
        Identify important medical information that is missing but would be useful for diagnosis.

        Examples:
        - symptoms
        - medication history
        - prior diagnoses
        - imaging confirmation
        - repeat labs

        ---

        # Recommended Diagnostic Follow-Up
        List tests or evaluations that could help confirm or clarify the suspected conditions.

        Example categories:
        - laboratory tests
        - imaging
        - specialist consultation
        - monitoring tests

        Do NOT invent results.

        ---

        # Suggested Clinical Questions
        Provide useful follow-up questions the physician might explore.

        Examples:
        - Explain the significance of elevated bilirubin.
        - Could pancreatic enzyme elevation indicate pancreatitis?
        - What additional tests confirm chronic liver disease?
        - How severe is the patient's glycemic dysregulation?

        ---

        IMPORTANT:
        - Use only evidence present in the provided data.
        - Do not invent results or diseases.
        - If information is insufficient for any section, clearly state that it is unavailable.
    """

    system_prompt = QUESTION_PROMPT if has_question else FULL_ANALYSIS_PROMPT

    logger.info(f"[CLINICAL_QUERY] Mode={'QUESTION' if has_question else 'FULL_ANALYSIS'}")

    if has_question:
        user_input = {
            "doctor_question": question,
            "patient_context": patient_context,
        }
    else:
        user_input = {
            "patient_context": patient_context,
        }

    try:
        response = client.chat.completions.create(
            # model="gpt-4o",
            model=CLAIM_REASONING_MODEL,
                                    temperature=0.2,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_input, ensure_ascii=False),
                },
            ],
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        raise to_http_exception(e, "[CLINICAL_QUERY]")

    logger.info(f"[CLINICAL_QUERY] Answer generated (length={len(answer)})")

    return {"answer": answer}
