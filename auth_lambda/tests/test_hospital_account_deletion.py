import os
import sys
import unittest
from pathlib import Path


AUTH_LAMBDA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTH_LAMBDA_ROOT))

for key, value in {
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_EC2_METADATA_DISABLED": "true",
    "USERS_TABLE": "Users",
    "DOCTORS_TABLE": "Doctors",
    "AUTH_TABLE": "AuthTable",
    "AUTH_TOKENS_TABLE": "AuthTokensTable",
    "SESSIONS_TABLE": "SessionsTable",
    "BREVO_SMTP_USER": "test",
    "BREVO_SMTP_KEY": "test",
    "BREVO_SMTP_SERVER": "localhost",
    "BREVO_SMTP_PORT": "587",
    "JWT_SECRET": "test",
}.items():
    os.environ.setdefault(key, value)

from app.services import account_service  # noqa: E402


def _matches_condition(item, condition):
    if condition is None:
        return True
    expression = condition.get_expression()
    operator = expression["operator"]
    values = expression["values"]
    if operator == "=":
        return item.get(values[0].name) == values[1]
    if operator == "AND":
        return all(_matches_condition(item, value) for value in values)
    if operator == "OR":
        return any(_matches_condition(item, value) for value in values)
    raise AssertionError(f"Unsupported fake condition: {operator}")


class FakeBatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def delete_item(self, Key):
        self.table.delete_item(Key=Key)


class FakeTable:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]

    @staticmethod
    def _key_matches(item, key):
        return all(item.get(name) == value for name, value in key.items())

    def get_item(self, Key):
        item = next((row for row in self.items if self._key_matches(row, Key)), None)
        return {"Item": dict(item)} if item else {}

    def query(self, **kwargs):
        condition = kwargs.get("KeyConditionExpression")
        return {
            "Items": [dict(row) for row in self.items if _matches_condition(row, condition)]
        }

    def scan(self, **kwargs):
        condition = kwargs.get("FilterExpression")
        return {
            "Items": [dict(row) for row in self.items if _matches_condition(row, condition)]
        }

    def batch_writer(self):
        return FakeBatchWriter(self)

    def delete_item(self, Key, ReturnValues=None):
        for index, row in enumerate(self.items):
            if self._key_matches(row, Key):
                deleted = self.items.pop(index)
                return {"Attributes": deleted} if ReturnValues else {}
        return {}

    def update_item(
        self,
        Key,
        UpdateExpression,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
        **kwargs,
    ):
        row = next(item for item in self.items if self._key_matches(item, Key))
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        if UpdateExpression == "REMOVE link_tokens":
            row.pop("link_tokens", None)
        elif UpdateExpression.startswith("REMOVE link_tokens."):
            for service_id in names.values():
                row.get("link_tokens", {}).pop(service_id, None)
        elif "REMOVE hospital_id, hospital_name" in UpdateExpression:
            row.pop("hospital_id", None)
            row.pop("hospital_name", None)
        elif "REMOVE hospital_id" in UpdateExpression:
            row.pop("hospital_id", None)
            row["relation_type"] = values[":relation_type"]
            row["linked_by"] = values[":linked_by"]
            row["updated_at"] = values[":updated_at"]
        else:
            raise AssertionError(f"Unsupported fake update: {UpdateExpression}")
        return {"Attributes": dict(row)}


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, Bucket, Prefix):
        matching = [key for key in self.client.keys if key.startswith(Prefix)]
        return [{"Contents": [{"Key": key} for key in matching]}]


class FakeS3:
    def __init__(self, keys):
        self.keys = set(keys)

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return FakePaginator(self)

    def delete_objects(self, Bucket, Delete):
        for entry in Delete["Objects"]:
            self.keys.discard(entry["Key"])
        return {}


class HospitalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.originals = {}

        tables = {
            "hospitals_table": FakeTable([
                {"hospital_id": "HOSP_1", "name": "Target Hospital"},
            ]),
            "user_hospital_table": FakeTable([
                {"hospital_id": "HOSP_1", "user_id": "USER_1"},
            ]),
            "doctor_hospital_table": FakeTable([
                {"hospital_id": "HOSP_1", "doctor_id": "DOC_1"},
            ]),
            "user_doctor_table": FakeTable([
                {
                    "user_id": "USER_1",
                    "doctor_id": "DOC_1",
                    "hospital_id": "HOSP_1",
                    "relation_type": "HOSPITAL_ASSIGNED",
                },
                {
                    "user_id": "USER_2",
                    "doctor_id": "DOC_2",
                    "hospital_id": "HOSP_1",
                    "relation_type": "SUBSCRIPTION",
                    "subscription_id": "SUB_1",
                },
            ]),
            "user_doctor_relations_table": FakeTable([
                {"relation_id": "REL_1", "hospital_id": "HOSP_1"},
            ]),
            "users_table": FakeTable([
                {
                    "user_id": "USER_1",
                    "name": "Shared Patient",
                    "hospital_id": "HOSP_1",
                    "hospital_name": "Target Hospital",
                },
            ]),
            "doctors_table": FakeTable([
                {
                    "doctor_id": "DOC_1",
                    "doctorname": "Shared Doctor",
                    "hospital_id": "HOSP_1",
                    "hospital_name": "Target Hospital",
                },
            ]),
            "medilocker_documents_table": FakeTable([
                {
                    "user_id": "USER_1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "hospital_id": "HOSP_1",
                    "s3_original_key": "docs/original.pdf",
                    "s3_ocr_key": "docs/ocr.txt",
                },
            ]),
            "hospital_files_table": FakeTable([
                {
                    "hospital_id": "HOSP_1",
                    "file_id": "FILE_1",
                    "s3_key": "HospitalData/HOSP_1/legacy.pdf",
                },
            ]),
            "hospital_abdm_table": FakeTable([
                {
                    "hospital_id": "HOSP_1",
                    "hip_id": "HIP_TARGET",
                    "hiu_id": "HIU_TARGET",
                },
            ]),
            "abdm_transactions_table": FakeTable([
                {
                    "request_id": "TX_TARGET",
                    "hospital_id": "HOSP_1",
                    "hip_id": "HIP_TARGET",
                },
                {
                    "request_id": "TX_OTHER",
                    "hospital_id": "HOSP_2",
                    "hip_id": "HIP_OTHER",
                },
            ]),
            "hiu_consent_requests_table": FakeTable([
                {
                    "request_id": "CR_TARGET",
                    "hospital_id": "HOSP_1",
                    "hiu_id": "HIU_TARGET",
                    "consent_ids": ["CONSENT_TARGET"],
                },
                {
                    "request_id": "CR_OTHER",
                    "hospital_id": "HOSP_2",
                    "hiu_id": "HIU_OTHER",
                },
            ]),
            "hiu_data_requests_table": FakeTable([
                {
                    "request_id": "DR_TARGET",
                    "hospital_id": "HOSP_1",
                    "hiu_id": "HIU_TARGET",
                    "consent_id": "CONSENT_TARGET",
                },
                {
                    "request_id": "DR_OTHER",
                    "hospital_id": "HOSP_2",
                    "hiu_id": "HIU_OTHER",
                },
            ]),
            "consent_artefacts_table": FakeTable([
                {
                    "consent_id": "CONSENT_TARGET",
                    "hiu_id": "HIU_TARGET",
                },
                {
                    "consent_id": "CONSENT_OTHER",
                    "hiu_id": "HIU_OTHER",
                },
            ]),
            "abha_accounts_table": FakeTable([
                {
                    "abha_number": "ABHA_1",
                    "link_tokens": {
                        "HIP_TARGET": {"token": "delete-me"},
                        "HIP_OTHER": {"token": "keep-me"},
                    },
                },
            ]),
        }

        for name, table in tables.items():
            self.originals[name] = getattr(account_service.config, name)
            setattr(account_service.config, name, table)
        self.tables = tables

        self.originals["s3_client"] = account_service.config.s3_client
        self.fake_s3 = FakeS3({
            "docs/original.pdf",
            "docs/ocr.txt",
            "HospitalData/HOSP_1/legacy.pdf",
            "HospitalData/HOSP_1/orphan.pdf",
            "hospital_uploads/HOSP_1/presigned.pdf",
            "HospitalData/HOSP_2/keep.pdf",
        })
        account_service.config.s3_client = self.fake_s3

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(account_service.config, name, value)

    def test_full_hospital_cascade_preserves_shared_accounts_and_subscription(self):
        deleted = {}
        errors = []

        account_service._delete_hospital_scoped("HOSP_1", deleted, errors)

        self.assertEqual(errors, [])
        self.assertEqual(self.tables["hospitals_table"].items, [])
        self.assertEqual(self.tables["user_hospital_table"].items, [])
        self.assertEqual(self.tables["doctor_hospital_table"].items, [])
        self.assertEqual(self.tables["user_doctor_relations_table"].items, [])

        relations = self.tables["user_doctor_table"].items
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["subscription_id"], "SUB_1")
        self.assertEqual(relations[0]["relation_type"], "USER_SUBSCRIPTION")
        self.assertNotIn("hospital_id", relations[0])

        patient = self.tables["users_table"].items[0]
        doctor = self.tables["doctors_table"].items[0]
        self.assertEqual(patient["name"], "Shared Patient")
        self.assertEqual(doctor["doctorname"], "Shared Doctor")
        self.assertNotIn("hospital_id", patient)
        self.assertNotIn("hospital_id", doctor)

        self.assertEqual(self.tables["medilocker_documents_table"].items, [])
        self.assertEqual(self.tables["hospital_files_table"].items, [])
        self.assertEqual(self.tables["hospital_abdm_table"].items, [])
        self.assertEqual(
            [row["request_id"] for row in self.tables["abdm_transactions_table"].items],
            ["TX_OTHER"],
        )
        self.assertEqual(
            [row["consent_id"] for row in self.tables["consent_artefacts_table"].items],
            ["CONSENT_OTHER"],
        )
        self.assertEqual(
            set(self.tables["abha_accounts_table"].items[0]["link_tokens"]),
            {"HIP_OTHER"},
        )

        self.assertEqual(self.fake_s3.keys, {"HospitalData/HOSP_2/keep.pdf"})
        self.assertEqual(deleted["hospitals"], 1)

    def test_profile_is_retained_when_a_cleanup_step_fails(self):
        original_query_all = account_service._query_all

        def fail_membership_query(table, **kwargs):
            if table is self.tables["user_hospital_table"]:
                raise RuntimeError("temporary DynamoDB failure")
            return original_query_all(table, **kwargs)

        account_service._query_all = fail_membership_query
        try:
            deleted = {}
            errors = []
            with self.assertLogs("app.services.account_service", level="ERROR"):
                account_service._delete_hospital_scoped("HOSP_1", deleted, errors)
        finally:
            account_service._query_all = original_query_all

        self.assertTrue(errors)
        self.assertEqual(deleted["hospitals"], 0)
        self.assertEqual(
            self.tables["hospitals_table"].items[0]["hospital_id"],
            "HOSP_1",
        )


if __name__ == "__main__":
    unittest.main()
