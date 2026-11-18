"""按需从 Ollama 官方 zip 中抓取指定文件（避免下载 1.6GB 全包）。

用法：
    python scripts/install_ollama.py            # ollama.exe + 基础库 + Vulkan
    python scripts/install_ollama.py --cuda     # 额外抓取 CUDA 12 运行库
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import urllib.request
import zlib

VERSION = os.environ.get("OLLAMA_ZIP_VERSION", "v0.40.16")
URL = (
    "https://github.com/ollama/ollama/releases/download/"
    f"{VERSION}/ollama-windows-amd64.zip"
)
DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime", "ollama")
UA = {"User-Agent": "Mozilla/5.0"}


def retry(func, attempts: int = 8, delay: float = 3.0):
    last = None
    for _ in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - 网络抖动统一重试
            last = exc
            print(f"\n  网络错误，{delay:.0f}s 后重试：{exc}")
            time.sleep(delay)
    raise last  # type: ignore[misc]


def file_size(url: str) -> int:
    def once() -> int:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers["Content-Length"])

    return retry(once)


def read_range(url: str, start: int, end: int, timeout: int = 120) -> bytes:
    def once() -> bytes:
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) != end - start + 1:
            raise IOError(f"Range 返回长度不符：{len(data)} != {end - start + 1}")
        return data

    return retry(once)


def read_central_directory(url: str, size: int) -> list[dict]:
    tail = read_range(url, size - 65536, size - 1)
    pos = tail.rfind(b"PK\x05\x06")
    if pos < 0:
        raise RuntimeError("zip 中找不到 EOCD")
    _, _, _, _, count, cd_size, cd_off, _ = struct.unpack("<IHHHHIIH", tail[pos : pos + 22])
    cd = read_range(url, cd_off, cd_off + cd_size - 1)

    entries: list[dict] = []
    i = 0
    for _ in range(count):
        f = struct.unpack("<I6H3I5H2I", cd[i : i + 46])
        method, crc, csize, usize = f[4], f[7], f[8], f[9]
        nlen, elen, clen, offset = f[10], f[11], f[12], f[16]
        name = cd[i + 46 : i + 46 + nlen].decode("utf-8", "replace")
        entries.append(
            {
                "name": name,
                "method": method,
                "crc": crc,
                "csize": csize,
                "usize": usize,
                "offset": offset,
            }
        )
        i += 46 + nlen + elen + clen
    return entries


def local_data_offset(url: str, entry: dict) -> int:
    header = read_range(url, entry["offset"], entry["offset"] + 29)
    nlen, elen = struct.unpack("<HH", header[26:30])
    return entry["offset"] + 30 + nlen + elen


def fetch_entry(url: str, entry: dict, dest_dir: str) -> None:
    out_path = os.path.join(dest_dir, entry["name"].replace("/", os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if entry["method"] != 8:
        raise RuntimeError(f"不支持的压缩方式：{entry['name']}")

    if os.path.exists(out_path) and os.path.getsize(out_path) == entry["usize"]:
        print(f"  已存在，跳过：{entry['name']}")
        return

    data_off = local_data_offset(url, entry)
    decomp = zlib.decompressobj(-15)
    remaining = entry["csize"]
    started = time.time()

    with open(out_path, "wb") as fh:
        while remaining > 0:
            start = data_off + entry["csize"] - remaining
            end = data_off + entry["csize"] - 1
            req = urllib.request.Request(
                url, headers={**UA, "Range": f"bytes={start}-{end}"}
            )

            def pump(req=req) -> None:
                nonlocal remaining
                with urllib.request.urlopen(req, timeout=120) as resp:
                    while remaining > 0:
                        chunk = resp.read(min(1 << 20, remaining))
                        if not chunk:
                            raise IOError("连接提前结束")
                        remaining -= len(chunk)
                        fh.write(decomp.decompress(chunk))

            retry(pump)

    with open(out_path, "ab") as fh:
        fh.write(decomp.flush())

    if entry["csize"] > 32 << 20:
        elapsed = max(time.time() - started, 0.001)
        print(
            f"  {entry['name']}: {entry['usize'] / 1048576:6.1f} MB "
            f"({entry['csize'] / elapsed / 1048576:4.2f} MB/s)"
        )

    actual = os.path.getsize(out_path)
    if actual != entry["usize"]:
        raise RuntimeError(f"{entry['name']} 大小不符：{actual} != {entry['usize']}")


def wanted(name: str, cuda: bool) -> bool:
    if name.endswith("/"):
        return False
    if name == "ollama.exe":
        return True
    if name.startswith("lib/ollama/vulkan/"):
        return True
    if cuda and name.startswith("lib/ollama/cuda_v12/"):
        return True
    if name.startswith("lib/ollama/") and "/" not in name[len("lib/ollama/") :]:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda", action="store_true", help="同时抓取 CUDA 12 运行库（约 900MB）")
    args = parser.parse_args()

    print(f"Ollama zip: {URL}")
    size = file_size(URL)
    entries = read_central_directory(URL, size)
    targets = [e for e in entries if wanted(e["name"], args.cuda)]
    total = sum(e["csize"] for e in targets)
    print(f"共 {len(targets)} 个文件，压缩体积 {total / 1048576:.1f} MB → {DEST}")

    os.makedirs(DEST, exist_ok=True)
    for index, entry in enumerate(targets, 1):
        print(f"[{index}/{len(targets)}] {entry['name']}")
        fetch_entry(URL, entry, DEST)

    print("安装完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
