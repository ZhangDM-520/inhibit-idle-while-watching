#!/usr/bin/env python3
"""Pure tests for the face-watch helper (no camera, no cv2, no dbus).

Every IO-heavy import in face-watch is lazy, so the decision layer can be
exercised under a bare python3: device parsing/selection, sensitivity mapping,
and the episode-loop decision that owns the hold/release contract.
"""

import importlib.machinery
import importlib.util
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


class EpisodeDecision(unittest.TestCase):
    """The hold/release contract: hold ONLY while faces keep appearing and the
    hard cap has not expired. Everything else releases (fail-open)."""

    def test_face_before_deadline_holds(self):
        self.assertEqual(fw.decide_episode("face", False, now=100.0, deadline=200.0), "hold")

    def test_face_at_deadline_releases(self):
        self.assertEqual(fw.decide_episode("face", True, now=200.0, deadline=200.0), "release")

    def test_no_face_releases_even_while_holding(self):
        self.assertEqual(fw.decide_episode("no_face", True, now=100.0, deadline=200.0), "release")

    def test_error_releases(self):
        self.assertEqual(fw.decide_episode("error", True, now=100.0, deadline=200.0), "release")


class LazyImports(unittest.TestCase):
    def test_module_imports_without_cv2_or_dbus(self):
        # Loading the module at the top of this file already proves it; pin the
        # modules are absent from the namespace so the laziness cannot regress.
        self.assertNotIn("cv2", vars(fw))
        self.assertNotIn("dbus", vars(fw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
