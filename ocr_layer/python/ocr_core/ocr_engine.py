"""
OCR engine — pure Textract calls.

Two public functions:
  extract_from_image  — DetectDocumentText with raw bytes (sync, images only)
  extract_from_pdf_s3 — StartDocumentTextDetection + polling (async, PDFs via S3)

Both return a plain str of extracted text and raise RuntimeError on failure.
Neither function knows about users, DynamoDB, or LLMs.
"""
import time
import logging

from ocr_core.textract_client import get_client
from ocr_core.helpers import collect_line_blocks

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 3  # seconds between GetDocumentTextDetection calls


def extract_from_image(image_bytes: bytes) -> str:
    """
    Extract text from an image using Textract DetectDocumentText.

    Synchronous — blocks until Textract responds (typically 1–4 s).

    Args:
        image_bytes: Raw image bytes (jpg, png, heic, webp, tiff, bmp …)

    Returns:
        Extracted text as a newline-joined string.

    Raises:
        RuntimeError: On any Textract API or parsing error.
    """
    start = time.time()
    try:
        logger.info(f"[OCR_CORE] DetectDocumentText ({len(image_bytes)} bytes)")
        response = get_client().detect_document_text(
            Document={"Bytes": image_bytes}
        )
        lines = collect_line_blocks(response)
        text = "\n".join(lines)
        logger.info(
            f"[OCR_CORE] DetectDocumentText done in {time.time()-start:.2f}s "
            f"— {len(text)} chars, {len(lines)} lines"
        )
        return text
    except Exception as exc:
        elapsed = time.time() - start
        logger.error(
            f"[OCR_CORE] DetectDocumentText failed after {elapsed:.2f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        raise RuntimeError(f"DetectDocumentText failed: {exc}") from exc


def extract_from_pdf_s3(
    s3_bucket: str,
    s3_key: str,
    max_wait: int = 270,
) -> str:
    """
    Extract text from a PDF stored in S3 using Textract async text detection.

    Uses StartDocumentTextDetection + GetDocumentTextDetection (cheaper than
    StartDocumentAnalysis because FORMS/TABLES are not requested; we only
    consume LINE blocks downstream).

    Args:
        s3_bucket: S3 bucket name containing the PDF.
        s3_key:    S3 object key of the PDF.
        max_wait:  Maximum seconds to poll before raising RuntimeError.
                   Default 270 s — safe under a 300 s Lambda timeout.

    Returns:
        Extracted text as a newline-joined string.

    Raises:
        RuntimeError: If the job fails, times out, or an API error occurs.
    """
    start = time.time()
    client = get_client()

    try:
        logger.info(f"[OCR_CORE] StartDocumentTextDetection s3://{s3_bucket}/{s3_key}")
        start_response = client.start_document_text_detection(
            DocumentLocation={
                "S3Object": {
                    "Bucket": s3_bucket,
                    "Name": s3_key,
                }
            }
        )
        job_id = start_response["JobId"]
        logger.info(f"[OCR_CORE] TextDetection job started: {job_id}")

        # Poll until SUCCEEDED or timeout
        status = "IN_PROGRESS"
        waited = 0
        result = None
        while status == "IN_PROGRESS" and waited < max_wait:
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            result = client.get_document_text_detection(JobId=job_id)
            status = result["JobStatus"]
            logger.debug(
                f"[OCR_CORE] job={job_id} status={status} waited={waited}s"
            )

        if status != "SUCCEEDED":
            msg = (result or {}).get("StatusMessage", "unknown reason")
            raise RuntimeError(
                f"TextDetection job {job_id} ended with status={status}: {msg}"
            )

        # Collect all LINE blocks across paginated results
        all_lines: list[str] = []
        pages = (result or {}).get("DocumentMetadata", {}).get("Pages", 0)
        next_token = None

        while True:
            if next_token:
                page = client.get_document_text_detection(
                    JobId=job_id, NextToken=next_token
                )
            else:
                page = result  # first page already fetched above

            all_lines.extend(collect_line_blocks(page))
            next_token = page.get("NextToken")
            if not next_token:
                break

        text = "\n".join(all_lines)
        elapsed = time.time() - start
        logger.info(
            f"[OCR_CORE] TextDetection done in {elapsed:.2f}s "
            f"— pages={pages}, {len(text)} chars, {len(all_lines)} lines"
        )
        return text

    except RuntimeError:
        raise
    except Exception as exc:
        elapsed = time.time() - start
        logger.error(
            f"[OCR_CORE] TextDetection failed after {elapsed:.2f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        raise RuntimeError(f"StartDocumentTextDetection failed: {exc}") from exc
