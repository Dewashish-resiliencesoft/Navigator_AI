#!/usr/bin/env python3
"""
Standalone script to run WebpageStreamer without Django.

This script allows you to run the webpage streaming functionality
independently of the Django framework.
"""

import argparse
import logging
import os
import sys

# Add the project root to Python path so we can import from bots
# This file is in bots/webpage_streamer/, so go up 2 levels to reach project root
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, "..", "..")
sys.path.insert(0, project_root)

from bots.webpage_streamer.webpage_streamer import WebpageStreamer
from bots.webpage_streamer_config import resolve_video_fps, resolve_video_frame_size


def setup_logging():
    """Configure basic logging for the standalone script."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.StreamHandler(sys.stdout)])


def main(video_frame_size, video_fps):
    """Main function to run the webpage streamer."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting WebpageStreamer as standalone script at %sx%s @ %sfps...", video_frame_size[0], video_frame_size[1], video_fps)

    try:
        webpage_streamer = WebpageStreamer(video_frame_size=video_frame_size, video_fps=video_fps)
        webpage_streamer.run()
    except KeyboardInterrupt:
        logger.info("Shutting down due to keyboard interrupt...")
    except Exception as e:
        logger.error(f"Error running WebpageStreamer: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run WebpageStreamer as standalone script")
    parser.add_argument("--video-frame-size", type=str, default=None, help="Video frame size in format WIDTHxHEIGHT. Defaults to WEBPAGE_STREAMER_VIDEO_FRAME_SIZE or the platform default (1920x1080).")
    parser.add_argument("--video-fps", type=str, default=None, help="Capture frame rate. Defaults to WEBPAGE_STREAMER_VIDEO_FPS or the platform default (30 for Google Meet, 60 otherwise).")
    parser.add_argument("--platform", type=str, default=None, help="Meeting platform: google_meet, zoom, teams, or local. Defaults to WEBPAGE_STREAMER_PLATFORM or local.")
    args = parser.parse_args()
    video_frame_size_tuple = resolve_video_frame_size(args.video_frame_size, platform=args.platform)
    video_fps = resolve_video_fps(args.video_fps, platform=args.platform)
    main(video_frame_size=video_frame_size_tuple, video_fps=video_fps)
