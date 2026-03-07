"""
Canonical GANI format field constants derived from Fox Engine binary templates.
"""

# TrackHeader fields
TRKH_ID = "Id"
TRKH_UNKNOWN_A = "UnknownA"
TRKH_UNKNOWN_B = "UnknownB"

# FoxData animation node name hashes (StrCode32) — used by old-format GANI files
# Source of truth: anim_FoxData.bt ResolveHashFunc
FOXDATA_HASH_ROOT            = 3933341002  # ROOT
FOXDATA_HASH_DEMO            = 3254096966  # DEMO
FOXDATA_HASH_CAMERA          = 2620425302  # CAMERA
FOXDATA_HASH_MOVE            = 3150281601  # MOVE
FOXDATA_HASH_CAMERA_PARAM    = 2862912361  # CameraParam  (exact bt string — camelCase)
FOXDATA_HASH_SI_FRAME        = 1885607306  # SI Frame     (exact bt string — contains space)
FOXDATA_HASH_SKEL            = 1889896775  # SKEL
FOXDATA_HASH_MESH_EVENT      = 2454300086  # MESH_EVENT
FOXDATA_HASH_MOTION          = 143688520   # MOTION — container node (bone-track data is in UNIT child)
FOXDATA_HASH_UNIT            = 3337172921  # UNIT  — TrackHeader payload: per-bone animation tracks
FOXDATA_HASH_MODEL           = 2215748180  # MODEL
FOXDATA_HASH_SKELINFO        = 3736262940  # SKELINFO
FOXDATA_HASH_MTPINFO         = 917055795   # MTPINFO
FOXDATA_HASH_MTEV            = 2846912397  # MTEV
FOXDATA_HASH_MTP             = 494270195   # MTP   — TrackHeader payload: motion point tracks
FOXDATA_HASH_EVP             = 371357229   # EVP   — EvpHeader payload: motion events
FOXDATA_HASH_SHADER          = 2250865118  # SHADER — container for facial/shader property nodes (ROOT child)
FOXDATA_HASH_MTP_LIST        = 3937479969  # MTP_LIST        — StringData payload: motion point names
FOXDATA_HASH_MTP_PARENT_LIST = 4042487769  # MTP_PARENT_LIST — StringData payload: motion point parent names
FOXDATA_HASH_SKL_LIST        = 2447659851  # SKL_LIST        — StringData payload: bone names (optional)
FOXDATA_HASH_LOCATOR         = 3187573380  # LOCATOR
FOXDATA_HASH_GLOBALSRT       = 2036377104  # GLOBALSRT — TrackHeader payload
FOXDATA_HASH_FRAGMENT        = 2053459263  # FRAGMENT  — TrackHeader payload

# Gani2 parameter name hashes (used by Gani2 format; sourced from same bt table)
FOXDATA_HASH_PARAM_TARGET_NAME = 2570203771  # TARGET_NAME
FOXDATA_HASH_PARAM_SLOPE_DIR   = 3426329078  # SLOPE_DIR
FOXDATA_HASH_PARAM_SLOPE_ANGLE = 35201703    # SLOPE_ANGLE

# SHADER child property name hashes (facial animation, children of the SHADER node)
FOXDATA_HASH_SHADER_TENSION_CHEEKL = 1097923221  # TENSION_CHEEKL
FOXDATA_HASH_SHADER_TENSION_CHEEKR = 890100143   # TENSION_CHEEKR
FOXDATA_HASH_SHADER_TENSION_NECK   = 2155534022  # TENSION_NECK


TRKH_FRAME_RATE = "FrameRate"
TRKH_FRAME_COUNT = "FrameCount"

# Custom property key prefix for per-property shader TrackHeader fields stored on actions.
# Format: action["shader_hdr_{prop_name}"] = "t_id=X ; unknown_a=Y ; unknown_b=Z ; frame_count=N ; frame_rate=W"
SHADER_HDR_PREFIX = "shader_hdr_"

# MotionEventHeader fields
EVPH_VERSION = "Version"
