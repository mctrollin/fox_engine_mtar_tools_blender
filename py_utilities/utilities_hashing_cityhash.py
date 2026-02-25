"""
Pure-Python implementation of Fox Engine / GzsTool file name hashing.

This replicates the four hash variants exposed by GzsTool's `-d` command:
  - hash_file_name           →  -d -h   (primary MGSV hash, 50-bit + meta-flag)
  - hash_file_name_legacy    →  -d -hl  (GZ-era hash, 48-bit)
  - hash_file_extension      →  -d -he  (extension-only hash, 13-bit)
  - hash_file_name_with_ext  →  -d -hwe (combined path+ext, 64-bit)

The underlying algorithm is CityHash v1.0.3 (Google), specifically
CityHash64WithSeeds, as used in GzsTool.Core (Atvaark/GzsTool, BobDoleOwndU/GzsTool).

Ported line-by-line from the Atvaark C# port:
  https://github.com/Atvaark/CityHash/blob/master/CityHash/CityHash.cs

String → bytes encoding uses latin-1 (matches C# Encoding.Default for ASCII paths).

Reference:
  https://github.com/BobDoleOwndU/GzsTool
  https://github.com/Atvaark/CityHash
"""

import struct

# ---------------------------------------------------------------------------
# CityHash v1.0.3 constants
# ---------------------------------------------------------------------------

_K0: int = 0xc3a5c85c97cb3127
_K1: int = 0xb492b66fbe98f273
_K2: int = 0x9ae16a3b2f90404f
_K3: int = 0xc949d7c7509e6557
_M64: int = 0xFFFFFFFFFFFFFFFF  # 64-bit mask

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _u64(x: int) -> int:
    """Mask x to 64-bit unsigned range via & 0xFFFFFFFFFFFFFFFF."""
    return x & _M64


def _fetch64(data: bytes, offset: int) -> int:
    """Fetch a 64-bit unsigned little-endian integer from data at offset."""
    return struct.unpack_from('<Q', data, offset)[0]


def _fetch32(data: bytes, offset: int) -> int:
    """Fetch a 32-bit unsigned little-endian integer from data at offset."""
    return struct.unpack_from('<I', data, offset)[0]


def _rotate64(val: int, shift: int) -> int:
    """Rotate val left by shift bits (64-bit).
    
    Handles the case where shift == 0 to avoid undefined behavior.
    """
    if shift == 0:
        return val & _M64
    return _u64((val >> shift) | (val << (64 - shift)))


def _rotate_by_at_least_1(val: int, shift: int) -> int:
    """RotateByAtLeast1 — same as rotate but shift is guaranteed > 0."""
    return _u64((val >> shift) | (val << (64 - shift)))


def _shift_mix(val: int) -> int:
    """Mix bits of val via XOR with right-shift by 47.
    
    Simple mixing function: val ^ (val >> 47).
    """
    return _u64(val ^ (val >> 47))


def _hash_128_to_64(low: int, high: int) -> int:
    """Hash128To64 — Murmur-inspired 128→64 bit mixer."""
    kMul: int = 0x9ddfea08eb382d69
    a = _u64((low ^ high) * kMul)
    a ^= (a >> 47)
    b = _u64((high ^ a) * kMul)
    b ^= (b >> 47)
    b = _u64(b * kMul)
    return b


def _hash_len16(u: int, v: int) -> int:
    """Hash two 64-bit values into a single 64-bit hash.
    
    Wrapper around _hash_128_to_64 for the common case of hashing (u, v) pairs.
    """
    return _hash_128_to_64(u, v)


# ---------------------------------------------------------------------------
# CityHash v1.0.3 core — ported from Atvaark/CityHash (CityHash.cs)
# ---------------------------------------------------------------------------

