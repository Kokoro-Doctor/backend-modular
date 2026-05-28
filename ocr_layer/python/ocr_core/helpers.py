"""
Shared helpers for parsing Textract response blocks.
"""
from typing import Any


def collect_line_blocks(response: dict[str, Any]) -> list[str]:
    """
    Extract all LINE-type text blocks from a Textract response dict.

    Works for both DetectDocumentText and GetDocumentTextDetection responses
    since both return the same Blocks structure.

    Args:
        response: Raw Textract API response dict

    Returns:
        List of non-empty text strings, one per LINE block.
    """
    lines = []
    for block in response.get("Blocks", []):
        if block.get("BlockType") == "LINE":
            text = block.get("Text", "").strip()
            if text:
                lines.append(text)
    return lines
