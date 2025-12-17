"""
Prescription service - handles prescription data extraction using GPT-4 Vision.
"""
import base64
import json
import time
from typing import List, Dict, Optional, Any
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
    files: List[Dict[str, str]], 
    frontend_patient_details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract patient details and medical data from files, then generate a prescription report.
    
    Args:
        files: List of dicts with 'filename' and 'content' (base64 encoded)
        frontend_patient_details: Optional patient details from frontend to merge when GPT returns empty fields
    
    Returns:
        Dict with 'patient_details' and 'prescription_report'
    """
    overall_start_time = time.time()
    
    logger.info("🔵 [EXTRACTION] Starting extract_structured_data_from_files")
    logger.info(f"🔵 [EXTRACTION] Input: {len(files)} file(s), has_frontend_details={frontend_patient_details is not None}")
    
    if not OPENAI_API_KEY:
        logger.error("❌ [EXTRACTION] OPENAI_API_KEY is not set")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    if not files:
        logger.error("❌ [EXTRACTION] No files provided")
        raise HTTPException(status_code=400, detail="No files provided")
    
    try:
        logger.info("🔵 [EXTRACTION] Initializing OpenAI client")
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Step 1: Extract patient details and medical data
        logger.info("🔵 [EXTRACTION] Step 1: Preparing extraction messages")
        extraction_start_time = time.time()
        
        extraction_messages = [
            {
                "role": "system",
                "content": """You are a medical document extraction AI. Your task is to extract patient information and all medical/prescription-related data from documents (PDFs, images, or text files).

CRITICAL INSTRUCTIONS:
1. Extract ONLY information that is explicitly present in the document. Do NOT hallucinate or invent any data.
2. If a field is not present in the document, return an empty string "" for that field.
3. Return EXACT JSON format as specified below - no markdown, no code blocks, just pure JSON.
4. For medical_data, extract ALL important information related to the patient's medical condition, diagnosis, medications, treatments, lab results, doctor notes, and any prescription-related information. Include everything that would be relevant for generating a prescription report.

Extract the following structure:

{
  "patient_details": {
    "name": "",
    "patient_id": "",
    "age": "",
    "dob": "",
    "sex": "",
    "weight": "",
    "allergies": "",
    "pregnancy_bf": ""
  },
  "medical_data": ""
}

The medical_data field should contain all important medical information, prescription details, diagnosis, medications, dosages, instructions, lab results, doctor notes, and any other relevant medical information found in the document(s). Format it as clear, organized text.

