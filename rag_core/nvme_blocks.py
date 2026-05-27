"""NVMe-Block-Storage: Maschinenoptimierter Wissenspeicher.

Blockformat (64KB max):
  [4B magic][1B type][1B flags][2B year]
  [8B timestamp][4B uncompressed_len][4B compressed_len]
  [32B key_hash]
  [compressed payload]

Typen:
  0x01 ARTICLE    – Rekonstruierter Volltextartikel
  0x02 FACT       – Einzelfakt (Subjekt, Prädikat, Objekt, Zeitraum)
  0x03 EVENT      – Zeitereignis (Jahr, Monat, Beschreibung, Kategorie)
  0x04 INDEX_NODE – Interner Index-Knoten
  0x05 META       – Metadaten (Statistiken, Schema-Version)
"""
from __future__ import annotations

import hashlib
import logging
import mmap
import os
import struct
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

log = logging.getLogger("nvme_blocks")
if not log.handlers:
    log.setLevel(logging.DEBUG)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    log.addHandler(_h)

try:
    import zstandard as zstd
    _ZSTD = zstd.ZstdCompressor(level=3)
    _ZSTD_D = zstd.ZstdDecompressor()
    log.debug("zstd kompression verfügbar (level=3)")
except ImportError:
    _ZSTD = None
    _ZSTD_D = None
    log.warning("zstandard nicht installiert – Blöcke werden UNKOMPRIMIERT gespeichert. "
                "Installation: pip install zstandard")

BLOCK_MAGIC = b"\x4e\x56\x4d\x4b"  # "NVMK"
HEADER_SIZE = 56  # 4+1+1+2+8+4+4+32
MAX_BLOCK_PAYLOAD = 65536 - HEADER_SIZE
BLOCK_ALIGN = 65536  # 64KB alignment for NVMe optimal access

TYPE_ARTICLE = 0x01
TYPE_FACT = 0x02
TYPE_EVENT = 0x03
TYPE_INDEX_NODE = 0x04
TYPE_META = 0x05

FLAG_COMPRESSED = 0x01
FLAG_DELETED = 0x80


@dataclass
class BlockHeader:
    block_type: int
    flags: int
    timestamp: float
    uncompressed_len: int
    compressed_len: int
    key_hash: bytes
    offset: int = 0
    year: int = 0


@dataclass
class BlockIndex:
    """In-memory index: maps key_hash → file offset. Constant structure, data on NVMe."""
    by_hash: dict[bytes, list[int]] = field(default_factory=lambda: defaultdict(list))
    by_type: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    by_year: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    total_blocks: int = 0
    total_bytes: int = 0


def _key_hash(key: str) -> bytes:
    return hashlib.blake2b(key.encode("utf-8"), digest_size=32).digest()