def _hash_len0_to16(s: bytes, offset: int = 0) -> int:
    """HashLen0To16 — CityHash v1.0.3.
    
    Hash strings of 0–16 bytes. Ported from Atvaark CityHash.cs.
    
    Args:
        s: Input bytes.
        offset: Starting offset in s (default 0).
    
    Returns:
        64-bit hash of s[offset:offset+len].
    """
    n = len(s) - offset
    if n > 8:
        a = _fetch64(s, offset)
        b = _fetch64(s, offset + n - 8)
        return _u64(_hash_len16(a, _rotate_by_at_least_1(_u64(b + n), n)) ^ b)
    if n >= 4:
        a = _fetch32(s, offset)
        return _hash_len16(_u64(n + (a << 3)), _fetch32(s, offset + n - 4))
    if n > 0:
        a = s[offset]
        b = s[offset + (n >> 1)]
        c = s[offset + (n - 1)]
        y = a + (b << 8)
        z = n + (c << 2)
        return _u64(_shift_mix(_u64(y * _K2 ^ z * _K3)) * _K2)
    return _K2


def _hash_len17_to32(s: bytes) -> int:
    """HashLen17To32 — CityHash v1.0.3.
    
    Hash strings of 17–32 bytes. Ported from Atvaark CityHash.cs.
    
    Args:
        s: Input bytes (must be 17–32 bytes).
    
    Returns:
        64-bit hash of s.
    """
    n = len(s)
    a = _u64(_fetch64(s, 0) * _K1)
    b = _fetch64(s, 8)
    c = _u64(_fetch64(s, n - 8) * _K2)
    d = _u64(_fetch64(s, n - 16) * _K0)
    return _hash_len16(
        _u64(_rotate64(_u64(a - b), 43) + _rotate64(c, 30) + d),
        _u64(a + _rotate64(_u64(b ^ _K3), 20) - c + n)
    )


def _weak_hash_len32_with_seeds(w: int, x: int, y: int, z: int,
                                 a: int, b: int) -> tuple[int, int]:
    """WeakHashLen32WithSeeds — CityHash v1.0.3.
    
    Fast 32-byte hash with two seed values. Returns (low, high) tuple.
    Ported from Atvaark CityHash.cs.
    
    Args:
        w, x, y, z: Four 64-bit input values (e.g., from Fetch64).
        a, b: Seed values.
    
    Returns:
        Tuple (low_64bit, high_64bit).
    """
    a = _u64(a + w)
    b = _rotate64(_u64(b + a + z), 21)
    c = a
    a = _u64(a + x)
    a = _u64(a + y)
    b = _u64(b + _rotate64(a, 44))
    return _u64(a + z), _u64(b + c)


def _weak_hash_len32_with_seeds_bytes(s: bytes, offset: int,
                                       a: int, b: int) -> tuple[int, int]:
    """WeakHashLen32WithSeeds — CityHash v1.0.3, byte array variant.
    
    Fast 32-byte hash with two seed values, reading from a byte array.
    Convenience wrapper around _weak_hash_len32_with_seeds.
    
    Args:
        s: Input bytes.
        offset: Starting offset (reads 32 bytes from offset to offset+31).
        a, b: Seed values.
    
    Returns:
        Tuple (low_64bit, high_64bit).
    """
    return _weak_hash_len32_with_seeds(
        _fetch64(s, offset),
        _fetch64(s, offset + 8),
        _fetch64(s, offset + 16),
        _fetch64(s, offset + 24),
        a, b
    )


def _hash_len33_to64(s: bytes) -> int:
    """HashLen33To64 — CityHash v1.0.3.
    
    Hash strings of 33–64 bytes. Ported from Atvaark CityHash.cs.
    
    Args:
        s: Input bytes (must be 33–64 bytes).
    
    Returns:
        64-bit hash of s.
    """
    n = len(s)
    z = _fetch64(s, 24)
    a = _u64(_fetch64(s, 0) + _u64(n + _fetch64(s, n - 16)) * _K0)
    b = _rotate64(_u64(a + z), 52)
    c = _rotate64(a, 37)
    a = _u64(a + _fetch64(s, 8))
    c = _u64(c + _rotate64(a, 7))
    a = _u64(a + _fetch64(s, 16))
    vf = _u64(a + z)
    vs = _u64(b + _rotate64(a, 31) + c)
    a = _u64(_fetch64(s, 16) + _fetch64(s, n - 32))
    z = _fetch64(s, n - 8)
    b = _rotate64(_u64(a + z), 52)
    c = _rotate64(a, 37)
    a = _u64(a + _fetch64(s, n - 24))
    c = _u64(c + _rotate64(a, 7))
    a = _u64(a + _fetch64(s, n - 16))
    wf = _u64(a + z)
    ws = _u64(b + _rotate64(a, 31) + c)
    r = _shift_mix(_u64(_u64(vf + ws) * _K2 + _u64(wf + vs) * _K0))
    return _u64(_shift_mix(_u64(r * _K0 + vs)) * _K2)


