"""
Validate app/abdm/fidelius.py against ABDM's reference implementation.

The bulk of these tests run against frozen vectors captured from Fidelius CLI
1.2.0 (vectors_fidelius.json), so no JRE is needed in CI.

This works because the scheme is deterministic — the GCM IV is derived from the
XOR of the two nonces rather than randomly — so identical key material and
plaintext must produce byte-identical ciphertext. That is a much stronger
assertion than "it round-trips".

To re-verify against a live CLI (e.g. after ABDM changes the scheme):

    python -m unittest tests.test_fidelius -v

with FIDELIUS_CLI pointing at the binary:

    FIDELIUS_CLI=../tools/fidelius-cli/examples/fidelius-cli-1.2.0/bin/fidelius-cli \
        python -m unittest tests.test_fidelius -v

Regenerate the vectors with tests/regenerate_vectors.py.
"""
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ABHA_LAMBDA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ABHA_LAMBDA_ROOT))

from app.abdm import fidelius  # noqa: E402

VECTORS_PATH = Path(__file__).parent / "vectors_fidelius.json"
FIDELIUS_CLI = os.environ.get("FIDELIUS_CLI")


def _load_vectors():
    with open(VECTORS_PATH) as handle:
        return json.load(handle)["vectors"]


class TestFideliusVectors(unittest.TestCase):
    """Frozen-vector tests — no Java required."""

    @classmethod
    def setUpClass(cls):
        cls.vectors = _load_vectors()
        assert cls.vectors, "vectors_fidelius.json is empty"

    def test_decrypts_reference_ciphertext(self):
        """Ciphertext produced by the Java CLI must decrypt here."""
        for v in self.vectors:
            with self.subTest(v["description"]):
                self.assertEqual(
                    fidelius.decrypt(
                        v["expectedCiphertext"],
                        requester_nonce_b64=v["requesterNonce"],
                        sender_nonce_b64=v["senderNonce"],
                        requester_private_key_b64=v["requesterPrivateKey"],
                        sender_public_key_b64=v["senderPublicKey"],
                    ),
                    v["plaintext"],
                )

    def test_reproduces_reference_ciphertext_byte_for_byte(self):
        """Our encryption must be byte-identical to the reference."""
        for v in self.vectors:
            with self.subTest(v["description"]):
                self.assertEqual(
                    fidelius.encrypt(
                        v["plaintext"],
                        sender_nonce_b64=v["senderNonce"],
                        requester_nonce_b64=v["requesterNonce"],
                        sender_private_key_b64=v["senderPrivateKey"],
                        requester_public_key_b64=v["requesterPublicKey"],
                    ),
                    v["expectedCiphertext"],
                )

    def test_accepts_x509_public_key_encoding(self):
        """Peers may send the 412-char X.509 SPKI instead of the 88-char point."""
        for v in self.vectors:
            with self.subTest(v["description"]):
                self.assertEqual(
                    fidelius.decrypt(
                        v["expectedCiphertext"],
                        requester_nonce_b64=v["requesterNonce"],
                        sender_nonce_b64=v["senderNonce"],
                        requester_private_key_b64=v["requesterPrivateKey"],
                        sender_public_key_b64=v["senderX509PublicKey"],
                    ),
                    v["plaintext"],
                )

    def test_x509_encoding_matches_reference(self):
        """
        Our SPKI must be byte-identical to the CLI's x509PublicKey.

        This is what goes on the wire as dhPublicKey.keyValue — ABDM's HIU
        parses it with X509EncodedKeySpec and rejects the raw point.
        """
        for v in self.vectors:
            with self.subTest(v["description"]):
                self.assertEqual(
                    fidelius.to_x509_public_key(v["senderPublicKey"]),
                    v["senderX509PublicKey"],
                )
                self.assertEqual(
                    fidelius.to_x509_public_key(v["requesterPublicKey"]),
                    v["requesterX509PublicKey"],
                )
                self.assertEqual(len(v["senderX509PublicKey"]), 412)

    def test_x509_roundtrips_back_to_the_same_point(self):
        for v in self.vectors:
            with self.subTest(v["description"]):
                spki = fidelius.to_x509_public_key(v["senderPublicKey"])
                self.assertEqual(
                    fidelius.load_public_key(spki),
                    fidelius.load_public_key(v["senderPublicKey"]),
                )

    def test_shared_secret_is_symmetric(self):
        for v in self.vectors:
            with self.subTest(v["description"]):
                self.assertEqual(
                    fidelius.compute_shared_secret(v["senderPrivateKey"], v["requesterPublicKey"]),
                    fidelius.compute_shared_secret(v["requesterPrivateKey"], v["senderPublicKey"]),
                )

    def test_tampered_ciphertext_raises(self):
        """GCM must reject modified data rather than returning garbage."""
        from cryptography.exceptions import InvalidTag
        import base64

        v = self.vectors[0]
        raw = bytearray(base64.b64decode(v["expectedCiphertext"]))
        raw[0] ^= 0xFF
        with self.assertRaises(InvalidTag):
            fidelius.decrypt(
                base64.b64encode(bytes(raw)).decode(),
                requester_nonce_b64=v["requesterNonce"],
                sender_nonce_b64=v["senderNonce"],
                requester_private_key_b64=v["requesterPrivateKey"],
                sender_public_key_b64=v["senderPublicKey"],
            )

    def test_invalid_public_key_rejected(self):
        """A point that is not on curve25519 must not silently produce a secret."""
        import base64
        bad = base64.b64encode(b"\x04" + b"\x01" * 64).decode()
        with self.assertRaises(ValueError):
            fidelius.compute_shared_secret(self.vectors[0]["senderPrivateKey"], bad)


