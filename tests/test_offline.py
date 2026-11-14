"""Offline checks: no network, no key, no money. Run from the repository root:

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import captions  # noqa: E402
import config  # noqa: E402
import oneshot  # noqa: E402
import runninghub  # noqa: E402

FILM = json.loads((ROOT / "examples/bamboo/film.json").read_text(encoding="utf-8"))


class FilmExample(unittest.TestCase):
    def test_segments_join_on_shared_keyframes(self):
        segs = FILM["segments"]
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a["last"], b["first"], f"{a['id']} → {b['id']}")

    def test_keyframes_edit_known_frames(self):
        ids = [k["id"] for k in FILM["keyframes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for k in FILM["keyframes"][1:]:
            for ref in [k.get("from"), *k.get("also", [])]:
                if ref:
                    self.assertIn(ref, ids, k["id"])
        used = {s["first"] for s in FILM["segments"]} | {s["last"] for s in FILM["segments"]}
        self.assertTrue(used <= set(ids))

    def test_captions_point_at_real_lines(self):
        n = len(FILM["script"])
        for item in FILM["captions"]["titles"] + FILM["captions"]["poems"]:
            self.assertLess(item["line"], n)


class H3Prompt(unittest.TestCase):
    def test_frames_snap_to_17k_plus_5(self):
        for seconds in (3, 5, 5.2, 7.7, 13, 15, 20):
            n, eff = runninghub.frames_for(seconds)
            self.assertEqual(n % 17, 5)
            self.assertGreaterEqual(eff, 5.0)
            self.assertLessEqual(n, 15 * 24 + 17)

    def test_first_line_pins_both_pictures(self):
        body = oneshot.h3_body(None, FILM["segments"][2])
        p = runninghub.fl_prompt(body, 5.2)
        head, rest = p.split("\n\n", 1)
        _, eff = runninghub.frames_for(5.2)
        self.assertIn("Picture 1 (from Shot 1) aligns with the 0.00-second mark", head)
        self.assertIn(f"Picture 2 (from Shot 1) aligns with the {eff:.2f}-second mark", head)
        self.assertTrue(rest.startswith("integrated_multimodal_description:"))
        self.assertIn("overall_soundscape:", rest)
        self.assertIn("non_diegetic_music: N/A", rest)

    def test_graph_fills_first_and_last_frame(self):
        g = runninghub.graph("PROMPT", "first.jpg", "last.jpg", 7.3, "9:16", 42)
        node = g[runninghub.FL_NODE]
        self.assertEqual(node["class_type"], "MiniMaxH3ImageToVideo")
        self.assertEqual(node["inputs"]["first_frame"][0], runninghub.FIRST)
        self.assertEqual(node["inputs"]["last_frame"][0], runninghub.LAST)
        self.assertEqual(g[runninghub.FIRST]["inputs"]["image"], "first.jpg")
        self.assertEqual(g[runninghub.LAST]["inputs"]["image"], "last.jpg")
        self.assertEqual(g[runninghub.PROMPT]["inputs"]["text"], "PROMPT")
        self.assertEqual(g[runninghub.SEED]["inputs"]["seed"], 42)
        self.assertEqual(runninghub.graph("p", "a", "b", 30, "9:16", 1)[runninghub.LENGTH]["inputs"]["value"], 15.0)


class Plan(unittest.TestCase):
    def test_plan_times_segments_from_the_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            pj_dir = Path(tmp)
            (pj_dir / "film.json").write_text(json.dumps(FILM, ensure_ascii=False), encoding="utf-8")
            chars, lines, t = [], [], 0.0
            for text in FILM["script"]:
                s = t
                for c in text:
                    if oneshot.norm(c):
                        chars.append({"c": oneshot.norm(c), "s": round(t, 3), "e": round(t + 0.3, 3)})
                        t += 0.3
                lines.append({"text": text, "s": round(s, 3), "e": round(t, 3)})
                t += 0.4
            (pj_dir / "voice").mkdir()
            (pj_dir / "voice/chars.json").write_text(json.dumps(chars, ensure_ascii=False), encoding="utf-8")
            (pj_dir / "voice/lines.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
            pj = oneshot.Project(pj_dir)
            oneshot.cmd_plan(pj, None)
            plan = json.loads((pj_dir / "gen/plan.json").read_text(encoding="utf-8"))
            rows = plan["segments"]
            self.assertEqual([r["id"] for r in rows], [s["id"] for s in FILM["segments"]])
            self.assertEqual(rows[0]["t0"], 0.0)
            for a, b in zip(rows, rows[1:]):
                self.assertEqual(a["t1"], b["t0"])
                self.assertLess(a["t0"], b["t0"])
            self.assertEqual(rows[-1]["t1"], plan["end"])


class CaptionCheck(unittest.TestCase):
    def test_overlap_and_safe_area_are_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = [
                {"kind": "title", "text": "竹鞭", "t0": 1, "t1": 3, "box": [300, 700, 780, 940]},
                {"kind": "poem", "text": "绿竹半含箨", "t0": 2, "t1": 5, "box": [200, 900, 880, 990]},
                {"kind": "sub", "text": "就是冬笋", "t0": 0, "t1": 2, "box": [40, 1340, 600, 1420]},
            ]
            Path(tmp, "events.json").write_text(json.dumps({"style": "moyun", "safe": [110, 260, 970, 1520],
                                                            "events": ev}, ensure_ascii=False), encoding="utf-8")
            r = captions.check(tmp)
            self.assertEqual([o[:2] for o in r["overlaps"]], [("竹鞭", "绿竹半含箨")])
            self.assertEqual([o[1] for o in r["outside_safe"]], ["就是冬笋"])

    def test_three_styles(self):
        self.assertEqual(set(captions.STYLES), {"moyun", "jinshu", "songyun"})
        names = {f["file"] for f in config.FONTS["fonts"]}
        for style in captions.STYLES.values():
            for part in ("title", "poem", "sub", "src"):
                self.assertIn(style[part][0], names)


class Keys(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("EVOLINK_API_KEY", "EVOLINK_KEY_FILE")}
        self.local = config._local_key_files
        config._local_key_files = lambda name: []

    def tearDown(self):
        config._local_key_files = self.local
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_missing_key_stops_without_printing_a_value(self):
        self.assertEqual(config.key("EVOLINK_API_KEY", required=False), "")
        with self.assertRaises(SystemExit) as cm:
            config.key("EVOLINK_API_KEY")
        self.assertIn("EVOLINK_API_KEY", str(cm.exception))

    def test_environment_then_key_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as fh:
            fh.write("  from-file  \n")
        try:
            os.environ["EVOLINK_KEY_FILE"] = fh.name
            self.assertEqual(config.key("EVOLINK_API_KEY"), "from-file")
            os.environ["EVOLINK_API_KEY"] = "from-env"
            self.assertEqual(config.key("EVOLINK_API_KEY"), "from-env")
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