def city_hash_64(data: bytes) -> int:
    """CityHash64 — Google CityHash v1.0.3, byte array variant.
    
    Main CityHash64 function for arbitrary-length byte arrays.
    Dispatches to length-specific hash functions and handles >64 byte iterative hashing.
    
    Ported line-by-line from Atvaark C# port (CityHash.cs).
    
    Args:
        data: Input bytes.
    
    Returns:
        64-bit hash of data.
    
    Note:
        This is the canonical hash function for Fox Engine MGSV/TPP animation paths.
    """
    n = len(data)
    if n <= 32:
        if n <= 16:
            return _hash_len0_to16(data)
        return _hash_len17_to32(data)
    if n <= 64:
        return _hash_len33_to64(data)

    # For strings over 64 bytes we hash the end first, and then as we
    # loop we keep 56 bytes of state: v, w, x, y, and z.
    x = _fetch64(data, n - 40)
    y = _u64(_fetch64(data, n - 16) + _fetch64(data, n - 56))
    z = _hash_len16(_u64(_fetch64(data, n - 48) + n), _fetch64(data, n - 24))
    v = _weak_hash_len32_with_seeds_bytes(data, n - 64, n, z)
    w = _weak_hash_len32_with_seeds_bytes(data, n - 32, _u64(y + _K1), x)
    x = _u64(_u64(x * _K1) + _fetch64(data, 0))

    # Decrease len to the nearest multiple of 64, and operate on 64-byte chunks.
    chunk_len = (n - 1) & ~63
    offset = 0
    while True:
        x = _u64(_rotate64(_u64(x + y + v[0] + _fetch64(data, offset + 8)), 37) * _K1)
        y = _u64(_rotate64(_u64(y + v[1] + _fetch64(data, offset + 48)), 42) * _K1)
        x ^= w[1]
        y = _u64(y + v[0] + _fetch64(data, offset + 40))
        z = _u64(_rotate64(_u64(z + w[0]), 33) * _K1)
        v = _weak_hash_len32_with_seeds_bytes(data, offset, _u64(v[1] * _K1), _u64(x + w[0]))
        w = _weak_hash_len32_with_seeds_bytes(data, offset + 32, _u64(z + w[1]), _u64(y + _fetch64(data, offset + 16)))
        z, x = x, z
        offset += 64
        chunk_len -= 64
        if chunk_len == 0:
            break

    return _hash_len16(
        _u64(_hash_len16(v[0], w[0]) + _u64(_shift_mix(y) * _K1) + z),
        _u64(_hash_len16(v[1], w[1]) + x)
    )


def city_hash_64_with_seeds(data: bytes, seed0: int, seed1: int) -> int:
    """CityHash64WithSeeds — CityHash v1.0.3 with two seeds.
    
    Hash using two seed values via HashLen16(CityHash64(data) - seed0, seed1).
    This is used internally by Fox Engine hashing functions.
    
    Args:
        data: Input bytes.
        seed0: First 64-bit seed value.
        seed1: Second 64-bit seed value.
    
    Returns:
        64-bit hash of data with seeds incorporated.
    """
    return _hash_len16(_u64(city_hash_64(data) - seed0), seed1)


# ---------------------------------------------------------------------------
# Fox Engine / GzsTool wrappers
# ---------------------------------------------------------------------------

