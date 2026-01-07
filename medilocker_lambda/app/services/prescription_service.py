"""
Prescription service - extracts prescription points directly from medical documents using GPT-4 Vision.
"""
import base64
import json
import time
from typing import List, Dict, Any
from fastapi import HTTPException
from openai import OpenAI
from app.config import OPENAI_API_KEY
from app.logger import get_logger

logger = get_logger(__name__)


def _get_mime_type(filename: str) -> str:
    """Get MIME type from filename."""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(filename)
    
    if mime_type:
        return mime_type
    
    filename_lower = filename.lower()
    if filename_lower.endswith(".pdf"):
        return "application/pdf"
    elif filename_lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    elif filename_lower.endswith(".png"):
        return "image/png"
    elif filename_lower.endswith(".gif"):
        return "image/gif"
    elif filename_lower.endswith(".webp"):
        return "image/webp"
    else:
        return "application/octet-stream"


def extract_structured_data_from_files(
    files: List[Dict[str, str]]
) -> Dict[str, Any]:
    """
    Extract prescription points and patient details from medical documents using GPT-4 Vision.
    
    Args:
        files: List of dicts with 'filename' and 'content' (base64 encoded)
    
    Returns:
        Dict with 'prescription' key containing plain text prescription points,
        and optionally 'patient_details' with Name, Age, Gender, Diagnosis if available
    """
    start_time = time.time()
    
    logger.info(f"[PRESCRIPTION] Processing {len(files)} file(s)")
    
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is not set")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    if not files:
        logger.error("No files provided")
        raise HTTPException(status_code=400, detail="No files provided")
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Build user message with file contents
        user_content = []
        
        for file_data in files:
            filename = file_data.get("filename", "unknown")
            content = file_data.get("content", "")
            
            if not content:
                logger.warning(f"Skipping file {filename} - no content")
                continue
            
            try:
                file_bytes = base64.b64decode(content)
            except Exception as e:
                logger.error(f"Failed to decode base64 for {filename}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid base64 content for file {filename}")
            
            mime_type = _get_mime_type(filename)
            
            if mime_type.startswith("image/") or mime_type == "application/pdf":
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{content}"
                    }
                })
            else:
                try:
                    text_content = file_bytes.decode("utf-8")
                    user_content.append({
                        "type": "text",
                        "text": f"--- Document: {filename} ---\n{text_content}"
                    })
                except UnicodeDecodeError:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{content}"
                        }
                    })
        
        if not user_content:
            logger.error("No valid file content to process")
            raise HTTPException(status_code=400, detail="No valid file content to process")
        
        # Single GPT call to extract prescription and patient details from documents
        messages = [
            {
                "role": "system",
                "content": """You are a medical assistant helping doctors write short prescriptions and extract patient information.

                Read the document(s) carefully and extract:
                1. Patient details (if available): Name, Age, Gender, Diagnosis
                2. Prescription points: 4-5 concise prescription points for the "Rx" section

                TASK:
                Extract patient information and generate helpful prescription points based on the medical information present.

                PATIENT DETAILS EXTRACTION:
                - Name: Extract patient's full name if mentioned
                - Age: Extract patient's age (as number, e.g., 45) if mentioned
                - Gender: Extract patient's gender (Male/Female/Other) if mentioned
                - Diagnosis: Extract primary diagnosis or medical condition if mentioned
                - Only extract if explicitly present in the document
                - If any field is not found, use null for that field

                PRESCRIPTION POINTS:
                Generate 4-5 concise prescription points based on:
                - Any diagnosis, symptoms, or medical conditions mentioned
                - Any medications or treatments referenced
                - Any test results or lab values
                - Any medical history or notes
                - Clinical context from the document

                GUIDELINES:
                - Extract explicit prescription information if present (medicines, doses, frequencies)
                - If no explicit prescription exists, suggest appropriate medications, tests, or instructions based on the medical condition/diagnosis mentioned
                - Use clinical knowledge to provide helpful, relevant suggestions
                - Keep it practical and doctor-like
                - If the document mentions a condition (e.g., "hypertension", "diabetes", "fever"), suggest relevant medications or management
                - If lab results show abnormalities, suggest relevant follow-up tests or treatments

                OUTPUT FORMAT (JSON):
                {
                    "patient_details": {
                        "name": "Patient Name or null",
                        "age": 45 or null,
                        "gender": "Male/Female/Other or null",
                        "diagnosis": "Primary diagnosis or null"
                    },
                    "prescription": "Plain text prescription points, one per line, 4-5 points. Return 'No prescription details found.' only if absolutely no medical information exists."
                }

                EXAMPLES:
                If document mentions "fever and cough" for "John Doe, 35 years, Male":
                {
                    "patient_details": {
                        "name": "John Doe",
                        "age": 35,
                        "gender": "Male",
                        "diagnosis": "Fever and cough"
                    },
                    "prescription": "Tab Paracetamol 500 mg SOS for fever\\nTab Azithromycin 500 mg once daily for 3 days\\nSteam inhalation twice daily\\nReview if symptoms persist after 3 days"
                }

                If document mentions "hypertension" but no patient details:
                {
                    "patient_details": {
                        "name": null,
                        "age": null,
                        "gender": null,
                        "diagnosis": "Hypertension"
                    },
                    "prescription": "Tab Amlodipine 5 mg once daily\\nMonitor BP twice weekly\\nLow salt diet\\nReview after 2 weeks"
                }

                IMPORTANT:
                - Return valid JSON only
                - Use null (not "null" string) for missing patient detail fields
                - Prescription should be plain text with \\n for line breaks
                - If no patient details are found at all, all patient_detail fields should be null"""
            },
            {
                "role": "user",
                "content": user_content
            }
        ]
        
        try:
            logger.info(f"[PRESCRIPTION] Calling GPT-4o Vision API")
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            elapsed = time.time() - start_time
            logger.info(f"[PRESCRIPTION] API call completed in {elapsed:.2f}s")
        except Exception as api_error:
            elapsed = time.time() - start_time
            logger.error(f"[PRESCRIPTION] API call failed after {elapsed:.2f}s: {type(api_error).__name__}: {str(api_error)}")
            raise
        
        response_content = response.choices[0].message.content.strip()
        logger.info(f"[PRESCRIPTION] Response received ({len(response_content)} chars)")
        
        # Parse JSON response
        try:
            result_data = json.loads(response_content)
            prescription_text = result_data.get("prescription", "")
            patient_details = result_data.get("patient_details", {})
            
            logger.info(f"[PRESCRIPTION] Prescription extracted ({len(prescription_text)} chars)")
            logger.info(f"[PRESCRIPTION] Prescription: {prescription_text}")
            
            # Check if any patient details are available
            has_patient_details = any(
                patient_details.get(key) is not None 
                for key in ["name", "age", "gender", "diagnosis"]
            )
            
            if has_patient_details:
                logger.info(f"[PRESCRIPTION] Patient details found: {patient_details}")
                return {
                    "prescription": prescription_text,
                    "patient_details": patient_details
                }
            else:
                logger.info(f"[PRESCRIPTION] No patient details found in document")
                return {
                    "prescription": prescription_text
                }
        except json.JSONDecodeError as json_error:
            logger.warning(f"[PRESCRIPTION] Failed to parse JSON response, falling back to plain text: {str(json_error)}")
            logger.warning(f"[PRESCRIPTION] Response content: {response_content[:500]}")
            # Fallback: treat as plain text prescription (backward compatibility)
            return {
                "prescription": response_content
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {str(e)}")
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=f"Failed to extract prescription: {str(e)}")

