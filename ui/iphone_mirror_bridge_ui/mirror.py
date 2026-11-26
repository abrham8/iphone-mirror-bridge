"""Screen mirroring: runs UxPlay (via iphone-mirror-screen) with its video
sent to a unix socket, and draws the frames in the app's own window.

UxPlay decodes the iPhone's stream (on the GPU when VA-API is available) and
hands decoded frames to `unixfdsink`; this process reads them with
`unixfdsrc`, converts to RGB and paints them in VideoFrameItem.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstVideo  # noqa: E402
from PySide6.QtCore import Property, QObject, QRectF, Qt, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtQuick import QQuickPaintedItem  # noqa: E402

LOG = logging.getLogger("iphone-mirror-bridge-ui")
REPO_SCRIPT = Path(__file__).resolve().parents[2] / "packaging/debian/iphone-mirror-screen.sh"


def screen_script() -> list[str]:
    installed = shutil.which("iphone-mirror-screen")
    if installed:
        return [installed]
    return ["sh", str(REPO_SCRIPT)]  # running from a source checkout


def default_socket() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/iphone-mirror-bridge-{os.getuid()}"
    return str(Path(runtime) / "iphone-mirror-bridge" / "video.sock")


class FrameSource(QObject):
    """Receives frames from the socket on GStreamer's thread."""
    frame = Signal(QImage)

    def __init__(self, socket_path: str, parent=None):
        super().__init__(parent)
        Gst.init(None)
        self.socket_path = socket_path
        self._pipeline = None
        self._wanted = False
        self._retry = QTimer(self, singleShot=True, interval=700, timeout=self._start_pipeline)

    def start(self):
        self._wanted = True
        self._start_pipeline()

    def stop(self):
        self._wanted = False
        self._retry.stop()
        self._teardown()

    def _teardown(self):
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            bus = self._pipeline.get_bus()
            bus.remove_signal_watch()
            self._pipeline = None

    def _start_pipeline(self):
        self._teardown()
        if not self._wanted:
            return
        if not os.path.exists(self.socket_path):
            self._retry.start()
            return
        desc = (f'unixfdsrc socket-path="{self.socket_path}" ! queue max-size-buffers=2 leaky=downstream '
                '! videoconvert ! video/x-raw,format=RGBx '
                '! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false')
        try:
            pipeline = Gst.parse_launch(desc)
        except GLib.Error as exc:
            LOG.error("Cannot build video pipeline: %s", exc)
            return
        pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_end)
        bus.connect("message::eos", self._on_end)
        self._pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)

    def _on_end(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, _dbg = message.parse_error()
            LOG.debug("Video source ended: %s", err.message)
        self._teardown()
        if self._wanted:
            self._retry.start()

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        # VideoInfo works the same with the GStreamer 1.24 and 1.26 Python
        # bindings (1.26 wraps caps structures differently) and gives the stride.
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        width, height, stride = info.width, info.height, info.stride[0]
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if ok:
            try:
                image = QImage(mapped.data, width, height, stride, QImage.Format_RGBX8888).copy()
            finally:
                buffer.unmap(mapped)
            self.frame.emit(image)
        return Gst.FlowReturn.OK


class MirrorController(QObject):
    """Starts/stops the receiver and tracks whether video is arriving."""
    runningChanged = Signal()
    streamingChanged = Signal()
    sizeChanged = Signal()
    firewallChanged = Signal()
    message = Signal(str, str)   # title, body (for notifications)
    frameReady = Signal(QImage)
    startingChanged = Signal()
    # Emitted from worker threads; Qt queues them to the main thread.
    _startFinished = Signal(bool, str)
    _firewallFinished = Signal(bool)

    def __init__(self, prefs, socket_path: str | None = None, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.socket_path = socket_path or default_socket()
        self._running = False
        self._streaming = False
        self._last_frame = 0.0
        self._size = (0, 0)
        self._firewall_blocked = False
        self._starting = False
        self._startFinished.connect(self._started)
        self._firewallFinished.connect(self._set_firewall)
        self.source = FrameSource(self.socket_path, self)
        self.source.frame.connect(self._on_frame)
        self._watch = QTimer(self, interval=500, timeout=self._check_streaming)
        self._watch.start()
        QTimer.singleShot(0, self.checkFirewall)

    # -- properties -----------------------------------------------------------
    running = Property(bool, lambda s: s._running, notify=runningChanged)
    streaming = Property(bool, lambda s: s._streaming, notify=streamingChanged)
    videoWidth = Property(int, lambda s: s._size[0], notify=sizeChanged)
    videoHeight = Property(int, lambda s: s._size[1], notify=sizeChanged)
    landscape = Property(bool, lambda s: s._size[0] > s._size[1] > 0, notify=sizeChanged)
    firewallBlocked = Property(bool, lambda s: s._firewall_blocked, notify=firewallChanged)
    starting = Property(bool, lambda s: s._starting, notify=startingChanged)

    def _script(self, *args, embed: bool = False):
        env = dict(os.environ, IPHONE_MIRROR_QUIET="1", IPHONE_MIRROR_FPS=str(self.prefs.fps))
        if embed:
            Path(self.socket_path).parent.mkdir(parents=True, exist_ok=True)
            env["IPHONE_MIRROR_EMBED_SOCKET"] = self.socket_path
        return subprocess.run([*screen_script(), *args], env=env, capture_output=True, text=True, timeout=30)

    # -- actions ----------------------------------------------------------------
    @Slot()
    def start(self):
        if self._running or self._starting:
            return
        self._starting = True
        self.startingChanged.emit()

        def work():
            self._script("--stop")          # replace a receiver started outside the app
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
            try:
                result = self._script("--start", embed=True)
                ok, output = result.returncode == 0, result.stdout.strip()
            except (OSError, subprocess.SubprocessError) as exc:
                ok, output = False, str(exc)
            # Never touch Qt timers/objects from this thread: signal the main thread.
            self._startFinished.emit(ok, output)
        threading.Thread(target=work, daemon=True).start()

    def _started(self, ok, output):
        self._starting = False
        self.startingChanged.emit()
        output = output.splitlines()
        if ok:
            self._running = True
            self.source.start()
            self.runningChanged.emit()
            self.message.emit("Ready for screen mirroring",
                              "On the iPhone: Control Center > Screen Mirroring > iPhone Mirror Bridge")
        else:
            reason = output[-1] if output else "UxPlay did not start"
            self.message.emit("Could not start screen mirroring", reason)
        self.checkFirewall()

    @Slot()
    def stop(self):
        self.source.stop()
        if self._running:
            self._running = False
            self.runningChanged.emit()
        self._set_streaming(False)
        threading.Thread(target=lambda: self._script("--stop"), daemon=True).start()

    @Slot()
    def restart(self):
        if self._running:
            self.source.stop()
            self._running = False
            self.start()

    @Slot()
    def checkFirewall(self):
        def work():
            try:
                blocked = self._script("--firewall-status").returncode == 1
            except (OSError, subprocess.SubprocessError):
                blocked = False
            self._firewallFinished.emit(blocked)
        threading.Thread(target=work, daemon=True).start()

    def _set_firewall(self, blocked):
        if blocked != self._firewall_blocked:
            self._firewall_blocked = blocked
            self.firewallChanged.emit()

    @Slot()
    def allowFirewall(self):
        def work():
            self._script("--allow-firewall")
            self.checkFirewall()
        threading.Thread(target=work, daemon=True).start()

    # -- frames -----------------------------------------------------------------
    def _on_frame(self, image: QImage):
        self._last_frame = time.monotonic()
        size = (image.width(), image.height())
        if size != self._size:
            self._size = size
            self.sizeChanged.emit()
        self._set_streaming(True)
        self.frameReady.emit(image)

    def _check_streaming(self):
        if self._streaming and time.monotonic() - self._last_frame > 2.0:
            self._set_streaming(False)

    def _set_streaming(self, value):
        if value != self._streaming:
            self._streaming = value
            self.streamingChanged.emit()


class VideoFrameItem(QQuickPaintedItem):
    """Paints the latest frame, scaled to fit, clipped to rounded corners."""
    radiusChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._radius = 0.0
        self.setAntialiasing(True)

    def _get_radius(self):
        return self._radius

    def _set_radius(self, value):
        self._radius = float(value)
        self.radiusChanged.emit()
        self.update()

    radius = Property(float, _get_radius, _set_radius, notify=radiusChanged)

    @Slot(QImage)
    def setFrame(self, image: QImage):
        self._image = image
        self.update()

    @Slot()
    def clear(self):
        self._image = QImage()
        self.update()

    def paint(self, painter: QPainter):
        if self._image.isNull():
            return
        bounds = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(bounds, self._radius, self._radius)
        painter.setClipPath(path)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(bounds, QColor("#000000"))
        img = self._image
        scale = min(bounds.width() / img.width(), bounds.height() / img.height())
        w, h = img.width() * scale, img.height() * scale
        target = QRectF((bounds.width() - w) / 2, (bounds.height() - h) / 2, w, h)
        painter.drawImage(target, img)