Return ONLY the JSON object, nothing else."""
            }
        ]
        
        # Build user message with file contents
        logger.info("🔵 [EXTRACTION] Processing files and building user content")
        user_content = []
        
        for idx, file_data in enumerate(files):
            filename = file_data.get("filename", "unknown")
            content = file_data.get("content", "")
            content_length = len(content) if content else 0
            
            logger.info(f"🔵 [EXTRACTION] Processing file {idx + 1}/{len(files)}: '{filename}' ({content_length} bytes)")
            
            if not content:
                logger.warning(f"⚠️ [EXTRACTION] Skipping file {filename} - no content provided")
                continue
            
            # Decode base64 content
            try:
                logger.debug(f"🔵 [EXTRACTION] Decoding base64 for '{filename}'")
                file_bytes = base64.b64decode(content)
                logger.debug(f"✅ [EXTRACTION] Decoded '{filename}': {len(file_bytes)} bytes")
            except Exception as e:
                logger.error(f"❌ [EXTRACTION] Failed to decode base64 content for '{filename}': {type(e).__name__}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid base64 content for file {filename}")
            
            # Determine MIME type from filename
            mime_type = _get_mime_type(filename)
            logger.debug(f"🔵 [EXTRACTION] Detected MIME type for '{filename}': {mime_type}")
            
            # Add file to message as image/document
            if mime_type.startswith("image/"):
                logger.info(f"🔵 [EXTRACTION] Adding '{filename}' as image (MIME: {mime_type})")
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{content}"
                    }
                })
            elif mime_type == "application/pdf":
                logger.info(f"🔵 [EXTRACTION] Adding '{filename}' as PDF")
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{content}"
                    }
                })
            else:
                # Try to decode as text
                try:
                    logger.debug(f"🔵 [EXTRACTION] Attempting to decode '{filename}' as text")
                    text_content = file_bytes.decode("utf-8")
                    text_length = len(text_content)
                    logger.info(f"✅ [EXTRACTION] Decoded '{filename}' as text: {text_length} characters")
                    user_content.append({
                        "type": "text",
                        "text": f"--- Document: {filename} ---\n{text_content}"
                    })
                except UnicodeDecodeError:
                    logger.warning(f"⚠️ [EXTRACTION] Could not decode '{filename}' as text, treating as binary")
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{content}"
                        }
                    })
        
        logger.info(f"✅ [EXTRACTION] Processed {len(user_content)} file(s) into user content")
        
        if not user_content:
            logger.error("❌ [EXTRACTION] No valid file content to process after filtering")
            raise HTTPException(status_code=400, detail="No valid file content to process")
        
        # Add instruction text for extraction
        logger.info("🔵 [EXTRACTION] Building extraction messages for GPT API")
        user_content_extraction = user_content.copy()
        user_content_extraction.insert(0, {
            "type": "text",
            "text": "Extract patient details and all medical/prescription-related data from the following document(s). Return ONLY valid JSON matching the specified structure."
        })
        
        extraction_messages.append({
            "role": "user",
            "content": user_content_extraction
        })
        
        # Step 1: Call GPT-4 Vision API to extract patient details and medical data
        logger.info("=" * 80)
        logger.info(f"🚀 [EXTRACTION] Step 1: Calling GPT-4o Vision API to extract data from {len(files)} file(s)")
        logger.info(f"🚀 [EXTRACTION] Model: gpt-4o, Temperature: 0.1, Response Format: json_object")
        
        try:
            extraction_response = client.chat.completions.create(
                model="gpt-4o",
                messages=extraction_messages,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            extraction_elapsed = time.time() - extraction_start_time
            logger.info(f"✅ [EXTRACTION] GPT-4o extraction API call completed in {extraction_elapsed:.2f} seconds")
            logger.info(f"✅ [EXTRACTION] Response tokens: {extraction_response.usage.total_tokens if hasattr(extraction_response, 'usage') else 'N/A'}")
        except Exception as api_error:
            extraction_elapsed = time.time() - extraction_start_time
            logger.error(f"❌ [EXTRACTION] GPT-4o API call failed after {extraction_elapsed:.2f} seconds")
            logger.error(f"❌ [EXTRACTION] API Error: {type(api_error).__name__}: {str(api_error)}")
            raise
        
        # Parse extraction response
        extraction_response_text = extraction_response.choices[0].message.content.strip()
        response_preview = extraction_response_text[:200] + "..." if len(extraction_response_text) > 200 else extraction_response_text
        logger.info(f"✅ [EXTRACTION] Extraction response received ({len(extraction_response_text)} chars): {response_preview}")
        
        # Parse JSON response
        logger.info("🔵 [EXTRACTION] Parsing JSON response")
        try:
            extracted_data = json.loads(extraction_response_text)
            logger.info(f"✅ [EXTRACTION] JSON parsed successfully")
        except json.JSONDecodeError as e:
            logger.error(f"❌ [EXTRACTION] Failed to parse JSON response: {type(e).__name__}: {str(e)}")
            logger.error(f"❌ [EXTRACTION] Response text (first 500 chars): {extraction_response_text[:500]}")
            raise HTTPException(status_code=500, detail="Failed to parse extraction response as JSON")
        
        # Ensure structure exists
        logger.info("🔵 [EXTRACTION] Validating extracted data structure")
        if "patient_details" not in extracted_data:
            logger.warning("⚠️ [EXTRACTION] 'patient_details' missing in response, initializing empty dict")
            extracted_data["patient_details"] = {}
        if "medical_data" not in extracted_data:
            logger.warning("⚠️ [EXTRACTION] 'medical_data' missing in response, initializing empty string")
            extracted_data["medical_data"] = ""
        
        logger.info(f"✅ [EXTRACTION] Extracted patient_details keys: {list(extracted_data.get('patient_details', {}).keys())}")
        logger.info(f"✅ [EXTRACTION] Medical data length: {len(extracted_data.get('medical_data', ''))} characters")
        
        # Merge frontend patient_details only when GPT returns empty fields
        if frontend_patient_details and isinstance(frontend_patient_details, dict):
            logger.info("🔵 [EXTRACTION] Merging frontend patient_details with extracted data")
            patient_details = extracted_data.get("patient_details", {})
            merged_count = 0
            
            for key, value in frontend_patient_details.items():
                if key not in patient_details or not patient_details.get(key):
                    if value:
                        patient_details[key] = value
                        merged_count += 1
                        logger.debug(f"🔵 [EXTRACTION] Merged frontend field: {key} = {value}")
            
            extracted_data["patient_details"] = patient_details
            logger.info(f"✅ [EXTRACTION] Merged {merged_count} field(s) from frontend patient_details")
        else:
            logger.info("🔵 [EXTRACTION] No frontend patient_details provided, skipping merge")
        
        # Step 2: Generate prescription report from medical data
        logger.info("=" * 80)
        prescription_start_time = time.time()
        medical_data = extracted_data.get("medical_data", "")
        
        if not medical_data or not medical_data.strip():
            logger.warning("⚠️ [EXTRACTION] Step 2: No medical data extracted, skipping prescription report generation")
            prescription_report = ""
        else:
            logger.info(f"🚀 [EXTRACTION] Step 2: Generating prescription report from extracted medical data ({len(medical_data)} chars)")
            logger.info(f"🚀 [EXTRACTION] Model: gpt-4o, Temperature: 0.3")
            
            prescription_messages = [
                {
                    "role": "system",
                    "content": """You are a medical AI assistant. Generate a concise, professional prescription report based on the medical data provided.

