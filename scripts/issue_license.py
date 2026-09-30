"""Cấp / thu hồi mã kích hoạt LezioneLive — chỉ Turin dùng.

    python scripts/issue_license.py cap ban@example.com        # cấp mã mới, in nội dung email để gửi
    python scripts/issue_license.py thu-hoi ban@example.com    # thu hồi mọi mã của email này
    python scripts/issue_license.py ds                         # danh sách mã đã cấp (sổ cục bộ)

Mã = 6 chữ số PIN trộn ngẫu nhiên với chữ "turin" (vd. 4TU82RI7N19), riêng cho từng email.
- ``licenses.json`` (commit lên GitHub) chỉ chứa băm của email và mã → push sau mỗi lần cấp/thu hồi.
- ``licenses.local.json`` (KHÔNG commit, đã có trong .gitignore) là sổ ghi email + mã gốc để tra lại.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.licensing import (  # noqa: E402
    AUTHOR,
    CONTACT_EMAIL,
    REGISTRY_PATH,
    code_hash,
    email_hash,
    load_registry,
    make_code,
    normalize_email,
)

LEDGER_PATH = ROOT / "licenses.local.json"


def _save(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ledger() -> list[dict[str, str]]:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def issue(email: str) -> None:
    email = normalize_email(email)
    if "@" not in email:
        sys.exit(f"Email không hợp lệ: {email}")
    code = make_code()
    salt = secrets.token_bytes(16)
    registry = load_registry()
    registry.setdefault("issuer", AUTHOR)
    registry.setdefault("licenses", []).append(
        {
            "email_hash": email_hash(email),
            "salt": salt.hex(),
            "hash": code_hash(email, code, salt),
            "issued": date.today().isoformat(),
            "revoked": False,
        }
    )
    _save(REGISTRY_PATH, registry)
    ledger = _ledger()
    ledger.append({"email": email, "code": code, "issued": date.today().isoformat()})
    _save(LEDGER_PATH, ledger)

    print(f"Đã cấp mã cho {email}: {code}\n")
    print("Bước tiếp theo: commit + push licenses.json để máy người nhận (git pull) nhận được mã.\n")
    print("----- Nội dung email gửi người nhận -----")
    print("Tiêu đề: Mã kích hoạt LezioneLive\n")
    print("Chào bạn,\n")
    print("Đây là mã kích hoạt LezioneLive dành riêng cho email này:\n")
    print(f"    Email: {email}")
    print(f"    Mã:    {code}\n")
    print("Chạy app (scripts/start), mở giao diện rồi nhập email + mã ở màn hình kích hoạt.")
    print("Nếu vừa được cấp mã, hãy `git pull` trước để cập nhật danh sách bản quyền.")
    print("Mã gắn với email của bạn, vui lòng không chia sẻ.\n")
    print(f"— {AUTHOR} ({CONTACT_EMAIL})")
    print("-----------------------------------------")


def revoke(email: str) -> None:
    target = email_hash(email)
    registry = load_registry()
    hits = [e for e in registry.get("licenses", []) if e["email_hash"] == target and not e.get("revoked")]
    for entry in hits:
        entry["revoked"] = True
    _save(REGISTRY_PATH, registry)
    print(f"Đã thu hồi {len(hits)} mã của {normalize_email(email)}. Commit + push licenses.json để có hiệu lực.")


def list_issued() -> None:
    registry = load_registry()
    revoked = {e["email_hash"] for e in registry.get("licenses", []) if e.get("revoked")}
    for row in _ledger():
        mark = " (đã thu hồi)" if email_hash(row["email"]) in revoked else ""
        print(f"{row['issued']}  {row['email']:<35} {row['code']}{mark}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cap", help="cấp mã mới").add_argument("email")
    sub.add_parser("thu-hoi", help="thu hồi mã").add_argument("email")
    sub.add_parser("ds", help="danh sách mã đã cấp")
    args = parser.parse_args()
    if args.cmd == "cap":
        issue(args.email)
    elif args.cmd == "thu-hoi":
        revoke(args.email)
    else:
        list_issued()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    main()