#: Bit-50 meta-flag OR'd into paths that are not under /Assets/ (or are tpptest)
META_FLAG: int = 0x4000000000000

#: Fox Engine file extensions used for type-id lookup in hash_file_name_with_ext
_FOX_EXTENSIONS: tuple[str, ...] = (
    "mtar", "gani", "frig", "fpk", "fpkd", "fmdl", "ftex", "fsop",
    "fage", "fage2", "fsmb", "ftxl", "fcev", "fcnp", "fcnpx", "fclo",
    "fdes", "fv2", "fox2", "fsd", "frt", "fmdlb", "fmtt", "fmttb",
    "lad", "ladb", "lad2", "lad2b", "nav", "csnav", "csenv",
    "bak", "bin", "dat", "des", "dfpk", "dfpkd",
    "lng", "lng2", "lua", "luab", "mog", "mog2", "qar",
    "grxla", "grxoc", "gsrd", "gskl", "gssp", "gtxd",
    "pftxs", "ph", "phd", "sand", "sani", "sani2",
    "sbp", "sbpc", "sdf", "sga", "sgb", "sgos", "sgpd",
    "sgt", "sim", "sims", "sns", "spd", "spdv2",
    "spsh", "spts", "srdb", "srecs", "sres", "sresb",
    "subp", "subpb", "svp", "svpb", "swvd",
    "ta", "tae", "tgt", "txml", "txp",
    "uia", "uiap", "uif", "uifb", "uigb", "uils",
    "wem", "bnk", "pck",
    "mtp", "mtpb", "xml", "xmlb",
    "veh", "vehb", "qef", "qefb",
)


def _build_extensions_map() -> dict[int, str]:
    """Build the 13-bit extension hash → extension string lookup.
    
    Pre-computes hashes for all known Fox Engine extensions and creates a
    reverse lookup table (ext_hash → extension_name). Used during
    hash_file_name_with_ext to verify that an extracted extension matches
    one of the known Fox Engine file types.
    
    Returns:
        Dict mapping 13-bit extension hash → extension string.
    
    Note:
        This is called once at module import time to populate _EXTENSIONS_MAP.
        Matches the behaviour of GzsTool's ReadDictionary and ExtensionsMap.
    """
    result: dict[int, str] = {}
    for ext in _FOX_EXTENSIONS:
        key = hash_file_extension(ext)
        if key not in result:
            result[key] = ext
    return result


def _encode(text: str) -> bytes:
    """Encode a string as bytes using latin-1 encoding.
    
    Latin-1 encoding matches C# Encoding.Default for ASCII paths. All Fox Engine
    asset paths are ASCII-range, so this encoding is transparent and lossless.
    Uses 'replace' error handling for any non-latin-1 characters (defensive).
    
    Args:
        text: Input string.
    
    Returns:
        Bytes in latin-1 encoding.
    
    Reference:
        C# GzsTool.Core uses Encoding.Default, which on Windows is ANSI
        (system code page, typically Windows-1252). For ASCII input,
        latin-1 is equivalent and portable.
    """
    return text.encode('latin-1', errors='replace')


