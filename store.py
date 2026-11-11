"""
Penyimpanan permanen di branch git `records` (track record, arsip berita, data BEI dari laptop,
fundamental, status notifikasi). Dipakai oleh workflow GitHub Actions maupun runner laptop.

  python store.py pull  <dir>            # ambil branch records ke <dir> (buat baru bila belum ada)
  python store.py push  <dir> "<pesan>"  # commit & push perubahan (otomatis rebase bila bentrok)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

BRANCH = "records"


def git(*args, cwd=None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True, capture_output=True)


def remote_url() -> str:
    return git("config", "--get", "remote.origin.url").stdout.strip()


def pull(path: Path) -> None:
    path = Path(path)
    url = remote_url()
    exists = git("ls-remote", "--heads", "origin", BRANCH, check=False).stdout.strip() != ""
    if (path / ".git").exists():
        git("fetch", "origin", BRANCH, cwd=path, check=False)
        git("reset", "--hard", f"origin/{BRANCH}", cwd=path, check=False)
        return
    path.mkdir(parents=True, exist_ok=True)
    # salin kredensial checkout (extraheader) agar push dari folder ini juga terotentikasi
    extra = git("config", "--get-regexp", r"http\..*\.extraheader", check=False).stdout.strip().splitlines()
    if exists:
        hdr = [f"-c{k}={v}" for k, v in (ln.split(" ", 1) for ln in extra)]
        git(*hdr, "clone", "--depth", "1", "--branch", BRANCH, url, str(path))
    else:
        git("init", "-b", BRANCH, str(path))
        git("remote", "add", "origin", url, cwd=path)
        (path / "README.md").write_text("Data permanen dashboard (dibuat otomatis). Jangan diedit manual.\n")
    for line in extra:
        k, v = line.split(" ", 1)
        git("config", k, v, cwd=path)
    git("config", "user.name", "dashboard-bot", cwd=path)
    git("config", "user.email", "dashboard-bot@users.noreply.github.com", cwd=path)


def push(path: Path, message: str, tries: int = 4) -> bool:
    path = Path(path)
    git("add", "-A", cwd=path)
    if git("diff", "--cached", "--quiet", cwd=path, check=False).returncode == 0:
        print("store: tidak ada perubahan")
        return True
    git("commit", "-q", "-m", message, cwd=path)
    for i in range(tries):
        r = git("push", "origin", f"HEAD:{BRANCH}", cwd=path, check=False)
        if r.returncode == 0:
            print("store: tersimpan")
            return True
        # bentrok dengan penulis lain (laptop vs cloud): rebase lalu coba lagi
        git("fetch", "origin", BRANCH, cwd=path, check=False)
        rb = git("rebase", f"origin/{BRANCH}", cwd=path, check=False)
        if rb.returncode != 0:
            git("rebase", "--abort", cwd=path, check=False)
            # konflik pada file status: pakai versi lokal untuk file milik run ini
            git("pull", "--no-rebase", "-X", "ours", "origin", BRANCH, cwd=path, check=False)
        time.sleep(2 + i * 3)
    print("store: gagal push:", r.stderr[-300:])
    return False


if __name__ == "__main__":
    cmd, d = sys.argv[1], Path(sys.argv[2])
    if cmd == "pull":
        pull(d)
    elif cmd == "push":
        ok = push(d, sys.argv[3] if len(sys.argv) > 3 else "update data")
        sys.exit(0 if ok else 1)