class NVMeBlockStore:
    """Append-only block store on NVMe with mmap read access."""

    def __init__(self, path: str | Path, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        self.index = BlockIndex()
        self._lock = threading.Lock()
        self._write_fd = None
        self._mmap = None
        self._mmap_fd = None
        self._mmap_size = 0

        log.info(f"NVMeBlockStore init: path={self.path}, readonly={readonly}")

        if not self.path.exists():
            if readonly:
                log.warning(f"Store-Datei existiert nicht und readonly=True: {self.path}")
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()
            log.info(f"Neue Store-Datei erstellt: {self.path}")

        try:
            self._rebuild_index()
        except Exception as e:
            log.error(f"Index-Rebuild fehlgeschlagen für {self.path}: {e}\n{traceback.format_exc()}")

    def _rebuild_index(self):
        """Scan file and rebuild in-memory index."""
        size = self.path.stat().st_size
        if size < HEADER_SIZE:
            log.debug(f"Store-Datei zu klein für Index ({size} bytes), übersprungen")
            return

        log.info(f"Index-Rebuild: Scanne {size / (1024*1024):.1f} MB …")
        t0 = time.time()
        self.index = BlockIndex()
        corrupt_blocks = 0
        deleted_blocks = 0

        with open(self.path, "rb") as f:
            offset = 0
            while offset + HEADER_SIZE <= size:
                f.seek(offset)
                raw = f.read(HEADER_SIZE)
                if len(raw) < HEADER_SIZE:
                    log.warning(f"Unvollständiger Header bei offset={offset}, "
                                f"gelesen={len(raw)}, erwartet={HEADER_SIZE}")
                    break

                magic = raw[0:4]
                if magic != BLOCK_MAGIC:
                    corrupt_blocks += 1
                    offset += BLOCK_ALIGN
                    continue

                try:
                    hdr = self._parse_header(raw, offset)
                except Exception as e:
                    log.error(f"Header-Parse fehlgeschlagen bei offset={offset}: {e}")
                    offset += BLOCK_ALIGN
                    continue

                if hdr.flags & FLAG_DELETED:
                    deleted_blocks += 1
                    offset += BLOCK_ALIGN
                    continue

                self.index.by_hash[hdr.key_hash].append(offset)
                self.index.by_type[hdr.block_type].append(offset)
                if hdr.year > 0:
                    self.index.by_year[hdr.year].append(offset)
                self.index.total_blocks += 1

                block_size = HEADER_SIZE + hdr.compressed_len
                aligned = ((block_size + BLOCK_ALIGN - 1) // BLOCK_ALIGN) * BLOCK_ALIGN
                self.index.total_bytes += aligned
                offset += aligned

        elapsed = time.time() - t0
        log.info(f"Index-Rebuild fertig: {self.index.total_blocks:,} Blöcke, "
                 f"{len(self.index.by_year)} Jahre, "
                 f"{corrupt_blocks} korrupt, {deleted_blocks} gelöscht, "
                 f"{elapsed:.2f}s")

    @staticmethod
    def _parse_header(raw: bytes, offset: int = 0) -> BlockHeader:
        block_type = raw[4]
        flags = raw[5]
        year = struct.unpack_from("<H", raw, 6)[0]
        timestamp = struct.unpack_from("<d", raw, 8)[0]
        uncompressed_len = struct.unpack_from("<I", raw, 16)[0]
        compressed_len = struct.unpack_from("<I", raw, 20)[0]
        key_hash = raw[24:56]
        return BlockHeader(
            block_type=block_type,
            flags=flags,
            timestamp=timestamp,
            uncompressed_len=uncompressed_len,
            compressed_len=compressed_len,
            key_hash=key_hash,
            offset=offset,
            year=year,
        )

    def _make_header(self, block_type: int, flags: int, timestamp: float,
                     uncompressed_len: int, compressed_len: int, key_hash: bytes,
                     year: int = 0) -> bytes:
        hdr = bytearray(HEADER_SIZE)
        hdr[0:4] = BLOCK_MAGIC
        hdr[4] = block_type
        hdr[5] = flags
        struct.pack_into("<H", hdr, 6, min(year, 65535))
        struct.pack_into("<d", hdr, 8, timestamp)
        struct.pack_into("<I", hdr, 16, uncompressed_len)
        struct.pack_into("<I", hdr, 20, compressed_len)
        hdr[24:56] = key_hash
        return bytes(hdr)

    def _open_mmap(self):
        try:
            size = self.path.stat().st_size
        except OSError as e:
            log.error(f"Kann Store-Datei nicht lesen: {self.path}: {e}")
            return
        if size == 0:
            return
        if self._mmap and self._mmap_size == size:
            return
        self._close_mmap()
        try:
            self._mmap_fd = open(self.path, "rb")
            self._mmap = mmap.mmap(self._mmap_fd.fileno(), 0, access=mmap.ACCESS_READ)
            self._mmap_size = size
        except (OSError, mmap.error) as e:
            log.error(f"mmap fehlgeschlagen für {self.path} ({size} bytes): {e}\n"
                      f"{traceback.format_exc()}")

    def _close_mmap(self):
        if self._mmap:
            self._mmap.close()
            self._mmap = None
        if self._mmap_fd:
            self._mmap_fd.close()
            self._mmap_fd = None
        self._mmap_size = 0

    def has_key(self, key: str, block_type: int | None = None) -> bool:
        """Check if a key already exists in the store."""
        kh = _key_hash(key)
        offsets = self.index.by_hash.get(kh, [])
        if not offsets:
            return False
        if block_type is None:
            return True
        for off in offsets:
            for t, t_offsets in self.index.by_type.items():
                if t == block_type and off in t_offsets:
                    return True
        return False

    def store(self, key: str, data: bytes, block_type: int = TYPE_ARTICLE,
              year: int | None = None, dedup: bool = True) -> int:
        """Store a block. Returns the offset. Skips if dedup=True and key exists."""
        if self.readonly:
            log.error(f"Schreibversuch auf readonly Store: key={key[:60]}")
            raise IOError("Store is read-only")

        kh = _key_hash(key)

        if dedup and kh in self.index.by_hash:
            return -1

        uncompressed_len = len(data)

        try:
            if _ZSTD:
                compressed = _ZSTD.compress(data)
                flags = FLAG_COMPRESSED
            else:
                compressed = data
                flags = 0
        except Exception as e:
            log.error(f"Kompression fehlgeschlagen für key={key[:60]}, "
                      f"data_len={len(data)}: {e}\n{traceback.format_exc()}")
            compressed = data
            flags = 0

        if len(compressed) > MAX_BLOCK_PAYLOAD:
            log.warning(f"Block zu groß: key={key[:60]}, "
                        f"compressed={len(compressed)} > max={MAX_BLOCK_PAYLOAD}, "
                        f"original={uncompressed_len} – kürze Originaldaten und re-komprimiere")
            ratio = MAX_BLOCK_PAYLOAD / len(compressed)
            truncated_len = int(len(data) * ratio * 0.9)
            data = data[:truncated_len]
            uncompressed_len = len(data)
            try:
                if _ZSTD:
                    compressed = _ZSTD.compress(data)
                    flags = FLAG_COMPRESSED
                else:
                    compressed = data
                    flags = 0
            except Exception as e:
                log.error(f"Re-Kompression fehlgeschlagen: key={key[:60]}: {e}")
                compressed = data
                flags = 0
            if len(compressed) > MAX_BLOCK_PAYLOAD:
                log.error(f"Block nach Re-Kompression immer noch zu groß: "
                          f"key={key[:60]}, compressed={len(compressed)}, "
                          f"wird übersprungen")
                return -1

        hdr = self._make_header(block_type, flags, time.time(),
                                uncompressed_len, len(compressed), kh,
                                year=year or 0)

        block = hdr + compressed
        padded_len = ((len(block) + BLOCK_ALIGN - 1) // BLOCK_ALIGN) * BLOCK_ALIGN
        block = block + b"\x00" * (padded_len - len(block))

        with self._lock:
            try:
                with open(self.path, "ab") as f:
                    offset = f.tell()
                    f.write(block)
            except OSError as e:
                log.error(f"Schreiben fehlgeschlagen: path={self.path}, key={key[:60]}, "
                          f"block_size={len(block)}: {e}\n{traceback.format_exc()}")
                raise

            self.index.by_hash[kh].append(offset)
            self.index.by_type[block_type].append(offset)
            if year is not None:
                self.index.by_year[year].append(offset)
            self.index.total_blocks += 1
            self.index.total_bytes += padded_len

            self._close_mmap()

        return offset

    def read(self, offset: int) -> tuple[BlockHeader, bytes] | None:
        """Read a block at the given offset. Returns (header, decompressed_data)."""
        try:
            self._open_mmap()
            if not self._mmap:
                log.debug(f"read(offset={offset}): mmap nicht verfügbar")
                return None
            if offset + HEADER_SIZE > self._mmap_size:
                log.warning(f"read(offset={offset}): Offset übersteigt Dateigröße "
                            f"({self._mmap_size})")
                return None

            raw_hdr = self._mmap[offset:offset + HEADER_SIZE]
            if raw_hdr[0:4] != BLOCK_MAGIC:
                log.debug(f"read(offset={offset}): Ungültiger Magic "
                          f"(got {raw_hdr[0:4].hex()}, expected {BLOCK_MAGIC.hex()})")
                return None

            hdr = self._parse_header(raw_hdr, offset)
            if hdr.flags & FLAG_DELETED:
                return None

            payload_start = offset + HEADER_SIZE
            payload_end = payload_start + hdr.compressed_len
            if payload_end > self._mmap_size:
                log.warning(f"read(offset={offset}): Payload übersteigt Dateigröße "
                            f"(end={payload_end}, file={self._mmap_size}, "
                            f"compressed_len={hdr.compressed_len})")
                return None

            compressed = self._mmap[payload_start:payload_end]

            if hdr.flags & FLAG_COMPRESSED and _ZSTD_D:
                try:
                    data = _ZSTD_D.decompress(compressed)
                except Exception as e:
                    log.error(f"Dekompress fehlgeschlagen bei offset={offset}, "
                              f"compressed_len={hdr.compressed_len}, "
                              f"uncompressed_len={hdr.uncompressed_len}: {e}")
                    return None
            else:
                data = bytes(compressed)

            return hdr, data
        except Exception as e:
            log.error(f"read(offset={offset}): Unerwarteter Fehler: {e}\n"
                      f"{traceback.format_exc()}")
            return None

    def read_by_key(self, key: str) -> list[tuple[BlockHeader, bytes]]:
        """Read all blocks for a given key."""
        kh = _key_hash(key)
        offsets = self.index.by_hash.get(kh, [])
        results = []
        for off in offsets:
            result = self.read(off)
            if result:
                results.append(result)
        return results

    def read_by_type(self, block_type: int, limit: int = 1000) -> list[tuple[BlockHeader, bytes]]:
        """Read blocks of a given type."""
        offsets = self.index.by_type.get(block_type, [])[:limit]
        results = []
        for off in offsets:
            result = self.read(off)
            if result:
                results.append(result)
        return results

    def read_by_year(self, year: int, limit: int = 500) -> list[tuple[BlockHeader, bytes]]:
        """Read all blocks for a given year."""
        offsets = self.index.by_year.get(year, [])[:limit]
        results = []
        for off in offsets:
            result = self.read(off)
            if result:
                results.append(result)
        return results

    def search_text(self, query: str, block_type: int | None = None,
                    limit: int = 50) -> list[tuple[BlockHeader, bytes, float]]:
        """Brute-force text search across blocks. Returns (header, data, score)."""
        query_lower = query.lower()
        query_terms = query_lower.split()

        if block_type is not None:
            offsets = self.index.by_type.get(block_type, [])
        else:
            offsets = []
            for type_offsets in self.index.by_type.values():
                offsets.extend(type_offsets)

        results = []
        for off in offsets:
            result = self.read(off)
            if not result:
                continue
            hdr, data = result
            try:
                text = data.decode("utf-8", errors="ignore").lower()
            except Exception:
                continue

            score = 0.0
            for term in query_terms:
                count = text.count(term)
                if count > 0:
                    score += count * (len(term) / max(len(text), 1))

            if score > 0:
                results.append((hdr, data, score))

        results.sort(key=lambda x: -x[2])
        return results[:limit]

    def stats(self) -> dict:
        type_names = {
            TYPE_ARTICLE: "articles",
            TYPE_FACT: "facts",
            TYPE_EVENT: "events",
            TYPE_INDEX_NODE: "index_nodes",
            TYPE_META: "meta",
        }
        by_type = {}
        for t, offsets in self.index.by_type.items():
            name = type_names.get(t, f"type_{t}")
            by_type[name] = len(offsets)

        return {
            "total_blocks": self.index.total_blocks,
            "total_bytes": self.index.total_bytes,
            "disk_mb": self.index.total_bytes / (1024 * 1024),
            "by_type": by_type,
            "years_indexed": len(self.index.by_year),
            "unique_keys": len(self.index.by_hash),
        }

    def close(self):
        self._close_mmap()
        if self._write_fd:
            self._write_fd.close()
            self._write_fd = None

    def __del__(self):
        self.close()

    def __len__(self):
        return self.index.total_blocks
