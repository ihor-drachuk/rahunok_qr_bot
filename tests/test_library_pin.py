import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_LINE = re.compile(
    r"^nbu_payment_qr @ https://github\.com/ihor-drachuk/nbu_payment_qr/archive/([0-9a-f]{40})\.tar\.gz$", re.M)


def test_requirements_pin_equals_library_submodule_commit():
    pin = PIN_LINE.search((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert pin, "requirements.txt has no pinned nbu_payment_qr archive line"
    staged = subprocess.run(["git", "ls-files", "-s", "libs/nbu_payment_qr"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()
    assert staged[:1] == ["160000"], f"libs/nbu_payment_qr is not a submodule in the index: {staged}"
    assert pin.group(1) == staged[1]
