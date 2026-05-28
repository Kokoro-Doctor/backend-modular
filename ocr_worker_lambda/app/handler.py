"""
OCRWorkerLambda entrypoint — triggered by SQS (OCRQueue).

Each SQS record carries a JSON payload produced by ocr_service.enqueue_ocr_async():
  {
    "user_id":    str,
    "file_id":    str,
    "s3_key":     str,   # original file S3 key
    "filename":   str,   # used for image vs PDF routing
    "created_at": str    # DynamoDB sort key
  }

BatchSize is set to 1 in template.yaml so each invocation handles exactly one
document.  On unhandled exception, SQS redelivers (up to maxReceiveCount=3)
then routes to OCRQueueDLQ.
"""
import json

from app.logger import get_logger
from app.services.ocr_worker_service import process_ocr_job

logger = get_logger(__name__)


def handler(event: dict, context) -> dict:
    """
    Lambda handler — processes one SQS record per invocation.

    Returns a dict so SAM local invoke shows clean output.
    Raises only for infrastructure failures (S3/DynamoDB unreachable) so
    SQS redelivery / DLQ kicks in correctly for transient errors.
    """
    records = event.get("Records", [])
    logger.info(f"[HANDLER] Received {len(records)} SQS record(s)")

    for record in records:
        message_id = record.get("messageId", "?")
        try:
            body = json.loads(record["body"])
        except (KeyError, json.JSONDecodeError) as exc:
            logger.error(
                f"[HANDLER] Cannot parse SQS body for messageId={message_id}: {exc}"
            )
            # Malformed message — skip, don't raise (would loop forever in DLQ)
            continue

        required = ("user_id", "file_id", "s3_key", "filename", "created_at")
        missing = [k for k in required if k not in body]
        if missing:
            logger.error(
                f"[HANDLER] Message {message_id} missing fields: {missing} — skipping"
            )
            continue

        logger.info(
            f"[HANDLER] Processing messageId={message_id} "
            f"file_id={body['file_id']} user_id={body['user_id']}"
        )

        process_ocr_job(
            user_id=body["user_id"],
            file_id=body["file_id"],
            s3_key=body["s3_key"],
            filename=body["filename"],
            created_at=body["created_at"],
        )

    return {"statusCode": 200, "processed": len(records)}
