"""
ocr_core — shared Textract wrapper.

Pure OCR layer: calls Textract, returns raw text.
No DynamoDB, no LLM, no user-level knowledge.
"""
from ocr_core.ocr_engine import extract_from_image, extract_from_pdf_s3

__all__ = ["extract_from_image", "extract_from_pdf_s3"]
