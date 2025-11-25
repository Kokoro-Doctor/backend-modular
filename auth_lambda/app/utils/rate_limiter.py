from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, Literal, Optional

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException

from app import config
from app.logger import get_logger
from app.utils.db_utils import generate_token_id, normalize_phone_number

logger = get_logger(__name__)


class RateLimitAction(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET_EMAIL = "password_reset_email"
    MOBILE_OTP = "mobile_otp"
    PASSWORD_RESET_PHONE = "password_reset_phone"


@dataclass(frozen=True)
class RateLimitRule:
    identifier_attr: Literal["email", "phoneNumber"]
    window_seconds: int
    max_attempts: int
    purpose: str
    error_detail: str


def _build_rules() -> Dict[RateLimitAction, RateLimitRule]:
    return {
        RateLimitAction.EMAIL_VERIFICATION: RateLimitRule(
            identifier_attr="email",
            window_seconds=config.EMAIL_VERIFICATION_RATE_LIMIT_WINDOW_SECONDS,
            max_attempts=config.EMAIL_VERIFICATION_RATE_LIMIT_MAX_ATTEMPTS,
            purpose="rate_limit#email_verification",
            error_detail="Too many verification emails requested. Please try again later.",
        ),
        RateLimitAction.PASSWORD_RESET_EMAIL: RateLimitRule(
            identifier_attr="email",
            window_seconds=config.PASSWORD_RESET_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
            max_attempts=config.PASSWORD_RESET_EMAIL_RATE_LIMIT_MAX_ATTEMPTS,
            purpose="rate_limit#password_reset_email",
            error_detail="Too many password reset emails requested. Please try again later.",
        ),
        RateLimitAction.MOBILE_OTP: RateLimitRule(
            identifier_attr="phoneNumber",
            window_seconds=config.MOBILE_OTP_RATE_LIMIT_WINDOW_SECONDS,
            max_attempts=config.MOBILE_OTP_RATE_LIMIT_MAX_ATTEMPTS,
            purpose="rate_limit#mobile_otp",
            error_detail="Too many OTP requests. Please wait before trying again.",
        ),
        RateLimitAction.PASSWORD_RESET_PHONE: RateLimitRule(
            identifier_attr="phoneNumber",
            window_seconds=config.PASSWORD_RESET_SMS_RATE_LIMIT_WINDOW_SECONDS,
            max_attempts=config.PASSWORD_RESET_SMS_RATE_LIMIT_MAX_ATTEMPTS,
            purpose="rate_limit#password_reset_phone",
            error_detail="Too many password reset OTP requests. Please wait before trying again.",
        ),
    }


RATE_LIMIT_RULES = _build_rules()


def _get_rule(action: RateLimitAction) -> RateLimitRule:
    try:
        return RATE_LIMIT_RULES[action]
    except KeyError as exc:
        raise ValueError(f"No rate limit rule configured for action {action}") from exc


def _normalize_identifier(rule: RateLimitRule, identifier: str) -> str:
    if not identifier:
        return ""

    if rule.identifier_attr == "email":
        return identifier.strip().lower()

    return normalize_phone_number(identifier)


def _to_int(value: Optional[int | str | Decimal]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _should_enforce(rule: RateLimitRule) -> bool:
    return rule.max_attempts > 0 and rule.window_seconds > 0


def _count_active_attempts(rule: RateLimitRule, identifier_value: str) -> int:
    index_name = "email-index" if rule.identifier_attr == "email" else "phone-index"
    key_condition = Key(rule.identifier_attr).eq(identifier_value) & Key("purpose").eq(
        rule.purpose
    )

    count = 0
    now_ts = int(datetime.now(timezone.utc).timestamp())
    params = {
        "IndexName": index_name,
        "KeyConditionExpression": key_condition,
    }

    while True:
        response = config.auth_tokens_table.query(**params)
        for item in response.get("Items", []):
            ttl_value = _to_int(item.get("ttl"))
            if ttl_value is None:
                continue
            if ttl_value >= now_ts:
                count += 1

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
        params["ExclusiveStartKey"] = last_evaluated_key

    return count


def _ensure_within_limit(rule: RateLimitRule, identifier_value: str) -> None:
    if not _should_enforce(rule):
        return

    active_attempts = _count_active_attempts(rule, identifier_value)
    if active_attempts >= rule.max_attempts:
        logger.warning(
            "[RateLimiter] Action=%s identifier=%s has %s attempts within %ss window",
            rule.purpose,
            identifier_value,
            active_attempts,
            rule.window_seconds,
        )
        raise HTTPException(status_code=429, detail=rule.error_detail)


def reserve_rate_limit_slot(action: RateLimitAction, identifier: str) -> Optional[str]:
    rule = _get_rule(action)
    normalized_identifier = _normalize_identifier(rule, identifier)

    if not normalized_identifier:
        raise HTTPException(status_code=400, detail="Invalid contact identifier provided.")

    if not _should_enforce(rule):
        return None

    _ensure_within_limit(rule, normalized_identifier)

    reservation_id = generate_token_id()
    ttl_value = int(datetime.now(timezone.utc).timestamp()) + rule.window_seconds

    item = {
        "token_id": reservation_id,
        "purpose": rule.purpose,
        rule.identifier_attr: normalized_identifier,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "ttl": ttl_value,
    }

    config.auth_tokens_table.put_item(Item=item)
    logger.info(
        "[RateLimiter] Reserved slot %s for action=%s identifier=%s",
        reservation_id,
        action.value,
        normalized_identifier,
    )
    return reservation_id


def release_rate_limit_slot(action: RateLimitAction, reservation_id: Optional[str]) -> None:
    if not reservation_id:
        return

    rule = _get_rule(action)
    try:
        config.auth_tokens_table.delete_item(
            Key={"token_id": reservation_id, "purpose": rule.purpose}
        )
        logger.info(
            "[RateLimiter] Released slot %s for action=%s", reservation_id, action.value
        )
    except Exception:
        logger.exception(
            "[RateLimiter] Failed to release slot %s for action=%s",
            reservation_id,
            action.value,
        )


@contextmanager
def rate_limit_guard(action: RateLimitAction, identifier: str):
    reservation_id = reserve_rate_limit_slot(action, identifier)
    try:
        yield
    except Exception:
        release_rate_limit_slot(action, reservation_id)
        raise