class TestGeneratedKeyMaterial(unittest.TestCase):
    """Our own keygen must match the reference encodings."""

    def test_encodings(self):
        import base64

        for _ in range(20):
            km = fidelius.generate_key_material()
            public = base64.b64decode(km["publicKey"])
            self.assertEqual(len(public), 65)
            self.assertEqual(public[0], 0x04)
            self.assertEqual(len(km["publicKey"]), 88)  # Fidelius switches on this
            self.assertEqual(len(base64.b64decode(km["nonce"])), 32)

            # privateKey mimics Java BigInteger.toByteArray(): signed, big-endian,
            # minimal length. That is <= 33 bytes but NOT always 32 — a scalar
            # with fewer than 249 significant bits encodes shorter.
            private_raw = base64.b64decode(km["privateKey"])
            self.assertLessEqual(len(private_raw), 33)
            scalar = int.from_bytes(private_raw, "big", signed=True)
            self.assertTrue(0 < scalar < fidelius._N)
            self.assertEqual(len(private_raw), (scalar.bit_length() // 8) + 1)

    def test_public_key_matches_private_scalar(self):
        """The advertised point must actually be scalar * G."""
        import base64

        for _ in range(5):
            km = fidelius.generate_key_material()
            scalar = int.from_bytes(base64.b64decode(km["privateKey"]), "big", signed=True)
            expected = fidelius._point_mul(scalar, fidelius._G)
            point = base64.b64decode(km["publicKey"])
            self.assertEqual(int.from_bytes(point[1:33], "big"), expected[0])
            self.assertEqual(int.from_bytes(point[33:65], "big"), expected[1])

    def test_roundtrip_between_two_generated_parties(self):
        sender, requester = fidelius.generate_key_material(), fidelius.generate_key_material()
        plaintext = '{"resourceType":"Bundle","id":"roundtrip"}'

        ciphertext = fidelius.encrypt(
            plaintext,
            sender_nonce_b64=sender["nonce"],
            requester_nonce_b64=requester["nonce"],
            sender_private_key_b64=sender["privateKey"],
            requester_public_key_b64=requester["publicKey"],
        )
        self.assertEqual(
            fidelius.decrypt(
                ciphertext,
                requester_nonce_b64=requester["nonce"],
                sender_nonce_b64=sender["nonce"],
                requester_private_key_b64=requester["privateKey"],
                sender_public_key_b64=sender["publicKey"],
            ),
            plaintext,
        )


class TestDataEncryptionAdapter(unittest.TestCase):
    """The ABDM payload adapter over the primitive."""

    def setUp(self):
        from app.abdm import data_encryption
        self.mod = data_encryption

    def test_key_material_shape(self):
        private_key, nonce, key_material = self.mod.generate_key_material()
        self.assertEqual(key_material["cryptoAlg"], "ECDH")
        self.assertEqual(key_material["curve"], "Curve25519")
        self.assertEqual(key_material["nonce"], nonce)
        self.assertTrue(private_key)

        # keyValue must be the 412-char EC SubjectPublicKeyInfo. Both other
        # encodings have been rejected by the live ABDM sandbox:
        #   raw 88-char point -> "failed to construct sequence from byte[]"
        #   X25519 SPKI       -> "algorithm identifier 1.3.101.110 not recognised"
        key_value = key_material["dhPublicKey"]["keyValue"]
        self.assertEqual(len(key_value), 412)
        self.assertFalse(key_value.startswith("MCowBQYDK2Vu"))  # not X25519
        self.assertTrue(key_value.startswith("MIIBMTCB6gYHKoZIzj0CAT"))  # id-ecPublicKey
        # ...and it must still describe our own key pair.
        self.assertEqual(
            fidelius.compute_shared_secret(private_key, key_value),
            fidelius.compute_shared_secret(private_key, key_value),
        )

    def test_hip_push_decrypts_on_the_hiu_side(self):
        """Full 6.3.5 loop: HIU requests, HIP encrypts + pushes, HIU decrypts."""
        hiu_private, hiu_nonce, hiu_key_material = self.mod.generate_key_material()

        bundles = [
            ("cc-ref-1", '{"resourceType":"Bundle","id":"one"}'),
            ("cc-ref-2", '{"resourceType":"Bundle","id":"two"}'),
        ]
        entries, sender_key_material = self.mod.encrypt_care_context_bundles(
            bundles, hiu_key_material,
        )
        self.assertEqual(len(entries), 2)

        decrypted = self.mod.decrypt_entries(
            entries=entries,
            hip_key_material=sender_key_material,
            receiver_private_key_b64=hiu_private,
            receiver_nonce_b64=hiu_nonce,
        )
        self.assertEqual([d["careContextReference"] for d in decrypted], ["cc-ref-1", "cc-ref-2"])
        self.assertEqual([d["fhir"] for d in decrypted], [b for _, b in bundles])
        self.assertTrue(all(d["checksum_ok"] for d in decrypted))

    def test_malformed_key_material_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.encrypt_care_context_bundles([("ref", "{}")], {})
        with self.assertRaises(ValueError):
            self.mod.encrypt_care_context_bundles(
                [("ref", "{}")], {"dhPublicKey": {"keyValue": ""}, "nonce": ""},
            )


@unittest.skipUnless(FIDELIUS_CLI, "set FIDELIUS_CLI to cross-check against the Java CLI")
class TestAgainstLiveCli(unittest.TestCase):
    """Opt-in cross-check against the real Fidelius CLI. Requires a JRE."""

    def _cli(self, args):
        result = subprocess.run([FIDELIUS_CLI] + args, stdout=subprocess.PIPE, encoding="UTF-8")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"fidelius-cli did not return JSON:\n{result.stdout}")

    def _cli_file(self, *params):
        path = f"/tmp/fidelius_test_{uuid.uuid4()}.txt"
        with open(path, "w") as handle:
            handle.write("\n".join(params))
        try:
            return self._cli(["-f", path])
        finally:
            os.remove(path)

    def test_bidirectional_interop(self):
        plaintext = '{"resourceType":"Bundle","id":"live"}'
        for _ in range(3):
            sender, requester = self._cli(["gkm"]), self._cli(["gkm"])

            java_ct = self._cli_file(
                "e", plaintext, sender["nonce"], requester["nonce"],
                sender["privateKey"], requester["publicKey"],
            )["encryptedData"]
            python_ct = fidelius.encrypt(
                plaintext,
                sender_nonce_b64=sender["nonce"], requester_nonce_b64=requester["nonce"],
                sender_private_key_b64=sender["privateKey"],
                requester_public_key_b64=requester["publicKey"],
            )
            self.assertEqual(python_ct, java_ct, "ciphertext must be byte-identical")

            self.assertEqual(
                self._cli_file(
                    "d", python_ct, requester["nonce"], sender["nonce"],
                    requester["privateKey"], sender["publicKey"],
                )["decryptedData"],
                plaintext,
            )

    def test_java_accepts_our_x509_key_value(self):
        """
        The exact path ABDM's HIU takes: feed our dhPublicKey.keyValue to Java's
        X509EncodedKeySpec. Fidelius routes any non-88-char key there, so this
        reproduces the parse that produced
        "failed to construct sequence from byte[] Extra data detected in stream".
        """
        plaintext = '{"resourceType":"Bundle","id":"x509"}'
        sender = self._cli(["gkm"])
        _, _, key_material = __import__(
            "app.abdm.data_encryption", fromlist=["x"]
        ).generate_key_material()
        key_value = key_material["dhPublicKey"]["keyValue"]
        self.assertEqual(len(key_value), 412)

        result = self._cli_file(
            "e", plaintext, sender["nonce"], key_material["nonce"],
            sender["privateKey"], key_value,
        )
        self.assertTrue(
            result.get("encryptedData"),
            "Java returned empty ciphertext — it could not parse our keyValue",
        )

    def test_java_accepts_our_generated_key_material(self):
        plaintext = '{"resourceType":"Bundle","id":"ours"}'
        sender = self._cli(["gkm"])
        ours = fidelius.generate_key_material()

        java_ct = self._cli_file(
            "e", plaintext, sender["nonce"], ours["nonce"],
            sender["privateKey"], ours["publicKey"],
        )["encryptedData"]
        self.assertEqual(
            fidelius.decrypt(
                java_ct,
                requester_nonce_b64=ours["nonce"], sender_nonce_b64=sender["nonce"],
                requester_private_key_b64=ours["privateKey"],
                sender_public_key_b64=sender["publicKey"],
            ),
            plaintext,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
