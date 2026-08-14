"""A small GRIB1 reader for IMGW's COSMO_HVD product.

Full GRIB1 is a large, old specification. IMGW's COSMO files are one narrow
slice of it -- record-sequential messages, grid representation type 10
(rotated latitude/longitude), simple grid-point packing, no bitmap section --
and that slice was confirmed against a real downloaded file (see cosmo.py's
module docstring for how), not assumed from the spec text alone. If a message
uses anything else this reader raises UnsupportedGrib rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np


class UnsupportedGrib(ValueError):
    """The message uses a template this reader deliberately does not implement."""


@dataclass
class Grib1Message:
    param: int
    level_type: int
    level: int
    values: np.ndarray  # 2-D (nj, ni): row 0 is the southernmost row, row-major west->east
    ni: int
    nj: int
    la1: float  # deg, rotated latitude of the first (southernmost) row
    lo1: float  # deg, rotated longitude of the first (westernmost) column
    dlat: float  # deg, always positive
    dlon: float  # deg, always positive
    pole_lat: float  # deg, geographic latitude of the grid's south pole
    pole_lon: float  # deg, geographic longitude of the grid's south pole


def _ibm_float(raw: bytes) -> float:
    """GRIB1's BDS reference value is IBM System/360 hex float, not IEEE 754."""
    value = int.from_bytes(raw, "big")
    sign = -1.0 if (value >> 31) & 1 else 1.0
    exponent = (value >> 24) & 0x7F
    mantissa = value & 0x00FFFFFF
    return sign * (mantissa / (1 << 24)) * (16.0 ** (exponent - 64))


def _signed(raw: int, bits: int) -> int:
    """GRIB1 stores signed integers as sign-and-magnitude, not two's complement."""
    sign_bit = 1 << (bits - 1)
    return -(raw & (sign_bit - 1)) if raw & sign_bit else raw


def _unpack_bits(payload: bytes, count: int, bits: int) -> np.ndarray:
    if bits == 0:
        return np.zeros(count, dtype=np.int64)
    raw = np.frombuffer(payload, dtype=np.uint8)
    unpacked = np.unpackbits(raw)[: count * bits]
    if unpacked.size < count * bits:
        raise UnsupportedGrib("Data section is shorter than the grid it describes")
    grid = unpacked.reshape(count, bits).astype(np.int64)
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.int64)
    return grid @ weights


def message_bounds(data: bytes) -> Iterator[tuple]:
    """Yield ``(start, total_len)`` for every message, without decoding it.

    A COSMO_HVD file is ~150 MB and mostly 3D fields nothing here wants; a
    caller that only needs a handful of surface fields can use this to walk
    the file and call :func:`peek` / :func:`decode_message` selectively,
    rather than fully unpacking every message's bit-packed values.
    """
    position = 0
    while position < len(data) - 4:
        if data[position : position + 4] != b"GRIB":
            break
        total_len = int.from_bytes(data[position + 4 : position + 7], "big")
        edition = data[position + 7]
        if edition != 1:
            raise UnsupportedGrib(f"GRIB edition {edition} is not supported")
        if total_len <= 0 or position + total_len > len(data):
            raise UnsupportedGrib("Malformed message length")
        yield position, total_len
        position += total_len


def peek(data: bytes, start: int) -> tuple:
    """``(param, level_type, level)`` for the message at ``start``, PDS only.

    Cheap on purpose -- reads a dozen bytes rather than unpacking the grid, so
    a caller can decide whether a message is wanted before paying for
    :func:`decode_message`.
    """
    pds_base = start + 8
    param = data[pds_base + 8]
    level_type = data[pds_base + 9]
    level = int.from_bytes(data[pds_base + 10 : pds_base + 12], "big")
    return param, level_type, level


def iter_messages(data: bytes) -> Iterator[Grib1Message]:
    """Decode every message in a record-sequential GRIB1 file, in file order."""
    for start, _ in message_bounds(data):
        yield decode_message(data, start)


def decode_message(data: bytes, start: int) -> Grib1Message:
    pds_base = start + 8
    pds_len = int.from_bytes(data[pds_base : pds_base + 3], "big")
    flag = data[pds_base + 7]
    param = data[pds_base + 8]
    level_type = data[pds_base + 9]
    level = int.from_bytes(data[pds_base + 10 : pds_base + 12], "big")
    dec_scale = _signed(int.from_bytes(data[pds_base + 26 : pds_base + 28], "big"), 16)

    if not (flag & 0x80):
        raise UnsupportedGrib("Message has no grid definition section")
    if flag & 0x40:
        raise UnsupportedGrib("Bitmapped messages are not supported")

    gds_base = pds_base + pds_len
    gds_len = int.from_bytes(data[gds_base : gds_base + 3], "big")
    gds = data[gds_base : gds_base + gds_len]
    grid_type = gds[5]
    if grid_type != 10:
        raise UnsupportedGrib(f"Grid representation type {grid_type} is not supported")

    ni = int.from_bytes(gds[6:8], "big")
    nj = int.from_bytes(gds[8:10], "big")
    la1 = _signed(int.from_bytes(gds[10:13], "big"), 24) / 1000.0
    lo1 = _signed(int.from_bytes(gds[13:16], "big"), 24) / 1000.0
    la2 = _signed(int.from_bytes(gds[17:20], "big"), 24) / 1000.0
    lo2 = _signed(int.from_bytes(gds[20:23], "big"), 24) / 1000.0
    scan = gds[27]
    pole_lat = _signed(int.from_bytes(gds[32:35], "big"), 24) / 1000.0
    pole_lon = _signed(int.from_bytes(gds[35:38], "big"), 24) / 1000.0

    if ni < 2 or nj < 2:
        raise UnsupportedGrib("Degenerate grid")
    # Scanning mode 0x40 (+i west->east, +j south->north, row-major) is the
    # only one seen on a real file; a bare reshape below only makes sense for
    # that layout, so anything else has to fail loudly rather than silently
    # transpose or mirror the field.
    if scan != 0x40:
        raise UnsupportedGrib(f"Scanning mode {scan:#04x} is not supported")
    dlon = (lo2 - lo1) / (ni - 1)
    dlat = (la2 - la1) / (nj - 1)

    bds_base = gds_base + gds_len
    bds_len = int.from_bytes(data[bds_base : bds_base + 3], "big")
    bds = data[bds_base : bds_base + bds_len]
    bds_flag = bds[3]
    if bds_flag & 0xC0:
        raise UnsupportedGrib("Only simple grid-point packing is supported")
    bin_scale = _signed(int.from_bytes(bds[4:6], "big"), 16)
    reference = _ibm_float(bds[6:10])
    bit_count = bds[10]

    packed = _unpack_bits(bds[11:], ni * nj, bit_count)
    values = (reference + packed * (2.0**bin_scale)) / (10.0**dec_scale)
    if values.size != ni * nj:
        raise UnsupportedGrib("Value count does not match the grid size")

    return Grib1Message(
        param=param,
        level_type=level_type,
        level=level,
        values=values.reshape(nj, ni),
        ni=ni,
        nj=nj,
        la1=la1,
        lo1=lo1,
        dlat=dlat,
        dlon=dlon,
        pole_lat=pole_lat,
        pole_lon=pole_lon,
    )
