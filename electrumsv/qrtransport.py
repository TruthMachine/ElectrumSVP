"""
Animated QR transport for ElectrumSVP.

This module provides a simple multipart QR framing protocol.

It does not know anything about Bitcoin transactions. It accepts and
returns arbitrary text payloads.

Protocol format:

    ESVPQR1|message_id|frame_number|total_frames|checksum|payload

Example:

    ESVPQR1|a1b2c3d4|1|3|8f14e45f...|<payload chunk>

The checksum is the SHA-256 hash of the complete original payload.

A receiver can therefore:
    1. Identify animated QR frames.
    2. Group frames belonging to the same message.
    3. Ignore duplicate frames.
    4. Reassemble the payload in the correct order.
    5. Verify the SHA-256 checksum.
"""

import hashlib
import secrets
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_PREFIX = "ESVPQR1"

# Maximum size of the complete animated QR frame in bytes.
#
# This includes the protocol header, message ID, frame numbers,
# checksum, separators, and payload.
#
# Keeping the complete frame below this size makes animated QR
# symbols smaller and easier for the camera to decode.
MAX_FRAME_BYTES = 900


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checksum(data: str) -> str:
    """Return the SHA-256 checksum of a text payload."""

    return hashlib.sha256(
        data.encode("utf-8")
    ).hexdigest()


def _new_message_id() -> str:
    """Generate a short random identifier for one QR transmission."""

    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Frame creation
# ---------------------------------------------------------------------------

def create_frames(
    data: str,
    chunk_size: Optional[int] = None,
    message_id: Optional[str] = None,
):
    """
    Split a text payload into animated QR frames.

    Each complete frame is guaranteed to be no larger than
    MAX_FRAME_BYTES when encoded as UTF-8.

    Args:
        data:
            Complete payload to transmit.

        chunk_size:
            Optional maximum number of characters in each payload
            chunk. The resulting frame can never exceed
            MAX_FRAME_BYTES.

        message_id:
            Optional message identifier. If omitted, a random one is
            generated.

    Returns:
        A list of QR frame strings.

    Raises:
        ValueError:
            If the payload or chunk size is invalid.
    """

    if not isinstance(data, str):
        raise TypeError("data must be a string")

    if not data:
        raise ValueError("data must not be empty")

    if chunk_size is not None and chunk_size <= 0:
        raise ValueError(
            "chunk_size must be greater than zero"
        )

    if message_id is None:
        message_id = _new_message_id()

    if not message_id:
        raise ValueError("message_id must not be empty")

    checksum = _checksum(data)

    # Start with the largest possible chunk size.
    #
    # The exact header size depends on the number of frames, so we
    # will verify the final frames below and reduce the chunk size
    # if necessary.
    if chunk_size is None:
        current_chunk_size = MAX_FRAME_BYTES
    else:
        current_chunk_size = min(
            chunk_size,
            MAX_FRAME_BYTES,
        )

    while True:

        chunks = [
            data[i:i + current_chunk_size]
            for i in range(0, len(data), current_chunk_size)
        ]

        total_frames = len(chunks)

        frames = []
        oversized = False

        for index, chunk in enumerate(chunks, start=1):

            frame = "{}|{}|{}|{}|{}|{}".format(
                PROTOCOL_PREFIX,
                message_id,
                index,
                total_frames,
                checksum,
                chunk,
            )

            frame_size = len(
                frame.encode("utf-8")
            )

            if frame_size > MAX_FRAME_BYTES:
                oversized = True
                break

            frames.append(frame)

        if not oversized:
            return frames

        # Reduce the payload chunk size until the complete frame
        # fits within the hard byte limit.
        current_chunk_size -= 1

        if current_chunk_size <= 0:
            raise ValueError(
                "Unable to create animated QR frames under "
                "{} bytes".format(MAX_FRAME_BYTES)
            )


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------

