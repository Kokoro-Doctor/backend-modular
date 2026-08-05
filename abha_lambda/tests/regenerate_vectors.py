"""
Regenerate tests/vectors_fidelius.json from the Fidelius CLI. Requires a JRE.

    python tests/regenerate_vectors.py

Only needed if ABDM changes the scheme or you want more coverage. The committed
vectors let the test suite run without Java.
"""
import json
import os
import subprocess
import uuid
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DEFAULT_CLI = (
    TESTS_DIR.parents[1] / "tools" / "fidelius-cli"
    / "examples" / "fidelius-cli-1.2.0" / "bin" / "fidelius-cli"
)
CLI = Path(os.environ.get("FIDELIUS_CLI", DEFAULT_CLI))

PLAINTEXTS = [
    '{"resourceType":"Bundle","id":"cc-1","entry":[]}',
    '{"data": "There is no war in Ba Sing Se!"}',
    '{"resourceType":"Bundle","note":"unicode: àéîõü नमस्ते"}',
]


def cli(args):
    result = subprocess.run([str(CLI)] + args, stdout=subprocess.PIPE, encoding="UTF-8")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"fidelius-cli did not return JSON:\n{result.stdout}")


def cli_file(*params):
    path = f"/tmp/fidelius_gen_{uuid.uuid4()}.txt"
    with open(path, "w") as handle:
        handle.write("\n".join(params))
    try:
        return cli(["-f", path])
    finally:
        os.remove(path)


def main():
    if not CLI.exists():
        raise SystemExit(f"fidelius-cli not found at {CLI}; set FIDELIUS_CLI")

    vectors = []
    for index, plaintext in enumerate(PLAINTEXTS):
        sender, requester = cli(["gkm"]), cli(["gkm"])
        ciphertext = cli_file(
            "e", plaintext, sender["nonce"], requester["nonce"],
            sender["privateKey"], requester["publicKey"],
        )["encryptedData"]
        vectors.append({
            "description": f"fidelius-cli-1.2.0 vector {index}",
            "plaintext": plaintext,
            "senderPrivateKey": sender["privateKey"],
            "senderPublicKey": sender["publicKey"],
            "senderX509PublicKey": sender["x509PublicKey"],
            "senderNonce": sender["nonce"],
            "requesterPrivateKey": requester["privateKey"],
            "requesterPublicKey": requester["publicKey"],
            "requesterX509PublicKey": requester["x509PublicKey"],
            "requesterNonce": requester["nonce"],
            "expectedCiphertext": ciphertext,
        })

    out = TESTS_DIR / "vectors_fidelius.json"
    with open(out, "w") as handle:
        json.dump(
            {"source": "fidelius-cli 1.2.0 (backend/tools/fidelius-cli)", "vectors": vectors},
            handle, indent=2,
        )
    print(f"wrote {len(vectors)} vectors -> {out}")


if __name__ == "__main__":
    main()
