""""
Helper program to inject absolute wall clock time into FLV stream for recordings
"""
import argparse
import struct
import sys
import time
import pyamf
from pyamf import amf0

from flvlib3.primitives import make_ui8, make_ui32
from flvlib3.tags import create_script_tag

def read_bytes(source, num_bytes):
    read_bytes = 0
    buf = b""
    while read_bytes < num_bytes:
        d_in = source.read(num_bytes - read_bytes)
        if d_in:
            read_bytes += len(d_in)
            buf += d_in
        else:
            return buf
    return buf


def write(data):
    sys.stdout.buffer.write(data)


def write_log(data):
    sys.stderr.buffer.write(f"{data}\n".encode())
    sys.stderr.flush()

def millis():
    return time.time_ns() // 1000000

def write_timestamp_trailer(tag_type, ts_ms):
    if tag_type == 8:
        write(bytes([0, 0, 187, 128, 0, 0, 0, 0, 0, 0, 0, 0]))
    else:
        write(bytes([0, 1, 95, 144, 0, 0, 0, 0, 0, 0, 0, 0]))

    write(struct.pack(">I", ts_ms))

def inject_clock_sync(timestamp, stream_clock_base, now_ms):
    data = {
        'streamClock': timestamp,
        'streamClockBase': stream_clock_base,
        'wallClock': now_ms
    }
    packet_to_inject = create_script_tag("onClockSync", data, timestamp)
    write(packet_to_inject)

def inject_on_metadata(streamName, height, width):
    width_map = {
        1920: 0,
        1024: 2,
        1280: 2,
        640: 1,
    }
    bw_map = {
        1920: 3000000,
        1024: 1500000,
        1280: 1500000,
        640: 300000
    }
    data = {
        'audioBandwidth': 64000,
        'audioChannels': 1,
        'audioFrequency': 48000,
        'channelId': width_map.get(width, 0),
        'extendedFormat': True,
        'hasAudio': True,
        'hasVideo': True,
        'streamId': width_map.get(width, 0),
        'streamName': streamName,
        'videoBandwidth': bw_map.get(width, 3000000),
        'videoFps': 15,
        'videoHeight': height,
        'videoWidth': width
    }
    packet_to_inject = create_script_tag("onMetaData", data, 0)
    write(packet_to_inject)

def inject_on_mpma(timestamp):
    data = {
        "cs": {
            "cur": 3000000,
            "max": 3000000,
            "min": 32000,
        },
        "m": {
            "cur": 3000000,
            "max": 3000000,
            "min": 300000,
        },
        "r": 0,
        "sp": {
            "cur": 3000000,
            "max": 3000000,
            "min": 0,
        },
        "t": 3000000,
    }
    packet_to_inject = create_script_tag("onMpma", data, timestamp)
    write(packet_to_inject)

def main(args):
    source = sys.stdin.buffer

    signature = read_bytes(source, 3)
    if signature != b"FLV":
        print("Not a valid FLV file")
        return

    write(signature)
    # Skip rest of FLV header
    write(read_bytes(source, 1))
    read_bytes(source, 1)
    # Write custom bitmask for FLV type
    write(make_ui8(7))
    write(read_bytes(source, 4))

    # Tag 0 previous size
    write(read_bytes(source, 4))

    start_ms = millis()
    correction_video_ms = -20000
    correction_audio_ms = -20000
    stream_clock_base = 0
    last_ts_ms = 0
    have_metadata = 0
    while True:
        now_ms = millis()

        header = read_bytes(source, 11)       
        tag_type = header[0]
        payload_size = int.from_bytes(header[1:4], byteorder='big')
        timestamp = int.from_bytes(bytes([header[7]]) + header[4:7], byteorder='big')
        stream_id = int.from_bytes(header[8:11], byteorder='big')
        payload = read_bytes(source, payload_size)
        
        #write_log(f"tag: {tag_type}\tpayload size: {payload_size}\ttimestamp: {timestamp}\tstream_id: {stream_id}")

        #if abs(now_ms - start_ms - timestamp + correction_ms) > 300:
        #    write_log(f"md: {have_metadata} drift: {now_ms - start_ms} % {timestamp} # {correction_ms} => {abs(now_ms - start_ms - timestamp + correction_ms)}")

        # dont proxy through script tags, we'll emulate them ourselves
        if tag_type == 18:
            decoder = pyamf.decode(payload, encoding=pyamf.AMF0)
            read_bytes(source, 4)   # prev tag size, discard

            script_name = decoder.readElement() # onMetaData?
            if script_name == "onMetaData":
                amf_data = decoder.readElement()

                inject_on_metadata(amf_data['streamName'], amf_data['height'], amf_data['width'])
                have_metadata = 1
            else:
                write_log(f"unknown script: {script_name}")
                write(header)
                write(payload)
                write(read_bytes(source, 4))
        else:
            write(header)
            write(payload)
            write(read_bytes(source, 4))

        write_timestamp_trailer(tag_type, now_ms - start_ms)

        if have_metadata:        
            if not last_ts_ms or now_ms - last_ts_ms >= 5000:
                last_ts_ms = now_ms
                inject_on_mpma(timestamp)
                write_timestamp_trailer(18, now_ms - start_ms)
            if tag_type == 9 and abs(now_ms - start_ms - timestamp + correction_video_ms) > 200:
                correction_video_ms = (now_ms - start_ms - timestamp) * -1
                write_log(f"sending onClockSync, video drift correction: {correction_video_ms}")
                inject_clock_sync(timestamp, stream_clock_base, now_ms)
                write_timestamp_trailer(18, now_ms - start_ms)
            if tag_type == 8 and abs(now_ms - start_ms - timestamp + correction_audio_ms) > 200:
                correction_audio_ms = (now_ms - start_ms - timestamp) * -1
                write_log(f"sending onClockSync, audio drift correction: {correction_audio_ms}")
                inject_clock_sync(timestamp, stream_clock_base, now_ms)
                write_timestamp_trailer(18, now_ms - start_ms)


def parse_args():
    parser = argparse.ArgumentParser(description="Modify Protect FLV stream")
    parser.add_argument(
        "--write-timestamps",
        action="store_true",
        help="Indicates we should write timestamp in between packets",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())

