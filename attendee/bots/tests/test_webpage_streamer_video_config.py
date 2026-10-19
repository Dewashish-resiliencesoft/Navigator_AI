import os
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.webpage_streamer_config import (
    DEFAULT_VIDEO_FRAME_SIZE,
    _push_vpx_config,
    apply_vp8_encoder_settings,
    build_gstreamer_pipeline_description,
    capture_settings_from_offer,
    default_fps_for_platform,
    default_frame_size_for_platform,
    default_video_bitrate_kbps,
    initial_data_webpage_streamer_js_fields,
    meeting_bot_output_rates,
    parse_video_fps,
    parse_video_frame_size,
    resolve_video_bitrate_kbps,
    resolve_video_fps,
    resolve_video_frame_size,
    video_keyframe_interval_frames,
    webpage_streamer_cli_args,
    webpage_streamer_env_vars,
    webpage_streaming_cpu_limit,
    webpage_streaming_cpu_request,
    webpage_streaming_memory_request,
    webpage_streaming_resource_limits,
)

ROOT = Path(__file__).resolve().parents[2]


class TestPlatformDefaults(unittest.TestCase):
    def test_platform_to_fps(self):
        self.assertEqual(default_fps_for_platform("google_meet"), 30)
        self.assertEqual(default_fps_for_platform("meet"), 30)
        self.assertEqual(default_fps_for_platform("zoom"), 60)
        self.assertEqual(default_fps_for_platform("zoom_web"), 60)
        self.assertEqual(default_fps_for_platform("teams"), 60)
        self.assertEqual(default_fps_for_platform("microsoft_teams"), 60)
        self.assertEqual(default_fps_for_platform("local"), 60)
        self.assertEqual(default_fps_for_platform("test"), 60)
        self.assertEqual(default_fps_for_platform(None), 60)

    def test_platform_frame_size_is_1080p(self):
        for platform in ("google_meet", "zoom", "teams", "local", None):
            self.assertEqual(default_frame_size_for_platform(platform), (1920, 1080))

    def test_env_fps_overrides_meet_default(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FPS": "60"}, clear=False):
            os.environ.pop("WEBPAGE_STREAMER_PLATFORM", None)
            self.assertEqual(resolve_video_fps(None, platform="google_meet"), 60)

    def test_env_frame_size_overrides_platform_default(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FRAME_SIZE": "1280x720"}):
            self.assertEqual(resolve_video_frame_size(None, platform="zoom"), (1280, 720))


class TestVideoFrameSize(unittest.TestCase):
    def test_default_frame_size_is_1080p(self):
        self.assertEqual(DEFAULT_VIDEO_FRAME_SIZE, (1920, 1080))

    def test_parses_1080p(self):
        self.assertEqual(parse_video_frame_size("1920x1080"), (1920, 1080))

    def test_parses_uppercase_separator(self):
        self.assertEqual(parse_video_frame_size("1920X1080"), (1920, 1080))

    def test_rejects_odd_dimensions(self):
        with self.assertRaises(ValueError):
            parse_video_frame_size("1919x1080")

    def test_rejects_missing_height(self):
        with self.assertRaises(ValueError):
            parse_video_frame_size("1920")

    def test_cli_overrides_env(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FRAME_SIZE": "1280x720"}):
            self.assertEqual(resolve_video_frame_size("1920x1080"), (1920, 1080))

    def test_env_used_when_cli_omitted(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FRAME_SIZE": "1920x1080"}):
            self.assertEqual(resolve_video_frame_size(None), (1920, 1080))

    def test_local_default_when_unset(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE", None)
        env.pop("WEBPAGE_STREAMER_PLATFORM", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_video_frame_size(None, platform="local"), (1920, 1080))
            self.assertEqual(resolve_video_frame_size(None, platform="google_meet"), (1920, 1080))


class TestVideoFps(unittest.TestCase):
    def test_meet_defaults_to_30_and_others_to_60(self):
        self.assertEqual(default_fps_for_platform("google_meet"), 30)
        self.assertEqual(default_fps_for_platform("zoom"), 60)

    def test_parses_60(self):
        self.assertEqual(parse_video_fps("60"), 60)
        self.assertEqual(parse_video_fps(60), 60)

    def test_rejects_non_positive_and_garbage(self):
        for value in ("0", "-1", "abc", "", "15.5"):
            with self.assertRaises(ValueError):
                parse_video_fps(value)

    def test_cli_overrides_env(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FPS": "15"}):
            self.assertEqual(resolve_video_fps("60"), 60)

    def test_env_used_when_cli_omitted(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FPS": "60"}):
            self.assertEqual(resolve_video_fps(None), 60)

    def test_platform_default_when_unset(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_FPS", None)
        env.pop("WEBPAGE_STREAMER_PLATFORM", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_video_fps(None, platform="google_meet"), 30)
            self.assertEqual(resolve_video_fps(None, platform="zoom"), 60)
            self.assertEqual(resolve_video_fps(None, platform="teams"), 60)
            self.assertEqual(resolve_video_fps(None), 60)


class TestGstreamerPipeline(unittest.TestCase):
    def test_pipeline_uses_configured_size_and_fps(self):
        description = build_gstreamer_pipeline_description(display_var=":99", width=1920, height=1080, framerate=60)
        self.assertIn("framerate=60/1", description)
        self.assertIn("width=1920", description)
        self.assertIn("height=1080", description)
        self.assertNotIn("framerate=15/1", description)
        # videorate actually locks the capture rate; a caps field alone does not.
        self.assertIn("videorate", description)
        self.assertIn("videoscale", description)
        self.assertIn("display-name=:99", description)

    def test_pipeline_uses_explicit_720p15(self):
        description = build_gstreamer_pipeline_description(display_var=":1", width=1280, height=720, framerate=15)
        self.assertIn("framerate=15/1", description)
        self.assertIn("width=1280", description)
        self.assertIn("height=720", description)


class TestMeetingBotOutputRates(unittest.TestCase):
    def test_meet_capture_stays_at_30_floor_when_draw_is_slower(self):
        rates = meeting_bot_output_rates(15, platform="google_meet")
        self.assertEqual(rates["draw_fps"], 15)
        # captureStream below 30 makes Google Meet reject the track.
        self.assertEqual(rates["capture_stream_fps"], 30)

    def test_60fps_raises_draw_and_capture_above_the_old_caps(self):
        rates = meeting_bot_output_rates(60)
        self.assertEqual(rates["draw_fps"], 60)
        self.assertEqual(rates["capture_stream_fps"], 60)

    def test_fps_between_15_and_30_keeps_meet_capture_floor(self):
        rates = meeting_bot_output_rates(24, platform="google_meet")
        self.assertEqual(rates["draw_fps"], 24)
        self.assertEqual(rates["capture_stream_fps"], 30)

    def test_zoom_capture_matches_configured_fps(self):
        rates = meeting_bot_output_rates(60, platform="zoom")
        self.assertEqual(rates["draw_fps"], 60)
        self.assertEqual(rates["capture_stream_fps"], 60)

    def test_initial_data_defaults(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_FPS", None)
        env.pop("WEBPAGE_STREAMER_PLATFORM", None)
        with patch.dict(os.environ, env, clear=True):
            meet_fields = initial_data_webpage_streamer_js_fields(platform="google_meet")
            zoom_fields = initial_data_webpage_streamer_js_fields(platform="zoom")
        self.assertIn("webpageStreamerVideoFps: 30", meet_fields)
        self.assertIn("webpageStreamerCaptureFps: 30", meet_fields)
        self.assertIn("webpageStreamerVideoFps: 60", zoom_fields)
        self.assertIn("webpageStreamerCaptureFps: 60", zoom_fields)

    def test_initial_data_follows_configured_fps(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FPS": "60"}):
            fields = initial_data_webpage_streamer_js_fields()
        self.assertIn("webpageStreamerVideoFps: 60", fields)
        self.assertIn("webpageStreamerCaptureFps: 60", fields)


class TestPodResources(unittest.TestCase):
    def test_cli_args_default_to_upstream(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE", None)
        env.pop("WEBPAGE_STREAMER_VIDEO_FPS", None)
        env.pop("WEBPAGE_STREAMER_PLATFORM", None)
        with patch.dict(os.environ, env, clear=True):
            args = webpage_streamer_cli_args()
        self.assertEqual(
            args,
            [
                "python",
                "bots/webpage_streamer/run_webpage_streamer.py",
                "--video-frame-size",
                "1920x1080",
                "--video-fps",
                "60",
                "--platform",
                "local",
            ],
        )

    def test_cli_args_use_meet_fps(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE", None)
        env.pop("WEBPAGE_STREAMER_VIDEO_FPS", None)
        env.pop("WEBPAGE_STREAMER_PLATFORM", None)
        with patch.dict(os.environ, env, clear=True):
            args = webpage_streamer_cli_args(platform="google_meet")
        self.assertIn("1920x1080", args)
        self.assertIn("30", args)
        self.assertIn("google_meet", args)

    def test_cli_args_follow_env_over_platform(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_FRAME_SIZE": "1280x720", "WEBPAGE_STREAMER_VIDEO_FPS": "24"}):
            args = webpage_streamer_cli_args(platform="google_meet")
        self.assertIn("1280x720", args)
        self.assertIn("24", args)

    def test_cpu_and_memory_defaults_match_1080p_compose(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMING_CPU_REQUEST", None)
        env.pop("WEBPAGE_STREAMING_MEMORY_REQUEST", None)
        env.pop("WEBPAGE_STREAMING_MEMORY_LIMIT", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(webpage_streaming_cpu_request(), "4")
            self.assertEqual(webpage_streaming_memory_request(), "8Gi")
            self.assertEqual(webpage_streaming_resource_limits()["memory"], "8Gi")

    def test_cpu_and_memory_stay_env_overridable(self):
        with patch.dict(
            os.environ,
            {
                "WEBPAGE_STREAMING_CPU_REQUEST": "1",
                "WEBPAGE_STREAMING_MEMORY_REQUEST": "4Gi",
                "WEBPAGE_STREAMING_MEMORY_LIMIT": "4Gi",
            },
        ):
            self.assertEqual(webpage_streaming_cpu_request(), "1")
            self.assertEqual(webpage_streaming_memory_request(), "4Gi")
            self.assertEqual(webpage_streaming_resource_limits()["memory"], "4Gi")

    def test_cpu_limit_is_unset_by_default(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMING_CPU_LIMIT", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(webpage_streaming_cpu_limit())
            self.assertNotIn("cpu", webpage_streaming_resource_limits())

    def test_cpu_limit_can_be_raised(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMING_CPU_LIMIT": "4"}):
            self.assertEqual(webpage_streaming_cpu_limit(), "4")
            self.assertEqual(webpage_streaming_resource_limits()["cpu"], "4")


class TestHiddenCapsRemovedFromSources(unittest.TestCase):
    def test_gstreamer_source_uses_configured_fps(self):
        source = (ROOT / "bots/webpage_streamer/webpage_streamer.py").read_text()
        self.assertNotIn("framerate=15/1", source)
        self.assertNotIn("framerate=15,", source)
        self.assertIn("build_gstreamer_pipeline_description", source)
        self.assertIn("self.video_fps", source)

    def test_canvas_capture_is_not_hardcoded_below_configured_fps(self):
        source = (ROOT / "bots/web_bot_adapter/shared_chromedriver_payload.js").read_text()
        self.assertNotIn("1000 / 15", source)
        self.assertNotIn("captureStream(30)", source)
        self.assertIn("webpageStreamerVideoFps", source)
        self.assertIn("webpageStreamerCaptureFps", source)
        self.assertIn("const WEBPAGE_STREAMER_FALLBACK_FPS = 60", source)
        self.assertEqual(source.count("WEBPAGE_STREAMER_FALLBACK_FPS"), 3)
        self.assertNotIn("|| 30", source)
        self.assertNotIn("|| 60", source)

    def test_meeting_bot_receives_streamer_fps(self):
        source = (ROOT / "bots/web_bot_adapter/web_bot_adapter.py").read_text()
        self.assertIn("initial_data_webpage_streamer_js_fields", source)

    def test_pod_creator_passes_fps_and_optional_cpu_limit(self):
        source = (ROOT / "bots/bot_pod_creator/bot_pod_creator.py").read_text()
        self.assertIn("webpage_streamer_cli_args", source)
        self.assertIn("webpage_streaming_resource_limits", source)
        self.assertIn("webpage_streamer_env_vars(", source)
        # Streamer pod and meeting-bot pod both receive the configured size and fps.
        self.assertEqual(source.count("_webpage_streamer_env()"), 2)

    def test_local_1080p_override_keeps_platform_fps(self):
        override = (ROOT / "dev.docker-compose.1080p.yaml").read_text()
        self.assertIn("1920x1080", override)
        self.assertIn("cpus:", override)
        self.assertNotIn("WEBPAGE_STREAMER_VIDEO_FPS", override)
        self.assertNotIn("dev.docker-compose.1080p60.yaml", (ROOT / "README.md").read_text())
        example = (ROOT / ".env.webpage-streamer.example").read_text()
        self.assertIn("WEBPAGE_STREAMER_VIDEO_FRAME_SIZE=1920x1080", example)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("dev.docker-compose.1080p.yaml", readme)
        self.assertIn("limits screen share to about 30 fps", readme)

    def test_local_compose_passes_platform_to_streamer(self):
        compose = (ROOT / "dev.docker-compose.yaml").read_text()
        streamer = compose.split("attendee-webpage-streamer-local:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("--platform ${WEBPAGE_STREAMER_PLATFORM:-local}", streamer)
        self.assertIn("WEBPAGE_STREAMER_PLATFORM=${WEBPAGE_STREAMER_PLATFORM:-}", streamer)


class TestVideoBitrate(unittest.TestCase):
    def test_1080p30_is_3_mbps_and_1080p60_is_6_mbps(self):
        self.assertEqual(default_video_bitrate_kbps(1920, 1080, 30), 3000)
        self.assertEqual(default_video_bitrate_kbps(1920, 1080, 60), 6000)
        self.assertGreaterEqual(default_video_bitrate_kbps(1920, 1080, 30), 2500)
        self.assertLessEqual(default_video_bitrate_kbps(1920, 1080, 30), 4000)
        self.assertGreaterEqual(default_video_bitrate_kbps(1920, 1080, 60), 5000)
        self.assertLessEqual(default_video_bitrate_kbps(1920, 1080, 60), 8000)

    def test_bitrate_scales_with_pixels_and_fps(self):
        self.assertEqual(default_video_bitrate_kbps(1280, 720, 30) * 2, default_video_bitrate_kbps(1280, 720, 60))
        self.assertLess(default_video_bitrate_kbps(1280, 720, 30), default_video_bitrate_kbps(1920, 1080, 30))

    def test_env_bitrate_override(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS": "4500"}):
            self.assertEqual(resolve_video_bitrate_kbps(1920, 1080, 60), 4500)

    def test_unset_bitrate_uses_the_formula(self):
        env = os.environ.copy()
        env.pop("WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_video_bitrate_kbps(1920, 1080, 30), 3000)

    def test_keyframe_interval_is_about_two_seconds(self):
        self.assertEqual(video_keyframe_interval_frames(30), 60)
        self.assertEqual(video_keyframe_interval_frames(60), 120)

    def test_offer_platform_selects_meet_fps(self):
        _size, fps = capture_settings_from_offer({"platform": "google_meet"}, (1920, 1080), 60, environ={})
        self.assertEqual(fps, 30)

    def test_offer_fps_overrides_startup_default(self):
        _size, fps = capture_settings_from_offer({"platform": "google_meet", "video_fps": 24}, (1920, 1080), 60, environ={})
        self.assertEqual(fps, 24)

    def test_offer_without_platform_keeps_startup_fps(self):
        _size, fps = capture_settings_from_offer({"sdp": "v=0"}, (1920, 1080), 60, environ={})
        self.assertEqual(fps, 60)

    def test_apply_vp8_overrides_aiortc_defaults(self):
        class Cfg:
            def __init__(self):
                self.kf_max_dist = 3000
                self.rc_target_bitrate = 500

        class Encoder:
            def __init__(self):
                self.cfg = None
                self.codec = None
                self._Vp8Encoder__target_bitrate = 500000
                self._Vp8Encoder__update_config_needed = False

            def encode(self, frame, force_keyframe=False):
                self.cfg = Cfg()
                self.cfg.rc_target_bitrate = self._Vp8Encoder__target_bitrate // 1000
                self.codec = object()
                return [b"ok"], 1

        encoder = Encoder()
        apply_vp8_encoder_settings(encoder, 3_000_000, 60)
        self.assertEqual(encoder._Vp8Encoder__target_bitrate, 3_000_000)
        encoder.encode(object(), False)
        self.assertEqual(encoder.cfg.kf_max_dist, 60)
        self.assertEqual(encoder.cfg.rc_target_bitrate, 3000)

    def test_1080p60_encoder_bitrate_exceeds_aiortc_cap(self):
        """Read the bitrate libvpx was given, not the number we stored beforehand."""
        from fractions import Fraction

        import aiortc.codecs.vpx as vpx
        from aiortc.codecs.vpx import Vp8Encoder
        from av import VideoFrame

        from bots.webpage_streamer.webpage_streamer import WebpageStreamer

        original_max = vpx.MAX_BITRATE
        original_min = vpx.MIN_BITRATE
        try:
            vpx.MAX_BITRATE = 1_500_000
            bitrate_bps = default_video_bitrate_kbps(1920, 1080, 60) * 1000
            self.assertGreater(bitrate_bps, 1_500_000)

            stock = Vp8Encoder()
            stock.target_bitrate = bitrate_bps
            self.assertEqual(stock.target_bitrate, 1_500_000)

            WebpageStreamer(video_frame_size=(1920, 1080), video_fps=60)
            self.assertGreaterEqual(vpx.MAX_BITRATE, bitrate_bps)

            encoder = Vp8Encoder()
            apply_vp8_encoder_settings(encoder, bitrate_bps, video_keyframe_interval_frames(60))
            frame = VideoFrame(1920, 1080, "yuv420p")
            frame.pts = 0
            frame.time_base = Fraction(1, 90000)
            encoder.encode(frame, False)
            # REMB assigns this setter after the encoder exists (rtcrtpsender.py:278).
            encoder.target_bitrate = bitrate_bps
            frame.pts = 3000
            encoder.encode(frame, False)

            self.assertEqual(encoder.target_bitrate, bitrate_bps)
            self.assertEqual(encoder.cfg.rc_target_bitrate, bitrate_bps // 1000)
            self.assertGreater(encoder.cfg.rc_target_bitrate, 1500)
        finally:
            vpx.MAX_BITRATE = original_max
            vpx.MIN_BITRATE = original_min

    def test_remb_stays_at_or_below_each_encoders_target(self):
        from fractions import Fraction

        import aiortc.codecs.vpx as vpx
        from aiortc.codecs.vpx import Vp8Encoder
        from av import VideoFrame

        original_max = vpx.MAX_BITRATE
        original_min = vpx.MIN_BITRATE
        try:
            vpx.MAX_BITRATE = 1_500_000
            meet = Vp8Encoder()
            zoom = Vp8Encoder()
            apply_vp8_encoder_settings(meet, 3_000_000, 60)
            apply_vp8_encoder_settings(zoom, 6_000_000, 120)
            # The process-wide ceiling follows the higher session. REMB still
            # has to stop at each encoder's own target.
            self.assertGreaterEqual(vpx.MAX_BITRATE, 6_000_000)
            meet.target_bitrate = 8_000_000
            zoom.target_bitrate = 8_000_000

            frame = VideoFrame(160, 120, "yuv420p")
            frame.pts = 0
            frame.time_base = Fraction(1, 90000)
            meet.encode(frame, False)
            zoom.encode(frame, False)

            self.assertEqual(meet.target_bitrate, 3_000_000)
            self.assertEqual(meet.cfg.rc_target_bitrate, 3000)
            self.assertEqual(zoom.target_bitrate, 6_000_000)
            self.assertEqual(zoom.cfg.rc_target_bitrate, 6000)
        finally:
            vpx.MAX_BITRATE = original_max
            vpx.MIN_BITRATE = original_min

    def test_push_vpx_config_logs_a_warning(self):
        class Cfg:
            kf_max_dist = 3000

        class Encoder:
            codec = object()
            cfg = Cfg()

        with self.assertLogs("bots.webpage_streamer_config", level="WARNING") as captured:
            _push_vpx_config(Encoder())
        self.assertTrue(any("VP8 encoder config" in line for line in captured.output))

    def test_video_track_recv_returns_a_real_frame(self):
        import asyncio
        from fractions import Fraction

        from bots.webpage_streamer.webpage_streamer import Gst, GstVideoStreamTrack

        width, height, fps = 64, 48, 30
        y_size = width * height
        uv_size = y_size // 4
        payload = bytes([16]) * y_size + bytes([128]) * uv_size + bytes([90]) * uv_size
        buffer = Gst.Buffer.new_allocate(None, len(payload), None)
        buffer.fill(0, payload)
        buffer.pts = 5_000_000_000

        class Sink:
            def emit(self, signal):
                self.signal = signal
                return Gst.Sample.new(buffer, None, None, None)

        sink = Sink()
        track = GstVideoStreamTrack(sink, width, height, fps)
        frame = asyncio.run(track.recv())

        self.assertEqual(sink.signal, "pull-sample")
        self.assertEqual(frame.width, width)
        self.assertEqual(frame.height, height)
        self.assertEqual(frame.format.name, "yuv420p")
        self.assertEqual(frame.time_base, Fraction(1, 1_000_000))
        self.assertEqual(frame.pts, 0)
        self.assertEqual(bytes(frame.planes[0])[:4], b"\x10\x10\x10\x10")

    def test_bitrate_override_is_forwarded_to_the_pod(self):
        with patch.dict(os.environ, {"WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS": "4500"}):
            env_vars = dict(webpage_streamer_env_vars(platform="zoom"))
        self.assertEqual(env_vars["WEBPAGE_STREAMER_VIDEO_BITRATE_KBPS"], "4500")

    def test_streamer_installs_vp8_settings_from_the_offer(self):
        source = (ROOT / "bots/webpage_streamer/webpage_streamer.py").read_text()
        self.assertIn("capture_settings_from_offer", source)
        self.assertIn("apply_vp8_encoder_settings", source)
        self.assertIn("_apply_offer_capture_settings", source)


if __name__ == "__main__":
    unittest.main()
