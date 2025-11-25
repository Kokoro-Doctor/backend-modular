import io
import mimetypes
from typing import List, Optional
from fastapi import HTTPException
from openai import OpenAI
import PyPDF2
from app.config import s3_client, S3_BUCKET, S3_FOLDER_PREFIX, OPENAI_API_KEY
from app.logger import get_logger

logger = get_logger(__name__)

# Optional imports for image processing
try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("OCR libraries (PIL/pytesseract) not available. Image text extraction will be limited.")

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF file."""
    try:
        pdf_file = io.BytesIO(file_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to extract text from PDF: {str(e)}")

def extract_text_from_image(file_bytes: bytes) -> str:
    """Extract text from image using OCR."""
    if not OCR_AVAILABLE:
        logger.warning("OCR not available. Cannot extract text from image.")
        return "[Image file - text extraction not available. Please provide text-based documents for prescription generation.]"
    
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from image: {e}")
        return f"[Image file - OCR extraction failed: {str(e)}]"

def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """Extract text from file based on its type."""
    content_type, _ = mimetypes.guess_type(filename)
    
    if content_type == "application/pdf":
        return extract_text_from_pdf(file_bytes)
    elif content_type and content_type.startswith("image/"):
        return extract_text_from_image(file_bytes)
    else:
        # Try to decode as text
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(f"Could not extract text from {filename}, content_type: {content_type}")
            return ""

def download_and_extract_documents(user_id: str, filenames: Optional[List[str]] = None) -> str:
    """Download documents from S3 and extract text from them."""
    try:
        # List all files for the user
        prefix = f"{S3_FOLDER_PREFIX}{user_id}/"
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        all_files = response.get("Contents", [])
        
        if not all_files:
            raise HTTPException(status_code=404, detail="No documents found for this user")
        
        extracted_texts = []
        
        # Filter files if specific filenames are provided
        if filenames:
            files_to_process = [
                obj for obj in all_files 
                if obj["Key"].split(f"{prefix}", 1)[1] in filenames
            ]
            if not files_to_process:
                raise HTTPException(status_code=404, detail="Specified files not found")
        else:
            files_to_process = all_files
        
        # Download and extract text from each file
        for obj in files_to_process:
            key = obj["Key"]
            filename = key.split(f"{prefix}", 1)[1]
            
            try:
                # Download file from S3
                file_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                file_bytes = file_obj["Body"].read()
                
                # Extract text
                text = extract_text_from_file(file_bytes, filename)
                if text:
                    extracted_texts.append(f"--- Document: {filename} ---\n{text}\n")
                else:
                    logger.warning(f"No text extracted from {filename}")
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                continue
        
        if not extracted_texts:
            raise HTTPException(status_code=500, detail="Could not extract text from any documents")
        
        return "\n\n".join(extracted_texts)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error downloading and extracting documents")
        raise HTTPException(status_code=500, detail=f"Failed to process documents: {str(e)}")

def generate_prescription(document_text: str, patient_symptoms: Optional[str] = None) -> str:
    """Generate prescription using OpenAI API based on document text."""
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is not set")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Build the prompt
        prompt = f"""You are a medical AI assistant. Based on the following medical documents and patient information, generate a comprehensive prescription.

Medical Documents:
{document_text}
"""
        
        if patient_symptoms:
            prompt += f"""
Patient's Current Symptoms/Complaints:
{patient_symptoms}
"""
        
        prompt += """
Please generate a professional prescription in the following EXACT format:

------------------------------------------------------------

[Clinic/Hospital Logo]         PRESCRIPTION No: _____

Clinic name:

Address:

Phone / Email:



Date: DD-MM-YYYY

Patient Name: ________________    Patient ID: ________

Age / DOB: _____   Sex: M / F / O   Weight: __ kg

Allergies: ____________________    Pregnancy/BF: Yes/No

Diagnosis / Indication: ____________________



Medications:

1) DRUG (generic, CAPS): ______  Strength: __   Form: __

   Dose: __    Route: __    Frequency: __    Duration: __ days

   Qty to dispense: __ units     Repeats: __

   Instructions: _________________________________________



2) DRUG (generic): ...

   ...



Lab tests / Investigations ordered: ____________________

Advice / counselling: __________________________________

Follow-up: ____________________



Prescribed by:

Dr. Full Name (as registered): __________________

Qualification: _________   Registration No.: __________

Clinic / Dept.: __________________   Signature / e-sign: _____

[Clinic stamp if physical]



Important: Prescribe generic names in CAPITALS. Check allergies and renal function before prescribing renally excreted medicines. 

------------------------------------------------------------

IMPORTANT INSTRUCTIONS:
- Fill in all the fields based on the medical documents and patient symptoms provided
- Use generic drug names in CAPITAL LETTERS
- Include proper dosages, frequencies, routes, and durations
- Format the prescription exactly as shown above with proper spacing and line breaks
- Use underscores (___) for empty fields that need to be filled
- Include all relevant medications, lab tests, advice, and follow-up information
- Maintain the exact structure and formatting
"""
        
        response = client.chat.completions.create(
            model="gpt-4",  # Using GPT-4 for better medical understanding
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional medical AI assistant. Generate accurate, clear, and professional prescriptions based on medical documents. Always include proper dosages, frequencies, and clear instructions. You MUST format prescriptions exactly as specified in the user's format template, maintaining the exact structure, spacing, and field layout. Use generic drug names in CAPITAL LETTERS."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # Lower temperature for more consistent, factual responses
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        logger.error(f"OpenAI API error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to generate prescription: {str(e)}")

