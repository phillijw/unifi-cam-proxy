""""
Helper program to inject absolute wall clock time into FLV stream for recordings
"""
import argparse
import struct
import sys
import time

from flvlib3.astypes import FLVObject, make_object
from flvlib3.primitives import make_ui8, make_ui32, make_si32_extended
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


def write_timestamp_trailer(is_packet, ts_ms):
    # Write 15 byte trailer
    write(make_ui8(0))
    if is_packet:
        write(bytes([1, 95, 144, 0, 0, 0, 0, 0, 0, 0, 0]))
    else:
        write(bytes([0, 43, 17, 0, 0, 0, 0, 0, 0, 0, 0]))

    # write_log(f'ts_ms: {int(ts_ms)}; is_packet={is_packet}')

    # write(make_ui32(int(ts_ms)))
    write(make_si32_extended(int(ts_ms)))


def main(args):
    source = sys.stdin.buffer

    header = read_bytes(source, 3)

    if header != b"FLV":
        print("Not a valid FLV file")
        return
    write(header)

    # Skip rest of FLV header
    write(read_bytes(source, 1))
    read_bytes(source, 1)
    # Write custom bitmask for FLV type
    write(make_ui8(7))
    write(read_bytes(source, 4))

    # Tag 0 previous size
    write(read_bytes(source, 4))

    start_ms = time.time_ns() / 1000000
    last_ts_ms = start_ms
    i = 0
    while True:
        # Packet structure from Wikipedia:
        #
        # Size of previous packet	uint32_be	0	For first packet set to NULL
        #
        # Packet Type	uint8	18	For first packet set to AMF Metadata
        # Payload Size	uint24_be	varies	Size of packet data only
        # Timestamp Lower	uint24_be	0	For first packet set to NULL
        # Timestamp Upper	uint8	0	Extension to create a uint32_be value
        # Stream ID	uint24_be	0	For first stream of same type set to NULL
        #
        # Payload Data	freeform	varies	Data as defined by packet type

        header = read_bytes(source, 12)
        if len(header) != 12:
            write(header)
            return

        # Packet type
        packet_type = header[0]

        # Get payload size to know how many bytes to read
        high, low = struct.unpack(">BH", header[1:4])
        payload_size = (high << 16) + low

        # Get timestamp to inject into clock sync tag
        ts_low_high = header[4:8]
        ts_lower = ts_low_high[:3]
        ts_higher = ts_low_high[3] #extension. Only used if >0

        if ts_higher == 0:
            combined =  b'\x00' + ts_lower
        else:
            combined = ts_lower + ts_higher

        timestamp = struct.unpack(">i", combined)[0]

        now_ms = time.time_ns() / 1000000
        # write_log(f'({int(now_ms)}ms) timestamp: {timestamp}')

        if not last_ts_ms or now_ms - last_ts_ms >= 5000:
            last_ts_ms = now_ms
            # Insert a custom packet every so often for time synchronization
            data = FLVObject()
            data["streamClock"] = int(timestamp)
            data["streamClockBase"] = 0
            data["wallClock"] = now_ms
            packet_to_inject = create_script_tag("onClockSync", data, timestamp)
            # write_log(f'now-start: {now_ms}-{start_ms} = {now_ms - start_ms}')
            # write_log(f'data: {data}')
            write(packet_to_inject)

            # Write 15 byte trailer
            write_timestamp_trailer(False, now_ms - start_ms)

            # Write mpma tag
            # {'cs': {'cur': 1500000.0,
            #         'max': 1500000.0,
            #         'min': 32000.0},
            #  'm': {'cur': 750000.0,
            #        'max': 1500000.0,
            #        'min': 750000.0},
            #  'r': 0.0,
            #  'sp': {'cur': 1500000.0,
            #         'max': 1500000.0,
            #         'min': 150000.0},
            #  't': 750000.0}

            data = FLVObject()
            data["cs"] = FLVObject()
            data["cs"]["cur"] = 1500000
            data["cs"]["max"] = 1500000
            data["cs"]["min"] = 1500000

            data["m"] = FLVObject()
            data["m"]["cur"] = 1500000
            data["m"]["max"] = 1500000
            data["m"]["min"] = 1500000
            data["r"] = 0

            data["sp"] = FLVObject()
            data["sp"]["cur"] = 1500000
            data["sp"]["max"] = 1500000
            data["sp"]["min"] = 1500000
            data["t"] = 75000.0
            packet_to_inject = create_script_tag("onMpma", data, 0)

            write(packet_to_inject)

            # Write 15 byte trailer
            write_timestamp_trailer(False, now_ms - start_ms)

        payload = read_bytes(source, payload_size)
        custom_payload = FLVObject()

        # The first packet encountered is usually a metadata packet which contains information such as:
        #     "duration" - 64-bit IEEE floating point value in seconds
        #     "width" and "height" – 64-bit IEEE floating point value in pixels
        #     "framerate" – 64-bit IEEE floating point value in frames per second
        #     "keyframes" – an array with the positions of p-frames, needed when random access is sought.
        #     "|AdditionalHeader" - an array of required stream decoding informational pairs
        #         "Encryption" - an array of required encryption informational pairs
        #         "Metadata" - Base64 encoded string of a signed X.509 certificate containing the Adobe Access AES decryption key required
        if i == 0:
            # write_log(f'payload: {payload}')
            p = {}
            p["duration"] = struct.unpack(">d", payload[28:36])[0]
            p["width"] = struct.unpack(">d", payload[44:52])[0]
            p["height"] = struct.unpack(">d", payload[61:69])[0]
            p["videodatarate"] = struct.unpack(">d", payload[85:93])[0]
            # write_log(p)

            custom_payload["audioBandwidth"] = struct.pack(">d", 64000.0)
            custom_payload["audioChannels"] = struct.pack(">d", 1.0)
            custom_payload["audioFrequency"] = struct.pack(">d", 48000.0)
            custom_payload["channelId"] = struct.pack(">d", 0.0)
            custom_payload["extendedFormat"] = struct.pack("?", 1)
            custom_payload["hasAudio"] = struct.pack("?", 1)
            custom_payload["hasVideo"] = struct.pack("?", 1)
            custom_payload["streamId"] = struct.pack(">d", 0.0)
            custom_payload["streamName"] = 'YtRKypErhhKFl5Ug'
            custom_payload["videoBandwidth"] = struct.pack(">d", 10000000.0)
            custom_payload["videoFps"] = struct.pack(">d", 18.0)
            custom_payload["videoHeight"] = struct.pack(">d", 1920.0)
            custom_payload["videoWidth"] = struct.pack(">d", 1080.0)

            # # Replace the payload with custom data
            # payload = create_script_tag('onMetaData', custom_payload, 0)
            # write_log(f'payload: {header}{payload}')
            # f = open('output', 'wb')
            # f.write(header)
            # f.write(payload)
            # f.close()


        # Write the packet
        write(header)
        write(payload)

        # Write previous packet size
        write(read_bytes(source, 3))

        # Write 15 byte trailer
        write_timestamp_trailer(packet_type == 9, now_ms - start_ms)

        # Write mpma tag
        i += 1


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