def hash_file_name(text: str, remove_extension: bool = True) -> int:
    """Fox Engine primary path hash (-d -h).
    
    Main GzsTool hashing variant (MGSV/TPP era). Strips extension at first '.', applies
    /Assets/ prefix logic for the meta-flag, constructs seed1 from last ≤8 chars,
    calls CityHash64WithSeeds, masks to 50 bits, and OR's in META_FLAG when appropriate.
    
    Args:
        text: File path or filename string (may contain '.' for extension).
        remove_extension: If True, strip at first '.' before hashing (default True).
    
    Returns:
        50-bit hash with optional META_FLAG (bit 50) set.
    
    Meta-flag logic:
        - Paths starting with '/Assets/tpptest' → meta_flag = True
        - Paths starting with '/Assets/' (but NOT tpptest) → meta_flag = False
        - All other paths → meta_flag = True
    
    Note:
        The seed0 is always _K2 (0x9ae16a3b2f90404f). The seed1 is constructed from
        the last ≤8 bytes of the path string (after prefix stripping), packed
        little-endian into a uint64.
    """
    if remove_extension:
        dot = text.find('.')
        if dot != -1:
            text = text[:dot]

    meta_flag = False
    assets_prefix = "/Assets/"
    if text.startswith(assets_prefix):
        text = text[len(assets_prefix):]
        if text.startswith("tpptest"):
            meta_flag = True
        # paths under /Assets/ that are NOT tpptest → meta_flag stays False
    else:
        meta_flag = True  # anything not under /Assets/ gets meta_flag

    text = text.lstrip('/')

    seed0: int = _K2  # 0x9ae16a3b2f90404f

    # seed1 = last ≤8 chars of text, read right-to-left, packed as little-endian uint64
    seed1_bytes = bytearray(8)
    src = _encode(text)
    for j, i in enumerate(range(len(src) - 1, max(len(src) - 9, -1), -1)):
        seed1_bytes[j] = src[i]
    seed1 = struct.unpack_from('<Q', seed1_bytes)[0]

    raw = city_hash_64_with_seeds(_encode(text), seed0, seed1)
    masked = raw & 0x3FFFFFFFFFFFF  # 50 bits

    return (masked | META_FLAG) if meta_flag else masked


def hash_file_name_legacy(text: str, remove_extension: bool = True) -> int:
    """Fox Engine legacy path hash (-d -hl).
    
    GZ-era hashing variant (deprecated, used for backward compatibility).
    Seed1 is constructed from the first char + string length (no prefix logic).
    A null terminator is appended to the input before hashing.
    
    Args:
        text: File path or filename string (may contain '.' for extension).
        remove_extension: If True, strip at first '.' before hashing (default True).
    
    Returns:
        48-bit hash (no meta-flag).
    
    Note:
        The seed0 is always _K2 (0x9ae16a3b2f90404f). The seed1 is
        (first_byte << 16) | string_length. A null terminator ("\0") is appended
        to the input string before hashing, matching C# behavior.
    """
    if remove_extension:
        dot = text.find('.')
        if dot != -1:
            text = text[:dot]

    seed0: int = _K2
    if text:
        seed1 = _u64((_encode(text)[0] << 16) + len(text))
    else:
        seed1 = 0

    # Append null terminator (matches C# `text + "\0"`)
    data = _encode(text + "\0")
    raw = city_hash_64_with_seeds(data, seed0, seed1)
    return raw & 0xFFFFFFFFFFFF  # 48 bits


def hash_file_extension(ext: str) -> int:
    """Fox Engine extension-only hash (-d -he).
    
    Hash a file extension string (without leading dot) to a 13-bit type ID.
    Used to pack the type-id into bits 51–63 of hash_file_name_with_ext.
    
    Args:
        ext: Bare extension string, e.g., 'mtar', 'gani', 'fpk' (NO leading dot).
    
    Returns:
        13-bit hash of the extension.
    
    Note:
        Internally calls hash_file_name(ext, remove_extension=False) then masks to
        0x1FFF (13 bits). The input is NOT treated as a filename with an extension
        (remove_extension=False ensures the entire string is hashed as-is).
    """
    return hash_file_name(ext, remove_extension=False) & 0x1FFF


# Build the extension map lazily (evaluated at import time after all functions are defined)
_EXTENSIONS_MAP: dict[int, str] = _build_extensions_map()


