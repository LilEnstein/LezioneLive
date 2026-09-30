# Chữ ký tác giả — LezioneLive, created by Turin

LezioneLive do **Turin** (brolai1204@gmail.com) viết. Tác quyền được đánh dấu theo 3 lớp:

| Lớp | Ở đâu | Tác dụng |
|---|---|---|
| Chữ ký số trên commit/tag | Git, GitHub (nhãn **Verified**) | Chứng minh bằng mật mã: commit do người giữ khoá riêng của Turin tạo, không giả được |
| Thông báo bản quyền | `LICENSE`, `AUTHORS` | Cơ sở pháp lý: mã công khai để tham khảo, không phải mã nguồn mở |
| "Created by Turin" | Tiêu đề mọi tab, góc dưới mọi trang, header HTTP `X-Created-By`, console, log backend | Ai dùng app cũng thấy tác giả |

## Khoá công khai của Turin

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAu++hWzWBtmuRfPFrzeQX+bRN8s++4pR7i4niMLzYaj Turin <brolai1204@gmail.com>
```

Dấu vân tay: `SHA256:+8F/Y7wHl/w7U4U8aP5N6zvHFLV7COzV4mjr9LICYf4`

Khoá riêng chỉ nằm trên máy của Turin (`~/.ssh/turin_signing_ed25519`), **không bao giờ** có trong repo.

## Kiểm tra chữ ký

```bash
git config gpg.ssh.allowedSignersFile .github/allowed_signers
git log --show-signature -5
```

Commit hợp lệ hiện `Good "git" signature for brolai1204@gmail.com with ED25519 key SHA256:+8F/Y7wHl/w7U4U8aP5N6zvHFLV7COzV4mjr9LICYf4`.
Trên GitHub, commit có nhãn **Verified** bên cạnh.

## Kích hoạt (mã PIN + TURIN)

Mỗi người dùng cần mã riêng theo email do Turin cấp: 6 chữ số trộn ngẫu nhiên với chữ TURIN (vd. `4TU82RI7N19`).
Repo chỉ chứa `licenses.json` với **bản băm** (SHA-256 của email, scrypt của email + mã), không lộ email hay mã.

Dành cho Turin:

```bash
python scripts/issue_license.py cap ban@example.com      # cấp mã, in sẵn nội dung email
python scripts/issue_license.py thu-hoi ban@example.com  # thu hồi
python scripts/issue_license.py ds                       # tra lại mã đã cấp (sổ licenses.local.json, không commit)
```

Sau khi cấp/thu hồi: commit + push `licenses.json`, người nhận `git pull` rồi nhập email + mã trong app.
