"""Resolution and frame rate for the webpage streamer.

Per-platform defaults (overridden by WEBPAGE_STREAMER_VIDEO_FRAME_SIZE and
WEBPAGE_STREAMER_VIDEO_FPS, and by explicit CLI flags):

- Google Meet: 1920x1080 at 30 fps (Meet's screen-share ceiling)
- Zoom, Microsoft Teams, and local/test: 1920x1080 at 60 fps

Google Meet rejects canvas.captureStream() below 30 fps, so the meeting bot
never captures the Meet canvas slower than that. A higher configured fps raises
the capture rate instead of leaving 30 as a hidden cap.
"""

import logging
import os

logger = logging.getLogger(__name__)

MEET_VIDEO_FPS = 30
DEFAULT_VIDEO_FPS = 60
DEFAULT_VIDEO_FRAME_SIZE = (1920, 1080)
# Google Meet rejects canvas.captureStream() frame rates below 30.
MEET_CAPTURE_STREAM_FPS_FLOOR = 30

_PLATFORM_ALIASES = {
    "google_meet": "google_meet",
    "googlemeet": "google_meet",
    "meet": "google_meet",
    "zoom": "zoom",
    "zoom_web": "zoom",
    "teams": "teams",
    "microsoft_teams": "teams",
    "microsoftteams": "teams",
    "local": "local",
    "test": "local",
}


def canonical_platform(platform):
    """Map a meeting platform to google_meet, zoom, teams, or local."""
    if platform is None:
        return "local"
    key = str(platform).strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return "local"
    return _PLATFORM_ALIASES.get(key, "local")


def default_fps_for_platform(platform):
    """Google Meet stays at 30. Zoom, Teams, and local/test use 60."""
    if canonical_platform(platform) == "google_meet":
        return MEET_VIDEO_FPS
    return DEFAULT_VIDEO_FPS


def default_frame_size_for_platform(platform):
    """Every supported platform shares 1920x1080. `platform` selects the table row."""
    canonical_platform(platform)
    return DEFAULT_VIDEO_FRAME_SIZE


def parse_video_frame_size(value):
    """Parse WIDTHxHEIGHT. Both dimensions must be positive even integers (I420)."""
    if not isinstance(value, str):
        raise ValueError(f"Video frame size must look like 1920x1080, got {value!r}")
    parts = value.strip().lower().split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"Video frame size must look like 1920x1080, got {value!r}")
    width, height = int(parts[0]), int(parts[1])
    if width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError(f"Video frame size must be positive even width and height, got {width}x{height}")
    return width, height


def parse_video_fps(value):
    """Parse a positive integer frame rate."""
    if isinstance(value, bool):
        raise ValueError(f"Video fps must be a positive integer, got {value!r}")
    if isinstance(value, int):
        fps = value
    elif isinstance(value, str) and value.strip().isdigit():
        fps = int(value.strip())
    else:
        raise ValueError(f"Video fps must be a positive integer, got {value!r}")
    if fps < 1:
        raise ValueError(f"Video fps must be a positive integer, got {value!r}")
    return fps


def _platform_from_env(platform, env):
    if platform not in (None, ""):
        return platform
    return env.get("WEBPAGE_STREAMER_PLATFORM", "")


def resolve_video_frame_size(cli_value, platform=None, environ=None):
    """CLI, then WEBPAGE_STREAMER_VIDEO_FRAME_SIZE, then the platform default."""
    env = os.environ if environ is None else environ
    if cli_value:
        return parse_video_frame_size(cli_value)
    raw = env.get("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE", "").strip()
    if raw:
        return parse_video_frame_size(raw)
    return default_frame_size_for_platform(_platform_from_env(platform, env))


def resolve_video_fps(cli_value, platform=None, environ=None):
    """CLI, then WEBPAGE_STREAMER_VIDEO_FPS, then the platform default."""
    env = os.environ if environ is None else environ
    if cli_value not in (None, ""):
        return parse_video_fps(cli_value)
    raw = env.get("WEBPAGE_STREAMER_VIDEO_FPS", "").strip()
    if raw:
        return parse_video_fps(raw)
    return default_fps_for_platform(_platform_from_env(platform, env))


