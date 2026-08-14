"""Tests for the small GRIB1 reader COSMO_HVD needs.

Messages are built by hand so the suite stays offline; each one exercises the
exact section layout confirmed against a real downloaded COSMO_HVD file (see
app/providers/imgw/cosmo.py's module docstring).
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from app.providers.imgw.grib1 import (
    UnsupportedGrib,
    _ibm_float,
    _signed,
    decode_message,
    iter_messages,
    message_bounds,
    peek,
)


def _pds(param: int = 11, level_type: int = 105, level: int = 2, dec_scale: int = 0, flag: int = 0x80) -> bytes:
    section = bytearray(28)
    section[0:3] = struct.pack(">I", len(section))[1:]
    section[7] = flag
    section[8] = param
    section[9] = level_type
    struct.pack_into(">H", section, 10, level)
    struct.pack_into(">h", section, 26, dec_scale)
    return bytes(section)


def _gds(ni: int, nj: int, la1: float, lo1: float, la2: float, lo2: float,
         pole_lat: float, pole_lon: float, grid_type: int = 10, scan: int = 0x40) -> bytes:
    section = bytearray(38)
    section[0:3] = struct.pack(">I", len(section))[1:]
    section[5] = grid_type
    struct.pack_into(">H", section, 6, ni)
    struct.pack_into(">H", section, 8, nj)

    def put_signed24(offset: int, value_millidegrees: int) -> None:
        raw = abs(value_millidegrees)
        if value_millidegrees < 0:
            raw |= 0x800000
        section[offset] = (raw >> 16) & 0xFF
        section[offset + 1] = (raw >> 8) & 0xFF
        section[offset + 2] = raw & 0xFF

    put_signed24(10, round(la1 * 1000))
    put_signed24(13, round(lo1 * 1000))
    put_signed24(17, round(la2 * 1000))
    put_signed24(20, round(lo2 * 1000))
    section[27] = scan
    put_signed24(32, round(pole_lat * 1000))
    put_signed24(35, round(pole_lon * 1000))
    return bytes(section)


def _ibm_bytes(value: float) -> bytes:
    """Encode a float as IBM System/360 hex float -- the inverse of _ibm_float."""
    if value == 0.0:
        return b"\x00\x00\x00\x00"
    sign = 0x80 if value < 0 else 0x00
    value = abs(value)
    exponent = 64
    while value >= 1.0:
        value /= 16.0
        exponent += 1
    while value < 1.0 / 16.0:
        value *= 16.0
        exponent -= 1
    mantissa = round(value * (1 << 24))
    if mantissa >= (1 << 24):  # rounding pushed it back over
        mantissa //= 16
        exponent += 1
    return bytes([sign | exponent]) + mantissa.to_bytes(3, "big")


def _bds(values: list[float], bits: int = 8, reference: float = 0.0, bin_scale: int = 0, flag: int = 0x00) -> bytes:
    count = len(values)
    packed = [round((v - reference) / (2.0**bin_scale)) for v in values]
    stream = "".join(format(p, f"0{bits}b") for p in packed)
    stream += "0" * (-len(stream) % 8)
    payload = bytes(int(stream[i : i + 8], 2) for i in range(0, len(stream), 8))

    section = bytearray(11)
    section[3] = flag

    def put_signed16(offset: int, value: int) -> None:
        raw = abs(value)
        if value < 0:
            raw |= 0x8000
        section[offset] = (raw >> 8) & 0xFF
        section[offset + 1] = raw & 0xFF

    put_signed16(4, bin_scale)
    section[6:10] = _ibm_bytes(reference)
    section[10] = bits
    section = section + bytearray(payload)
    section[0:3] = struct.pack(">I", len(section))[1:]
    return bytes(section)


def _message(pds: bytes, gds: bytes, bds: bytes) -> bytes:
    sections = pds + gds + bds
    total = 8 + len(sections) + 4
    head = b"GRIB" + struct.pack(">I", total)[1:] + bytes([1])
    return head + sections + b"7777"


def _simple(ni=3, nj=2, values=None, **kwargs):
    values = values if values is not None else [float(v) for v in range(ni * nj)]
    return _message(
        _pds(**{k: v for k, v in kwargs.items() if k in ("param", "level_type", "level", "dec_scale", "flag")}),
        _gds(ni, nj, la1=-4.0, lo1=-5.0, la2=0.0, lo2=-1.0, pole_lat=-40.0, pole_lon=10.0),
        _bds(values, **{k: v for k, v in kwargs.items() if k in ("bits", "reference", "bin_scale")}),
    )


# ------------------------------------------------------------------- IBM float


@pytest.mark.parametrize("value", [0.0, 1.0, -1.0, 273.15, -40.0, 0.015625, 1e-5, 12345.678])
def test_ibm_float_round_trips(value):
    assert _ibm_float(_ibm_bytes(value)) == pytest.approx(value, rel=1e-5, abs=1e-6)


# --------------------------------------------------------------- sign-magnitude


@pytest.mark.parametrize(
    "raw, bits, expected",
    [(0, 16, 0), (5, 16, 5), (0x8005, 16, -5), (0x8000, 16, 0)],
)
def test_signed_is_sign_and_magnitude_not_twos_complement(raw, bits, expected):
    assert _signed(raw, bits) == expected


# ------------------------------------------------------------------ decoding


def test_decodes_grid_geometry_and_values():
    message = _simple(values=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    field = decode_message(message, 0)
    assert (field.ni, field.nj) == (3, 2)
    assert field.values.shape == (2, 3)
    np.testing.assert_allclose(field.values, [[0, 1, 2], [3, 4, 5]])
    assert field.la1 == pytest.approx(-4.0)
    assert field.lo1 == pytest.approx(-5.0)
    assert field.dlat == pytest.approx(4.0)  # (0 - -4) / (2-1)
    assert field.dlon == pytest.approx(2.0)  # (-1 - -5) / (3-1)
    assert field.pole_lat == pytest.approx(-40.0)
    assert field.pole_lon == pytest.approx(10.0)


def test_applies_reference_binary_and_decimal_scale():
    """value = (R + X * 2^E) / 10^D."""
    message = _simple(
        ni=2, nj=2, values=[12.0, 12.0, 12.0, 12.0], reference=10.0, bin_scale=1, dec_scale=1,
    )
    field = decode_message(message, 0)
    # Packed value is round((12 - 10) / 2) = 1 -> (10 + 1*2) / 10 = 1.2
    np.testing.assert_allclose(field.values.ravel(), [1.2, 1.2, 1.2, 1.2])


def test_field_identity_is_the_pds_signature():
    message = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], param=61, level_type=1, level=0)
    field = decode_message(message, 0)
    assert (field.param, field.level_type, field.level) == (61, 1, 0)


def test_peek_reads_the_signature_without_decoding():
    message = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], param=71, level_type=1, level=0)
    assert peek(message, 0) == (71, 1, 0)


def test_message_bounds_walks_a_record_sequential_file():
    one = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], param=11)
    two = _simple(ni=2, nj=2, values=[2.0, 2.0, 2.0, 2.0], param=17)
    data = one + two
    bounds = list(message_bounds(data))
    assert bounds == [(0, len(one)), (len(one), len(two))]


def test_iter_messages_decodes_every_message_in_order():
    one = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], param=11)
    two = _simple(ni=2, nj=2, values=[2.0, 2.0, 2.0, 2.0], param=17)
    fields = list(iter_messages(one + two))
    assert [f.param for f in fields] == [11, 17]
    assert [f.values.ravel()[0] for f in fields] == [1.0, 2.0]


# ---------------------------------------------------------------- rejections


def test_rejects_a_grid_without_the_gds_present_bit():
    message = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], flag=0x00)
    with pytest.raises(UnsupportedGrib, match="grid definition"):
        decode_message(message, 0)


def test_rejects_a_bitmapped_message():
    message = _simple(ni=2, nj=2, values=[1.0, 1.0, 1.0, 1.0], flag=0x80 | 0x40)
    with pytest.raises(UnsupportedGrib, match="Bitmap"):
        decode_message(message, 0)


def test_rejects_an_unsupported_grid_representation_type():
    message = _message(
        _pds(),
        _gds(2, 2, 0, 0, 1, 1, 0, 0, grid_type=0),  # regular lat/lon, not rotated
        _bds([1.0, 1.0, 1.0, 1.0]),
    )
    with pytest.raises(UnsupportedGrib, match="representation type"):
        decode_message(message, 0)


def test_rejects_an_unsupported_scanning_mode():
    message = _message(
        _pds(),
        _gds(2, 2, 0, 0, 1, 1, -40, 10, scan=0x00),
        _bds([1.0, 1.0, 1.0, 1.0]),
    )
    with pytest.raises(UnsupportedGrib, match="Scanning mode"):
        decode_message(message, 0)


def test_rejects_complex_packing():
    # Corrupt the BDS flag octet in place: complex/spherical-harmonic packing.
    pds = _pds()
    gds = _gds(2, 2, -4.0, -5.0, 0.0, -1.0, -40.0, 10.0)
    bds = bytearray(_bds([1.0, 1.0, 1.0, 1.0]))
    bds[3] |= 0xC0  # flag octet is section-relative index 3
    message = _message(pds, gds, bytes(bds))
    with pytest.raises(UnsupportedGrib, match="simple grid-point packing"):
        decode_message(message, 0)


def test_rejects_a_truncated_data_section():
    pds = _pds()
    gds = _gds(4, 4, -4.0, -5.0, 0.0, -1.0, -40.0, 10.0)
    bds = _bds([1.0, 2.0])  # claims a 4x4=16-point grid, only 2 packed
    message = _message(pds, gds, bds)
    with pytest.raises(UnsupportedGrib):
        decode_message(message, 0)


def test_rejects_a_non_grib1_edition():
    message = b"GRIB" + b"\x00\x00\x10" + bytes([2]) + b"\x00" * 20
    with pytest.raises(UnsupportedGrib, match="edition"):
        list(message_bounds(message))