def hash_file_name_with_ext(file_path: str) -> int:
    """Fox Engine combined path+extension hash (-d -hwe).
    
    Full MTAR/GANI file hash including both path and extension components.
    Normalises path separators, splits on first '.', looks up the 13-bit
    extension type-id, and packs: (type_id << 51) | hash_file_name(base).
    
    Args:
        file_path: Full file path, e.g., '/Assets/mgo/motion/.../foo.gani'.
    
    Returns:
        64-bit hash: 13-bit type-id (bits 51–63) | 50-bit path hash (bits 0–49)
        with possible META_FLAG at bit 50.
    
    Note:
        - Path separators are normalised: backslash '\\' → forward slash '/'
        - The extension is looked up in _EXTENSIONS_MAP to verify it matches a known
          Fox Engine extension; if not found, type_id defaults to 0.
        - The path portion (before first '.') is hashed via hash_file_name, which
          applies /Assets/ prefix stripping and meta-flag logic.
        - This is the primary hash variant used when importing/exporting Fox GANI
          animation files.
    
    Example:
        hash_file_name_with_ext("/Assets/mgo/motion/bodies/enem/enemasr.gani")
        → 64-bit hash with 13-bit 'gani' type-id packed into bits 51–63
    """
    # Normalise: backslash → forward slash (matches DenormalizeFilePath)
    file_path = file_path.replace('\\', '/')

    dot = file_path.find('.')
    if dot == -1:
        hashable_part = file_path
        extension_part = ""
    else:
        hashable_part = file_path[:dot]
        extension_part = file_path[dot + 1:]

    # Look up the 13-bit type-id for this extension
    type_id: int = 0
    ext_hash = hash_file_extension(extension_part) if extension_part else 0
    if extension_part and _EXTENSIONS_MAP.get(ext_hash) == extension_part:
        type_id = ext_hash

    path_hash = hash_file_name(hashable_part)  # 50-bit + possible META_FLAG
    return _u64((type_id << 51) | path_hash)


def strcode32(text: str, remove_extension: bool = True) -> int:
    """Fox Engine StrCode32 animation name hashing (48-bit via CityHash64 with custom seeds).
    
    Used to hash animation track names, event names, bone names, and other animation-related
    identifiers in GANI and MTAR animation binary formats. Removes extension at first '.',
    constructs seed0 and seed1 from input metadata (first character + length), calls CityHash64WithSeeds,
    masks to 48-bit, and casts to 32-bit for StrCode32 compatibility.
    
    This implements the HashWrangler.FoxEngine.StrCode() algorithm:
    https://github.com/TinManTex/HashWrangler/blob/main/HashWrangler/Hashing/FoxEngine.cs
    
    Args:
        text: String to hash (animation name, track name, bone name, event name, etc.).
              May contain a file extension; if so, strip before hashing (controlled by remove_extension).
        remove_extension: If True (default), strip at first '.' before hashing.
                         Matches HashWrangler C# default behavior.
    
    Returns:
        32-bit hash value (cast from 48-bit CityHash64WithSeeds result).
    
    Example:
        strcode32("RIG_ROOT") → uint32 hash of "RIG_ROOT" animation track
        strcode32("track.ext") → uint32 hash of "track" (ext stripped)
        strcode32("track.ext", remove_extension=False) → uint32 hash of "track.ext" (no strip)
    
    Note:
        seed0 is always K2 (0x9ae16a3b2f90404f, the CityHash constant).
        seed1 encodes: (first_char << 16) | string_length for non-empty strings, else 0.
        Input is null-terminated before hashing (C# default behavior).
    """
    # Step 1: Remove extension if requested
    if remove_extension:
        dot_idx = text.find('.')
        if dot_idx >= 0:
            text = text[:dot_idx]
    
    # Step 2: Prepare seeds for CityHash64WithSeeds
    # seed0 is always K2 (CityHash constant)
    seed0 = _K2  # 0x9ae16a3b2f90404f
    
    # seed1: (first_char << 16) | string_length
    if text:
        seed1 = _u64((ord(text[0]) << 16) | len(text))
    else:
        seed1 = 0
    
    # Step 3: Encode string with latin-1, append null terminator (matches C# behavior)
    data = _encode(text + "\0")
    
    # Step 4: Hash using CityHash64WithSeeds
    hash64 = city_hash_64_with_seeds(data, seed0, seed1)
    
    # Step 5: Mask to 48-bit (Fox Engine compatibility), then cast to 32-bit
    hash48 = hash64 & 0xFFFFFFFFFFFF
    hash32 = hash48 & 0xFFFFFFFF
    
    return hash32
