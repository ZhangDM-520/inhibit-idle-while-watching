#!/usr/bin/env python3
"""Pure tests for the face-watch helper (no camera, no cv2, no dbus).

Every IO-heavy import in face-watch is lazy, so the decision layer can be
exercised under a bare python3: device parsing/selection, sensitivity mapping,
and the episode-loop decision that owns the hold/release contract.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import pathlib
import unittest

HELPER = pathlib.Path(__file__).resolve().parent.parent / "inhibit-idle-while-watching" / "face-watch"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("face_watch", str(HELPER))
    spec = importlib.util.spec_from_loader("face_watch", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


fw = load_helper()

LISTING = """\
ASUS FHD webcam: ASUS FHD webca (usb-0000:c4:00.4-1):
        /dev/video0
        /dev/video1
        /dev/video2
        /dev/video3

Integrated Camera: Integrate (usb-0000:c4:00.3-2):
        /dev/video4
        /dev/video5
"""


class ParseDevices(unittest.TestCase):
    def test_parses_both_devices_in_order(self):
        devices = fw.parse_v4l2_devices(LISTING)
        self.assertEqual([primary for primary, _ in devices],
                         ["/dev/video0", "/dev/video4"])
        self.assertEqual(devices[0][1],
                         ["/dev/video0", "/dev/video1", "/dev/video2", "/dev/video3"])

    def test_empty_listing(self):
        self.assertEqual(fw.parse_v4l2_devices(""), [])

    def test_header_without_nodes_is_dropped(self):
        self.assertEqual(fw.parse_v4l2_devices("Some header:\n\n"), [])


class PickCamera(unittest.TestCase):
    def test_prefers_webcam_named_device(self):
        devices = fw.parse_v4l2_devices(LISTING)
        capable = {f"/dev/video{i}": i in (0, 4) for i in range(6)}
        self.assertEqual(fw.pick_camera(devices, capable), "/dev/video0")

    def test_skips_metadata_nodes(self):
        devices = fw.parse_v4l2_devices(LISTING)
        capable = {f"/dev/video{i}": i in (1, 5) for i in range(6)}
        self.assertEqual(fw.pick_camera(devices, capable), "/dev/video1")

    def test_none_when_nothing_can_capture(self):
        devices = fw.parse_v4l2_devices(LISTING)
        self.assertIsNone(fw.pick_camera(devices, {}))

    def test_falls_back_to_lowest_capable_without_names(self):
        devices = [("/dev/video8", ["/dev/video8"]), ("/dev/video2", ["/dev/video2"])]
        capable = {"/dev/video8": True, "/dev/video2": True}
        self.assertEqual(fw.pick_camera(devices, capable), "/dev/video8")


class SensitivityMapping(unittest.TestCase):
    def test_haar_neighbors_clamped(self):
        self.assertEqual(fw.haar_neighbors(1), 2)
        self.assertEqual(fw.haar_neighbors(5), 5)
        self.assertEqual(fw.haar_neighbors(99), 12)

    def test_dnn_threshold_monotone_and_clamped(self):
        values = [fw.score_threshold(s) for s in (1, 5, 10, 0, 99)]
        self.assertEqual(values[0], fw.score_threshold(1))
        self.assertLess(values[0], values[1])
        self.assertLess(values[1], values[2])
        self.assertEqual(values[3], fw.score_threshold(1))
        self.assertEqual(values[4], fw.score_threshold(10))


class HaarCascadePath(unittest.TestCase):
    """Cascade resolution must work on distro OpenCV (no cv2.data) as well as
    pip wheels — the candidate list is tried in order."""

    def test_joins_cascade_file_name(self):
        seen = []
        path = fw.resolve_haar_cascade(["/d"], exists=lambda p: seen.append(p) or True)
        self.assertEqual(path, "/d/" + fw.HAAR_CASCADE_FILE)
        self.assertEqual(seen, ["/d/" + fw.HAAR_CASCADE_FILE])

    def test_prefers_first_existing_candidate(self):
        existing = {"/b/" + fw.HAAR_CASCADE_FILE, "/c/" + fw.HAAR_CASCADE_FILE}
        self.assertEqual(fw.resolve_haar_cascade(["/a", "/b", "/c"], exists=existing.__contains__),
                         "/b/" + fw.HAAR_CASCADE_FILE)

    def test_none_when_no_candidate_exists(self):
        self.assertIsNone(fw.resolve_haar_cascade(["/a", "/b"], exists=lambda p: False))

    def test_system_distros_are_covered(self):
        # Arch ships the XMLs in /usr/share/opencv5/haarcascades (python-opencv
        # 5.0.0 has no cv2.data); at least that path must be a candidate.
        self.assertIn("/usr/share/opencv5/haarcascades", fw.HAAR_CASCADE_DIRS)


class DnnDecode(unittest.TestCase):
    """ULFG-style output rows: per-anchor [bg, face] scores + boxes. The raw net
    fires ~10 boxes per real face (measured 2026-10-22); the decode must count
    DISTINCT faces and never index a score pair as a scalar."""

    def test_one_face_amid_many_anchors_counts_one(self):
        scores = [[0.99, 0.01], [0.2, 0.8], [0.95, 0.05]]
        boxes = [[0, 0, 10, 10], [5, 5, 25, 25], [100, 100, 110, 110]]
        self.assertEqual(fw.decode_dnn_faces(scores, boxes, 0.5), 1)

    def test_overlapping_boxes_suppressed(self):
        scores = [[0.0, 0.9], [0.0, 0.85], [0.0, 0.8]]
        boxes = [[0, 0, 10, 10], [1, 1, 11, 11], [2, 0, 12, 10]]
        self.assertEqual(fw.decode_dnn_faces(scores, boxes, 0.5), 1)

    def test_distinct_faces_counted_separately(self):
        scores = [[0.0, 0.9], [0.0, 0.8]]
        boxes = [[0, 0, 10, 10], [100, 100, 110, 110]]
        self.assertEqual(fw.decode_dnn_faces(scores, boxes, 0.5), 2)

    def test_below_threshold_is_no_face(self):
        self.assertEqual(fw.decode_dnn_faces([[0.7, 0.3]], [[0, 0, 10, 10]], 0.5), 0)

    def test_empty_rows(self):
        self.assertEqual(fw.decode_dnn_faces([], [], 0.5), 0)

    def test_iou_disjoint_and_identical(self):
        self.assertEqual(fw.box_iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)
        self.assertAlmostEqual(fw.box_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)


class LockGate(unittest.TestCase):
    """Privacy gate (security review 2026-10-22): capture only on KNOWN
    unlocked. 'Unknown' must deny — the old gate captured on None."""

    def test_known_unlocked_allows(self):
        self.assertTrue(fw.may_capture(False))

    def test_locked_denies(self):
        self.assertFalse(fw.may_capture(True))

    def test_unknown_denies(self):
        self.assertFalse(fw.may_capture(None))


class ParseLocked(unittest.TestCase):
    """`noctalia msg status` parsing is strict: anything but a boolean
    `locked` field is 'unknown', never 'unlocked'."""

    def test_boolean_states(self):
        self.assertIs(fw.parse_locked('{"locked": true}'), True)
        self.assertIs(fw.parse_locked('{"locked": false}'), False)

    def test_missing_key_is_unknown(self):
        self.assertIsNone(fw.parse_locked("{}"))

    def test_non_boolean_value_is_unknown(self):
        self.assertIsNone(fw.parse_locked('{"locked": "yes"}'))

    def test_non_object_json_is_unknown(self):
        self.assertIsNone(fw.parse_locked("[]"))

    def test_malformed_or_empty_is_unknown(self):
        self.assertIsNone(fw.parse_locked("garbage"))
        self.assertIsNone(fw.parse_locked(""))


class ModelDigest(unittest.TestCase):
    """Planted ONNX = code execution in OpenCV's importer; only the pinned
    bytes may load."""

    def test_matching_digest_loads(self):
        import hashlib
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"model-bytes")
            path = fh.name
        try:
            expected = hashlib.sha256(b"model-bytes").hexdigest()
            self.assertTrue(fw.model_digest_ok(path, expected))
        finally:
            os.remove(path)

    def test_tampered_file_refused(self):
        import hashlib
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"tampered-bytes")
            path = fh.name
        try:
            expected = hashlib.sha256(b"model-bytes").hexdigest()
            self.assertFalse(fw.model_digest_ok(path, expected))
        finally:
            os.remove(path)

    def test_pinned_digest_is_a_sha256(self):
        self.assertRegex(fw.DNN_MODEL_SHA256, r"^[0-9a-f]{64}$")


class StatusFileModes(unittest.TestCase):
    """Status/log files carry occupancy patterns: 0600, no symlink follow."""

    def test_write_status_creates_private_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "status.json")
            fw.write_status(path, state="idle")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_write_status_tightens_existing_loose_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "status.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            os.chmod(path, 0o644)
            fw.write_status(path, state="idle")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_log_line_tightens_existing_loose_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "debug.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("old\n")
            os.chmod(path, 0o644)
            fw.log_line(path, "verdict")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class GatePoke(unittest.TestCase):
    """The gate contract: reset the idle countdown ONLY on a confirmed face.
    Every other outcome exits and lets the configured idle chain proceed."""

    def test_face_resets_the_countdown(self):
        self.assertTrue(fw.should_poke(fw.EXIT_FACE))

    def test_no_face_does_not_reset(self):
        self.assertFalse(fw.should_poke(fw.EXIT_NO_FACE))

    def test_skipped_or_error_does_not_reset(self):
        self.assertFalse(fw.should_poke(fw.EXIT_SKIPPED))
        self.assertFalse(fw.should_poke(fw.EXIT_ERROR))


class GateTapLockGuard(unittest.TestCase):
    """Review 2026-10-22 (community-plugins#1117): the lock state must be
    re-checked immediately before the synthetic tap. Capture+detect can span up
    to ~15 s, so a session that locks mid-check must never receive input.
    Deterministic harness: camera and keyboard operations are replaced."""

    def run_gate(self, lock_states, faces=1):
        """Run cmd_gate with camera/keyboard replaced. `lock_states` is the
        sequence returned by successive session_locked() calls (check start,
        pre-capture, pre-tap). Returns (exit_code, verdict, pokes)."""
        import contextlib
        import io

        states = iter(lock_states)
        pokes = []
        orig = (fw.session_locked, fw.resolve_camera, fw.capture_frame,
                fw.detect_faces, fw.poke_idle)
        fw.session_locked = lambda: next(states)
        fw.resolve_camera = lambda args: "/dev/video0"
        fw.capture_frame = lambda camera, width: object()
        fw.detect_faces = lambda img, detector, sensitivity: faces
        fw.poke_idle = lambda log_path=None: pokes.append(log_path) or True
        try:
            args = argparse.Namespace(
                camera_path="", detector="haar", sensitivity=5,
                capture_width=640, log_file=None, status_file=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = fw.cmd_gate(args)
            return code, out.getvalue().strip(), pokes
        finally:
            (fw.session_locked, fw.resolve_camera, fw.capture_frame,
             fw.detect_faces, fw.poke_idle) = orig

    def test_tap_refused_when_session_locks_during_capture(self):
        # Maintainer's reproduction: unlocked at both pre-capture checks, locks
        # during capture/detection. The tap must not be sent.
        code, verdict, pokes = self.run_gate([False, False, True])
        self.assertEqual(pokes, [])
        self.assertEqual(code, fw.EXIT_SKIPPED)
        self.assertEqual(verdict, "locked")

    def test_tap_refused_when_lock_state_unknown_before_tap(self):
        # Unknown counts as locked: fail closed at the tap too.
        code, verdict, pokes = self.run_gate([False, False, None])
        self.assertEqual(pokes, [])
        self.assertEqual(code, fw.EXIT_SKIPPED)
        self.assertEqual(verdict, "skipped")

    def test_tap_fires_when_unlocked_throughout(self):
        code, verdict, pokes = self.run_gate([False, False, False])
        self.assertEqual(len(pokes), 1)
        self.assertEqual(code, fw.EXIT_FACE)
        self.assertEqual(verdict, "face (1)")

    def test_face_seen_but_tap_refused_still_reports_refusal(self):
        # The face verdict must not claim a reset that never happened.
        code, verdict, pokes = self.run_gate([False, False, True], faces=3)
        self.assertEqual(pokes, [])
        self.assertNotIn("face", verdict)
        self.assertEqual(code, fw.EXIT_SKIPPED)


class LazyImports(unittest.TestCase):
    def test_module_imports_without_cv2_or_dbus(self):
        # Loading the module at the top of this file already proves it; pin the
        # modules are absent from the namespace so the laziness cannot regress.
        self.assertNotIn("cv2", vars(fw))
        self.assertNotIn("dbus", vars(fw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
