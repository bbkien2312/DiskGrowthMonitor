"""Bounded, lossless scan checkpoints. Never expand an archive back to disk."""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

MIB = 1024 * 1024


class ExportLimitError(ValueError):
    """An expected scan-budget failure with an actionable user message."""


def open_export(path, mode='rb', **kwargs):
    path = Path(path)
    return gzip.open(path, mode, **kwargs) if path.suffix == '.gz' else path.open(mode, **kwargs)


def compress_export(source, temporary, proof, budget):
    """Write and verify a caller-owned staging file, leaving the source intact."""
    written = hashlib.sha256()
    count = 0
    with source.open('rb') as stream, temporary.open('xb') as target:
        with gzip.GzipFile(filename='', fileobj=target, mode='wb', compresslevel=1, mtime=0) as packed:
            while chunk := stream.read(MIB):
                count += len(chunk)
                if count > proof['bytes']:
                    raise ValueError('Nguồn export thay đổi trong lúc nén')
                written.update(chunk)
                packed.write(chunk)
                if target.tell() > budget:
                    raise ExportLimitError('Điểm tiếp tục nén vượt hạn mức')
        size = target.tell()
        if size > budget:
            raise ExportLimitError('Điểm tiếp tục nén vượt hạn mức')
        target.flush()
        os.fsync(target.fileno())
    if count != proof['bytes'] or written.hexdigest() != proof['sha256']:
        raise ValueError('Nguồn export không khớp checksum đã lưu')
    verified = hashlib.sha256()
    count = 0
    with gzip.open(temporary, 'rb') as stream:
        while chunk := stream.read(MIB):
            count += len(chunk)
            if count > proof['bytes']:
                raise ValueError('Bản nén có kích thước giải nén không hợp lệ')
            verified.update(chunk)
    if count != proof['bytes'] or verified.hexdigest() != proof['sha256']:
        raise ValueError('Bản nén không khớp dữ liệu nguồn')
    with temporary.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(proof, bytes=size, sha256=digest,
                raw_bytes=proof['bytes'], raw_sha256=proof['sha256'])