def format_video_frame_size(frame_size):
    width, height = frame_size
    return f"{width}x{height}"


def build_gstreamer_pipeline_description(display_var, width, height, framerate):
    """X11 capture pipeline. videoscale/videorate enforce the configured size and fps."""
    fps = parse_video_fps(framerate)
    if width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError(f"Video frame size must be positive even width and height, got {width}x{height}")
    return f"""
            ximagesrc display-name={display_var} use-damage=0 show-pointer=false
                ! videoscale
                ! videorate
                ! video/x-raw,framerate={fps}/1,width={width},height={height}
                ! videoconvert
                ! video/x-raw,format=I420,width={width},height={height}
                ! queue max-size-buffers=5 max-size-time=0 leaky=downstream
                ! appsink name=video_sink emit-signals=false max-buffers=1 drop=true

            alsasrc device=default
                ! audio/x-raw,format=S16LE,channels=1,rate=16000
                ! audioconvert
                ! audioresample
                ! queue max-size-buffers=8000 leaky=downstream
                ! appsink name=audio_sink emit-signals=false max-buffers=8000 drop=true
        """


def meeting_bot_output_rates(video_fps, platform=None):
    """Rates for the meeting bot that re-publishes the streamer video.

    The canvas draw rate matches the configured fps. On Google Meet, captureStream
    stays at least 30 because Meet rejects slower tracks, and rises with the
    configured fps when that is higher.
    """
    fps = parse_video_fps(video_fps)
    if canonical_platform(platform) == "google_meet":
        capture_stream_fps = max(MEET_CAPTURE_STREAM_FPS_FLOOR, fps)
    else:
        capture_stream_fps = fps
    return {
        "draw_fps": fps,
        "capture_stream_fps": capture_stream_fps,
    }


def initial_data_webpage_streamer_js_fields(platform=None, environ=None):
    """JS object fields injected into window.initialData on the meeting bot."""
    env = os.environ if environ is None else environ
    fps = resolve_video_fps(None, platform=platform, environ=env)
    rates = meeting_bot_output_rates(fps, platform=platform)
    return f"webpageStreamerVideoFps: {rates['draw_fps']}, webpageStreamerCaptureFps: {rates['capture_stream_fps']}"


def webpage_streamer_cli_args(platform=None, environ=None):
    env = os.environ if environ is None else environ
    frame_size = resolve_video_frame_size(None, platform=platform, environ=env)
    fps = resolve_video_fps(None, platform=platform, environ=env)
    return [
        "python",
        "bots/webpage_streamer/run_webpage_streamer.py",
        "--video-frame-size",
        format_video_frame_size(frame_size),
        "--video-fps",
        str(fps),
        "--platform",
        canonical_platform(_platform_from_env(platform, env)),
    ]


# aiortc 1.10.1 src/aiortc/codecs/vpx.py defaults, which are too low for 1080p:
#   DEFAULT_BITRATE = 500000          line 15 (500 kbps)
#   MAX_BITRATE = 1500000             line 17 (the setter at line 372 clamps to this)
#   Vp8Encoder.cfg.kf_max_dist = 3000 line 279 (3000 frames, about 100s at 30 fps)
# 1920*1080*fps // 20736 is 3000 kbps at 30 fps and 6000 kbps at 60 fps.
_PIXEL_FPS_PER_KBPS = 20736
KEYFRAME_INTERVAL_SECONDS = 2


def parse_video_bitrate_kbps(value):
    """Parse a positive integer bitrate in kbps."""
    return parse_video_fps(value)