def parse_frame(frame: str):
    """
    Parse one animated QR frame.

    Returns:
        None if this is not an ESVP animated QR frame.

        Otherwise returns a dictionary containing:

            {
                'message_id': str,
                'frame_number': int,
                'total_frames': int,
                'checksum': str,
                'payload': str,
            }
    """

    if not isinstance(frame, str):
        return None

    prefix = PROTOCOL_PREFIX + "|"

    if not frame.startswith(prefix):
        return None

    # Split only the first five separators.
    #
    # This allows the payload itself to contain "|" characters.
    parts = frame.split("|", 5)

    if len(parts) != 6:
        raise ValueError("Invalid animated QR frame")

    (
        protocol,
        message_id,
        frame_number_text,
        total_frames_text,
        checksum,
        payload,
    ) = parts

    if protocol != PROTOCOL_PREFIX:
        return None

    if not message_id:
        raise ValueError(
            "Animated QR frame has no message ID"
        )

    try:
        frame_number = int(frame_number_text)
        total_frames = int(total_frames_text)
    except ValueError:
        raise ValueError(
            "Invalid animated QR frame numbering"
        )

    if total_frames <= 0:
        raise ValueError(
            "Invalid total frame count"
        )

    if frame_number < 1 or frame_number > total_frames:
        raise ValueError(
            "Animated QR frame number out of range"
        )

    if len(checksum) != 64:
        raise ValueError(
            "Invalid animated QR checksum"
        )

    try:
        int(checksum, 16)
    except ValueError:
        raise ValueError(
            "Invalid animated QR checksum"
        )

    return {
        "message_id": message_id,
        "frame_number": frame_number,
        "total_frames": total_frames,
        "checksum": checksum,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Reassembly
# ---------------------------------------------------------------------------

class QRReassembler:
    """
    Collect animated QR frames and reconstruct the original payload.

    Frames may arrive:
        - in order
        - out of order
        - multiple times

    Duplicate frames are ignored.
    """

    def __init__(self):
        self.message_id = None
        self.total_frames = None
        self.checksum = None
        self.frames: Dict[int, str] = {}

    def add_frame(self, frame: str) -> bool:
        """
        Add one QR frame.

        Returns:
            True if the frame was accepted.

        Returns False for duplicate frames.

        Raises:
            ValueError if the frame conflicts with the current message.
        """

        parsed = parse_frame(frame)

        if parsed is None:
            raise ValueError(
                "Not an ESVP animated QR frame"
            )

        message_id = parsed["message_id"]
        total_frames = parsed["total_frames"]
        checksum = parsed["checksum"]
        frame_number = parsed["frame_number"]
        payload = parsed["payload"]

        # First frame establishes the message metadata.
        if self.message_id is None:

            self.message_id = message_id
            self.total_frames = total_frames
            self.checksum = checksum

        else:

            # All subsequent frames must belong to exactly the same
            # transmission.
            if message_id != self.message_id:
                raise ValueError(
                    "Animated QR frame belongs to another message"
                )

            if total_frames != self.total_frames:
                raise ValueError(
                    "Animated QR frame count does not match"
                )

            if checksum != self.checksum:
                raise ValueError(
                    "Animated QR checksum does not match"
                )

        # Ignore an identical duplicate frame.
        if frame_number in self.frames:

            if self.frames[frame_number] == payload:
                return False

            raise ValueError(
                "Conflicting data for animated QR frame {}".format(
                    frame_number
                )
            )

        self.frames[frame_number] = payload

        return True

    def is_complete(self) -> bool:
        """Return True when all expected frames have been received."""

        if self.total_frames is None:
            return False

        return len(self.frames) == self.total_frames

    def frame_count(self) -> int:
        """Return the number of unique frames currently received."""

        return len(self.frames)

    def expected_frame_count(self) -> int:
        """Return the total number of frames expected."""

        if self.total_frames is None:
            return 0

        return self.total_frames

    def progress(self) -> float:
        """Return reception progress as a value between 0.0 and 1.0."""

        if self.total_frames is None:
            return 0.0

        return len(self.frames) / float(self.total_frames)

    def reassemble(self) -> str:
        """
        Reassemble and verify the complete payload.

        Raises:
            ValueError if the message is incomplete or the checksum fails.

        Returns:
            The original payload string.
        """

        if not self.is_complete():
            raise ValueError(
                "Animated QR message is incomplete: received {} of {} "
                "frames".format(
                    self.frame_count(),
                    self.expected_frame_count(),
                )
            )

        payload = "".join(
            self.frames[index]
            for index in range(1, self.total_frames + 1)
        )

        actual_checksum = _checksum(payload)

        if actual_checksum != self.checksum:
            raise ValueError(
                "Animated QR checksum verification failed"
            )

        return payload

    def reset(self) -> None:
        """Clear all currently collected frames."""

        self.message_id = None
        self.total_frames = None
        self.checksum = None
        self.frames.clear()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def is_animated_qr(frame: str) -> bool:
    """
    Return True if a decoded QR string uses the ESVP animated QR protocol.
    """

    if not isinstance(frame, str):
        return False

    return frame.startswith(
        PROTOCOL_PREFIX + "|"
    )
