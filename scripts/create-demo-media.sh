#!/usr/bin/env bash
set -euo pipefail
mkdir -p media/channel1
ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=640x360:rate=24' -f lavfi -i 'sine=frequency=440:sample_rate=44100' -t 30 -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -f hls -hls_time 5 -hls_list_size 0 -hls_segment_filename 'media/channel1/segment%03d.ts' media/channel1/index.m3u8
echo 'Demo HLS created in media/channel1'