def default_video_bitrate_kbps(width, height, fps):
    """Scale bitrate with pixels and frame rate. 1080p30 is 3 Mbps, 1080p60 is 6 Mbps."""
    fps = parse_video_fps(fps)
    if width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError(f"Video frame size must be positive even width and height, got {width}x{height}")
    return max(1, (width * height * fps) // _PIXEL_FPS_PER_KBPS)


def resolve_video_bitrate_kbps(width, height, fps, environ=None):
    """WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS, otherwise scale with resolution and fps."""
    env = os.environ if environ is None else environ
    raw = env.get("WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS", "").strip()
    if raw:
        return parse_video_bitrate_kbps(raw)
    return default_video_bitrate_kbps(width, height, fps)


def video_keyframe_interval_frames(fps):
    """Keyframe about every 2 seconds. aiortc's default kf_max_dist is 3000 frames."""
    return parse_video_fps(fps) * KEYFRAME_INTERVAL_SECONDS


def capture_settings_from_offer(params, frame_size, fps, environ=None):
    """Use platform or fps from the meeting bot's /offer when capture has not started."""
    if not isinstance(params, dict):
        return frame_size, fps
    platform = params.get("platform")
    video_fps = params.get("video_fps")
    if platform in (None, "") and video_fps in (None, ""):
        return frame_size, fps
    resolved_fps = resolve_video_fps(
        None if video_fps in (None, "") else video_fps,
        platform=None if platform in (None, "") else platform,
        environ=environ,
    )
    return frame_size, resolved_fps


def install_aiortc_vpx_bitrate_ceiling(bitrate_bps):
    """Raise aiortc's VP8 bitrate cap before any encoder or REMB update.

    aiortc 1.10.1 src/aiortc/codecs/vpx.py:17 sets MAX_BITRATE = 1500000.
    Vp8Encoder.target_bitrate (vpx.py:372) clamps every assignment to that cap,
    including REMB from RTCRtpSender (rtcrtpsender.py:278). The next encode
    copies the clamped value into cfg.rc_target_bitrate (vpx.py:394) and passes
    it to libvpx. Patch the module global here. Do not fork or vendor aiortc.
    """
    try:
        import aiortc.codecs.vpx as vpx
    except ImportError:
        return
    bitrate_bps = int(bitrate_bps)
    if bitrate_bps > vpx.MAX_BITRATE:
        vpx.MAX_BITRATE = bitrate_bps
    if 0 < bitrate_bps < vpx.MIN_BITRATE:
        vpx.MIN_BITRATE = bitrate_bps


def _cap_encoder_target_bitrate(encoder, bitrate_bps):
    """Keep REMB from pushing this encoder above its own target.

    aiortc's MAX_BITRATE is one process-wide number and this module only raises
    it. A 60 fps session would otherwise leave the cap at 6 Mbps, and a later
    30 fps Meet encoder would accept a REMB above its 3 Mbps target. The cap
    lives on the instance. REMB assigns encoder.target_bitrate, so the setter
    on that instance enforces it.
    """
    bitrate_bps = int(bitrate_bps)
    install_aiortc_vpx_bitrate_ceiling(bitrate_bps)
    encoder._webpage_streamer_bitrate_cap_bps = bitrate_bps

    cls = type(encoder)
    if getattr(cls, "_webpage_streamer_bitrate_capped", False):
        return

    capped = getattr(cls, "_webpage_streamer_capped_subclass", None)
    if capped is None:
        original_set = cls.target_bitrate.fset

        def target_bitrate(self, bitrate):
            cap = getattr(self, "_webpage_streamer_bitrate_cap_bps", None)
            if cap is not None:
                bitrate = min(int(bitrate), int(cap))
            original_set(self, bitrate)

        capped = type(
            "WebpageStreamerVp8Encoder",
            (cls,),
            {
                "target_bitrate": property(cls.target_bitrate.fget, target_bitrate),
                "_webpage_streamer_bitrate_capped": True,
            },
        )
        cls._webpage_streamer_capped_subclass = capped
    encoder.__class__ = capped


def _push_vpx_config(encoder):
    codec = getattr(encoder, "codec", None)
    cfg = getattr(encoder, "cfg", None)
    if codec is None or cfg is None:
        return
    try:
        from aiortc.codecs.vpx import _vpx_assert, lib
    except ImportError:
        return
    try:
        _vpx_assert(lib.vpx_codec_enc_config_set(codec, cfg))
    except Exception as exc:
        logger.warning("Could not push VP8 encoder config: %s", exc)


def apply_vp8_encoder_settings(encoder, bitrate_bps, keyframe_interval_frames):
    """Override aiortc's 500 kbps / kf_max_dist=3000 defaults on one Vp8Encoder."""
    bitrate_bps = int(bitrate_bps)
    keyframe_interval_frames = int(keyframe_interval_frames)
    # The public setter is what REMB uses. It stores the bitrate aiortc later
    # copies into cfg.rc_target_bitrate. Objects without that setter (tests)
    # get the same private fields the setter writes.
    if hasattr(encoder, "target_bitrate"):
        _cap_encoder_target_bitrate(encoder, bitrate_bps)
        encoder.target_bitrate = bitrate_bps
    else:
        install_aiortc_vpx_bitrate_ceiling(bitrate_bps)
        private_bitrate = "_Vp8Encoder__target_bitrate"
        private_dirty = "_Vp8Encoder__update_config_needed"
        if hasattr(encoder, private_bitrate):
            setattr(encoder, private_bitrate, bitrate_bps)
        if hasattr(encoder, private_dirty):
            setattr(encoder, private_dirty, True)

    original_encode = encoder.encode

    def encode(frame, force_keyframe=False):
        payloads, timestamp = original_encode(frame, force_keyframe)
        cfg = getattr(encoder, "cfg", None)
        # encode() hardcodes kf_max_dist = 3000 before init. Fix that only.
        # rc_target_bitrate stays whatever Vp8Encoder.__update_config wrote.
        if cfg is not None and getattr(cfg, "kf_max_dist", None) != keyframe_interval_frames:
            cfg.kf_max_dist = keyframe_interval_frames
            _push_vpx_config(encoder)
        return payloads, timestamp

    encoder.encode = encode
    return encoder


def _bitrate_env_var(env):
    raw = env.get("WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS", "").strip()
    if not raw:
        return []
    return [("WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS", str(parse_video_bitrate_kbps(raw)))]


def webpage_streaming_cpu_request(environ=None):
    """Match dev.docker-compose.1080p.yaml (4 CPUs). Override with WEBPAGE_STREAMING_CPU_REQUEST."""
    env = os.environ if environ is None else environ
    return env.get("WEBPAGE_STREAMING_CPU_REQUEST", "4")


def webpage_streaming_memory_request(environ=None):
    """Match dev.docker-compose.1080p.yaml (8g). Override with WEBPAGE_STREAMING_MEMORY_REQUEST."""
    env = os.environ if environ is None else environ
    return env.get("WEBPAGE_STREAMING_MEMORY_REQUEST", "8Gi")


def webpage_streaming_cpu_limit(environ=None):
    """Optional Kubernetes CPU limit. Unset means no CPU limit (upstream behavior)."""
    env = os.environ if environ is None else environ
    value = env.get("WEBPAGE_STREAMING_CPU_LIMIT", "").strip()
    return value or None


def webpage_streaming_resource_limits(environ=None):
    env = os.environ if environ is None else environ
    limits = {
        "memory": env.get("WEBPAGE_STREAMING_MEMORY_LIMIT", "8Gi"),
        "ephemeral-storage": env.get("WEBPAGE_STREAMING_EPHEMERAL_STORAGE_LIMIT", "0.5Gi"),
    }
    cpu_limit = webpage_streaming_cpu_limit(environ=env)
    if cpu_limit:
        limits["cpu"] = cpu_limit
    return limits


def webpage_streamer_env_vars(platform=None, environ=None):
    """Resolved size, fps, and platform passed into the streamer and meeting-bot pods."""
    env = os.environ if environ is None else environ
    resolved_platform = canonical_platform(_platform_from_env(platform, env))
    frame_size = resolve_video_frame_size(None, platform=resolved_platform, environ=env)
    fps = resolve_video_fps(None, platform=resolved_platform, environ=env)
    return [
        ("WEBPAGE_STREAMER_PLATFORM", resolved_platform),
        ("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE", format_video_frame_size(frame_size)),
        ("WEBPAGE_STREAMER_VIDEO_FPS", str(fps)),
        *_bitrate_env_var(env),
    ]