CRITICAL INSTRUCTIONS:
1. Generate a clear, organized prescription report based ONLY on the medical data provided.
2. Include all relevant medications, dosages, instructions, diagnosis, and treatment plans.
3. Format the report in a professional, readable manner.
4. Do not add information that is not present in the medical data.
5. Keep the report concise but comprehensive."""
                },
                {
                    "role": "user",
                    "content": f"""Based on the following medical data, generate a prescription report:

{medical_data}

Generate a clear, professional prescription report that includes all relevant information about medications, dosages, instructions, diagnosis, and treatment plans."""
                }
            ]
            
            try:
                prescription_response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=prescription_messages,
                    temperature=0.3,
                )
                prescription_elapsed = time.time() - prescription_start_time
                logger.info(f"✅ [EXTRACTION] Prescription report API call completed in {prescription_elapsed:.2f} seconds")
                logger.info(f"✅ [EXTRACTION] Response tokens: {prescription_response.usage.total_tokens if hasattr(prescription_response, 'usage') else 'N/A'}")
            except Exception as api_error:
                prescription_elapsed = time.time() - prescription_start_time
                logger.error(f"❌ [EXTRACTION] Prescription report API call failed after {prescription_elapsed:.2f} seconds")
                logger.error(f"❌ [EXTRACTION] API Error: {type(api_error).__name__}: {str(api_error)}")
                raise
            
            prescription_report = prescription_response.choices[0].message.content.strip()
            logger.info(f"✅ [EXTRACTION] Prescription report generated successfully ({len(prescription_report)} characters)")
            report_preview = prescription_report[:200] + "..." if len(prescription_report) > 200 else prescription_report
            logger.info(f"✅ [EXTRACTION] Report preview: {report_preview}")
        
        # Return final structure
        overall_elapsed = time.time() - overall_start_time
        result = {
            "patient_details": extracted_data.get("patient_details", {}),
            "prescription_report": prescription_report
        }
        
        logger.info("=" * 80)
        logger.info(f"✅ [EXTRACTION] Successfully completed extraction and report generation")
        logger.info(f"✅ [EXTRACTION] Total time: {overall_elapsed:.2f} seconds")
        logger.info(f"✅ [EXTRACTION] Final result summary:")
        logger.info(f"   - Patient details fields: {len(result.get('patient_details', {}))}")
        logger.info(f"   - Prescription report length: {len(result.get('prescription_report', ''))} characters")
        logger.info("=" * 80)
        
        return result
    
    except HTTPException:
        overall_elapsed = time.time() - overall_start_time
        logger.error(f"❌ [EXTRACTION] HTTPException raised after {overall_elapsed:.2f} seconds")
        raise
    except Exception as e:
        overall_elapsed = time.time() - overall_start_time
        logger.error(f"❌ [EXTRACTION] Unexpected error after {overall_elapsed:.2f} seconds: {type(e).__name__}: {str(e)}")
        logger.exception("❌ [EXTRACTION] Full exception traceback:")
        raise HTTPException(status_code=500, detail=f"Failed to extract structured data: {str(e)}")

