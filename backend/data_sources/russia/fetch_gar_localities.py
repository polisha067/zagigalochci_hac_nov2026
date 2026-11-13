"""Fetch only the GAR settlement tables through HTTP byte ranges."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import re
import zlib
from zipfile import ZipFile

import httpx

from backend.data_sources.russia.build_coverage_settlements import GAR


class RemoteZip(io.RawIOBase):
    def __init__(self, client, url):
        self.client, self.url = client, url
        self.position = 0
        head = client.head(url)
        head.raise_for_status()
        self.length = int(head.headers["content-length"])

    def seek(self, offset, whence=0):
        self.position = (0, self.position, self.length)[whence] + offset
        return self.position

    def tell(self):
        return self.position

    def seekable(self):
        return True

    def chunk(self, start, size):
        end = start + size - 1
        with self.client.stream("GET", self.url, headers={"Range": f"bytes={start}-{end}"}) as response:
            if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.length}":
                raise RuntimeError("Сервер не подтвердил диапазон. Полный архив загружаться не будет.")
            result = response.read()
        if len(result) != size:
            raise RuntimeError("Размер полученного диапазона не совпадает")
        return result

    def read(self, size=-1):
        available = self.length - self.position
        size = available if size < 0 else min(size, available)
        if size <= 0:
            return b""
        data = self.chunk(self.position, size)
        self.position += size
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit-mb", type=int, default=200, help="Maximum compressed size in decimal MB")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        remote = RemoteZip(client, GAR["archive"])
        with ZipFile(remote) as archive:
            files = [item for item in archive.infolist() if re.fullmatch(r"\d\d/AS_ADDR_OBJ_\d[^/]*\.XML", item.filename)]
        sizes = sum(item.compress_size + 1024 for item in files)
        if not files or sizes > args.limit_mb * 1_000_000:
            parser.error(f"Selected tables exceed the limit or are missing ({sizes} bytes)")

        def fetch(item):
            data = remote.chunk(item.header_offset, item.compress_size + 1024)
            if data[:4] != b"PK\x03\x04":
                raise RuntimeError(f"Неверный заголовок {item.filename}")
            offset = 30 + int.from_bytes(data[26:28], "little") + int.from_bytes(data[28:30], "little")
            xml = zlib.decompress(data[offset:offset + item.compress_size], -15)
            if len(xml) != item.file_size or zlib.crc32(xml) != item.CRC:
                raise RuntimeError(f"Контрольная сумма не совпадает: {item.filename}")
            output = args.output / f"gar-{item.filename[:2]}.xml"
            temporary = output.with_suffix(".tmp")
            temporary.write_bytes(xml)
            temporary.replace(output)

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, files))
    print(f"{len(files)} regional tables, {sizes} compressed bytes")


if __name__ == "__main__":
    main()
