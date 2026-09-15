#!/usr/bin/env python

import os

from electrumsv.platform import platform
from electrumsv.startup import base_dir


# ============================================================================
# Qt Multimedia + zxingcpp
#
# Windows and Linux:
#     Qt Multimedia camera capture + zxingcpp decoding
#
# macOS:
#     Original CalinsQRReader helper application
#
# IMPORTANT:
#     This file does NOT use ZBar.
# ============================================================================


def _scan_barcode_qt(device='', max_duration=30):

    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QHBoxLayout,
    )
    from PyQt5.QtMultimedia import (
        QCamera,
        QCameraImageCapture,
        QCameraInfo,
    )

    import zxingcpp

    # ------------------------------------------------------------------------
    # QApplication
    # ------------------------------------------------------------------------

    app = QApplication.instance()

    if app is None:
        app = QApplication([])

    # ------------------------------------------------------------------------
    # Scanner dialog
    # ------------------------------------------------------------------------

    dialog = QDialog()
    dialog.setWindowTitle("Scan QR Code")
    dialog.setModal(True)
    dialog.resize(700, 600)

    # ------------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------------

    preview = QLabel()
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumSize(640, 480)
    preview.setStyleSheet(
        "background-color: black;"
    )

    instruction = QLabel(
        "Starting camera..."
    )
    instruction.setAlignment(Qt.AlignCenter)

    cancel_button = QPushButton("Cancel")

    button_layout = QHBoxLayout()
    button_layout.addStretch()
    button_layout.addWidget(cancel_button)
    button_layout.addStretch()

    layout = QVBoxLayout(dialog)
    layout.addWidget(preview)
    layout.addWidget(instruction)
    layout.addLayout(button_layout)

    # ------------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------------

    result = {
        'text': '',
        'finished': False,
        'closing': False,
    }

    # ------------------------------------------------------------------------
    # Find cameras
    # ------------------------------------------------------------------------

    try:
        cameras = QCameraInfo.availableCameras()

    except Exception as e:

        instruction.setText(
            "Unable to enumerate cameras: {}".format(e)
        )

        QTimer.singleShot(
            1500,
            dialog.reject
        )

        dialog.exec_()
        return ''

    if not cameras:

        instruction.setText(
            "No camera was detected."
        )

        QTimer.singleShot(
            1500,
            dialog.reject
        )

        dialog.exec_()
        return ''

    # ------------------------------------------------------------------------
    # Select camera
    # ------------------------------------------------------------------------

    selected_camera = None

    if device and device != 'default':

        for camera_info in cameras:

            try:

                if (
                    camera_info.deviceName() == device
                    or camera_info.description() == device
                ):
                    selected_camera = camera_info
                    break

            except Exception:
                pass

    if selected_camera is None:
        selected_camera = cameras[0]

    # ------------------------------------------------------------------------
    # Create camera
    # ------------------------------------------------------------------------

    camera = QCamera(selected_camera)

    # Explicitly use still-image capture.
    try:
        camera.setCaptureMode(
            QCamera.CaptureStillImage
        )
    except Exception:
        pass

    capture = QCameraImageCapture(camera)

    # ------------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------------

    capture_timer = QTimer()
    timeout_timer = QTimer()

    timeout_timer.setSingleShot(True)

    # ------------------------------------------------------------------------
    # Finish / shutdown
    #
    # IMPORTANT:
    #
    # We do NOT delete the QCamera immediately.
    #
    # On Linux, Qt Multimedia may be using GStreamer underneath.
    # Immediately destroying the camera while the dialog is closing can
    # leave the camera device in a partially-open state.
    # ------------------------------------------------------------------------

    def finish_scan(accepted):

        if result['closing']:
            return

        result['closing'] = True

        # Stop requesting new frames first.
        try:
            capture_timer.stop()
        except Exception:
            pass

        # Stop timeout.
        try:
            timeout_timer.stop()
        except Exception:
            pass

        # Stop camera.
        try:
            camera.stop()
        except Exception:
            pass

        # Give Qt Multimedia/GStreamer time to process the camera shutdown
        # before closing the dialog.
        if accepted:

            QTimer.singleShot(
                150,
                dialog.accept
            )

        else:

            QTimer.singleShot(
                150,
                dialog.reject
            )

    # ------------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------------

    def cancel_scan():

        if result['finished']:
            return

        result['finished'] = True

        instruction.setText(
            "Cancelling..."
        )

        finish_scan(False)

    cancel_button.clicked.connect(
        cancel_scan
    )

    # ------------------------------------------------------------------------
    # Camera errors
    # ------------------------------------------------------------------------

    def camera_error(error):

        if result['finished']:
            return

        try:
            error_string = camera.errorString()
        except Exception:
            error_string = ''

        if error_string:

            instruction.setText(
                "Camera error: {}".format(
                    error_string
                )
            )

        else:

            instruction.setText(
                "Unable to access the camera."
            )

    try:
        camera.error.connect(
            camera_error
        )
    except Exception:
        pass

    # ------------------------------------------------------------------------
    # Camera status
    # ------------------------------------------------------------------------

    def camera_status_changed(status):

        if result['finished']:
            return

        try:

            if status == QCamera.StartingStatus:

                instruction.setText(
                    "Starting camera..."
                )

            elif status == QCamera.LoadedStatus:

                instruction.setText(
                    "Camera ready..."
                )

            elif status == QCamera.ActiveStatus:

                instruction.setText(
                    "Point your camera at a QR code."
                )

            elif status == QCamera.UnloadedStatus:

                if not result['closing']:
                    instruction.setText(
                        "Camera stopped."
                    )

        except Exception:
            pass

    try:
        camera.statusChanged.connect(
            camera_status_changed
        )
    except Exception:
        pass

    # ------------------------------------------------------------------------
    # QImage -> zxingcpp buffer
    #
    # QImage RGB888 rows can contain padding.
    #
    # Therefore we cannot blindly assume:
    #
    #     bytes_per_line == width * 3
    #
    # We explicitly remove row padding.
    # ------------------------------------------------------------------------

    def image_to_buffer(image):

        image = image.convertToFormat(
            QImage.Format_RGB888
        )

        width = image.width()
        height = image.height()

        if width <= 0 or height <= 0:
            return None

        bytes_per_line = image.bytesPerLine()
        row_size = width * 3

        ptr = image.bits()

        try:
            ptr.setsize(
                image.byteCount()
            )
        except Exception:
            pass

        raw = bytes(ptr)

        # No padding.
        if bytes_per_line == row_size:

            return memoryview(
                raw
            ).cast(
                'B',
                (height, width, 3)
            )

        # Remove row padding.
        packed = bytearray(
            row_size * height
        )

        for y in range(height):

            src_start = y * bytes_per_line
            src_end = src_start + row_size

            dst_start = y * row_size
            dst_end = dst_start + row_size

            packed[dst_start:dst_end] = (
                raw[src_start:src_end]
            )

        return memoryview(
            packed
        ).cast(
            'B',
            (height, width, 3)
        )

    # ------------------------------------------------------------------------
    # Process captured frame
    # ------------------------------------------------------------------------

    def process_image(image_id, image):

        if result['finished']:
            return

        try:

            if image.isNull():
                return

            # ------------------------------------------------------------
            # Display preview
            # ------------------------------------------------------------

            pixmap = QPixmap.fromImage(
                image
            )

            scaled_pixmap = pixmap.scaled(
                preview.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            preview.setPixmap(
                scaled_pixmap
            )

            # ------------------------------------------------------------
            # Convert image
            # ------------------------------------------------------------

            frame = image_to_buffer(
                image
            )

            if frame is None:
                return

            # ------------------------------------------------------------
            # Decode
            # ------------------------------------------------------------

            barcodes = zxingcpp.read_barcodes(
                frame,
                try_rotate=True,
                try_downscale=True,
            )

            for barcode in barcodes:

                if not barcode.text:
                    continue

                # QR successfully decoded.
                result['text'] = barcode.text
                result['finished'] = True

                instruction.setText(
                    "QR code detected!"
                )

                finish_scan(True)

                return

        except Exception:

            # Ignore malformed individual frames.
            #
            # The scanner should continue trying subsequent frames.
            return

    capture.imageCaptured.connect(
        process_image
    )

    # ------------------------------------------------------------------------
    # Capture one frame
    # ------------------------------------------------------------------------

    def capture_frame():

        if result['finished']:
            return

        try:

            if capture.isReadyForCapture():

                capture.capture()

        except Exception:
            pass

    capture_timer.timeout.connect(
        capture_frame
    )

    # ------------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------------

    def timeout_scan():

        if result['finished']:
            return

        result['finished'] = True

        instruction.setText(
            "No QR code detected."
        )

        finish_scan(False)

    timeout_timer.timeout.connect(
        timeout_scan
    )

    # ------------------------------------------------------------------------
    # Start camera
    # ------------------------------------------------------------------------

    try:

        camera.start()

    except Exception as e:

        instruction.setText(
            "Unable to start camera: {}".format(e)
        )

        QTimer.singleShot(
            1500,
            dialog.reject
        )

        dialog.exec_()

        return ''

    # ------------------------------------------------------------------------
    # Wait until camera is active before capturing.
    # ------------------------------------------------------------------------

    def start_capture_timer():

        if result['finished'] or result['closing']:
            return

        try:

            if camera.status() == QCamera.ActiveStatus:

                instruction.setText(
                    "Point your camera at a QR code."
                )

                # About 6-7 frames per second.
                capture_timer.start(150)

            else:

                QTimer.singleShot(
                    100,
                    start_capture_timer
                )

        except Exception:

            # If status isn't available, try anyway.
            capture_timer.start(150)

    QTimer.singleShot(
        250,
        start_capture_timer
    )

    # ------------------------------------------------------------------------
    # Start overall timeout
    # ------------------------------------------------------------------------

    timeout_timer.start(
        int(max_duration * 1000)
    )

    # ------------------------------------------------------------------------
    # Run modal dialog
    # ------------------------------------------------------------------------

    try:

        dialog.exec_()

    finally:

        # Stop timers.
        try:
            capture_timer.stop()
        except Exception:
            pass

        try:
            timeout_timer.stop()
        except Exception:
            pass

        # Stop camera one final time.
        try:
            camera.stop()
        except Exception:
            pass

        # Schedule destruction through Qt rather than destroying the
        # multimedia objects immediately.
        try:
            capture.deleteLater()
        except Exception:
            pass

        try:
            camera.deleteLater()
        except Exception:
            pass

    return result['text']


# ============================================================================
# macOS
# ============================================================================

def scan_barcode_osx(*args_ignored, **kwargs_ignored):

    import subprocess

    # Original ElectrumSV macOS helper.
    prog = os.path.join(
        base_dir,
        "CalinsQRReader.app",
        "Contents",
        "MacOS",
        "CalinsQRReader"
    )

    if not os.path.exists(prog):

        raise RuntimeError(
            "Cannot start QR scanner; helper app not found."
        )

    try:

        with subprocess.Popen(
            [prog],
            stdout=subprocess.PIPE
        ) as p:

            return (
                p.stdout
                .read()
                .decode('utf-8')
                .strip()
            )

    except OSError as e:

        raise RuntimeError(
            "Cannot start camera helper app; {}".format(
                e.strerror
            )
        )


# ============================================================================
# Public scanner
# ============================================================================

def scan_barcode(
    device='',
    max_duration=30
):

    # macOS keeps its original native helper.
    if platform.name == 'MacOSX':

        return scan_barcode_osx(
            device=device
        )

    # Windows + Linux:
    #
    # Qt Multimedia camera
    # +
    # zxingcpp QR decoding
    #
    # No ZBar.
    return _scan_barcode_qt(
        device=device,
        max_duration=max_duration
    )


# ============================================================================
# Linux camera discovery
# ============================================================================

def find_system_cameras():

    device_root = "/sys/class/video4linux"

    devices = {}

    if os.path.exists(device_root):

        for device in os.listdir(device_root):

            path = os.path.join(
                device_root,
                device,
                'name'
            )

            try:

                with open(
                    path,
                    encoding='utf-8'
                ) as f:

                    name = f.read().strip()

            except Exception:

                continue

            devices[name] = os.path.join(
                "/dev",
                device
            )

    return devices


# ============================================================================
# Direct command-line testing
# ============================================================================

if __name__ == "__main__":

    print(
        scan_barcode()
    )
