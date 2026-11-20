import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import transcriber


class FakeModel:
    """Renvoie le nom du fichier comme texte et compte les appels."""

    def __init__(self):
        self.calls = []

    def transcribe(self, path, **_kwargs):
        self.calls.append(Path(path).name)
        return [SimpleNamespace(start=0.0, end=1.0, text=f" {Path(path).stem} ")], None


class LiveTranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "m1"
        (self.dir / "segments").mkdir(parents=True)
        (self.dir / "meeting.json").write_text(json.dumps({"id": "m1", "status": "recording"}))
        transcriber.KEEP_AUDIO = False

    def tearDown(self):
        self.tmp.cleanup()

    def add_segment(self, offset_ms, user="u1"):
        file = f"segments/{offset_ms:09d}_{user}.wav"
        (self.dir / file).write_bytes(b"wav")
        seg = {"file": file, "userId": user, "speaker": user.upper(), "offsetMs": offset_ms, "durationMs": 1000}
        with (self.dir / "segments.jsonl").open("a") as f:
            f.write(json.dumps(seg) + "\n")

    def test_la_passe_finale_ne_refait_que_les_dernieres_prises_de_parole(self):
        model = FakeModel()
        self.add_segment(2000)
        self.add_segment(0, "u2")
        self.assertEqual(transcriber.transcribe_new_segments(model, self.dir, limit=10), 2)
        self.assertEqual(transcriber.transcribe_new_segments(model, self.dir, limit=10), 0)

        self.add_segment(5000)
        model.calls.clear()
        count = transcriber.transcribe_meeting(model, self.dir)

        self.assertEqual(model.calls, ["000005000_u1.wav"])
        self.assertEqual(count, 3)
        transcript = json.loads((self.dir / "transcript.json").read_text())
        self.assertEqual([e["text"] for e in transcript["entries"]], ["000000000_u2", "000002000_u1", "000005000_u1"])
        self.assertEqual(transcript["entries"][0]["speaker"], "U2")
        self.assertFalse((self.dir / transcriber.PARTIAL).exists())
        self.assertEqual(list((self.dir / "segments").iterdir()), [])

    def test_limite_par_tour(self):
        model = FakeModel()
        for i in range(5):
            self.add_segment(i * 1000)
        self.assertEqual(transcriber.transcribe_new_segments(model, self.dir, limit=3), 3)
        self.assertEqual(transcriber.transcribe_new_segments(model, self.dir, limit=3), 2)
        self.assertEqual(len(model.calls), 5)

    def test_ligne_en_cours_d_ecriture_ignoree(self):
        self.add_segment(0)
        with (self.dir / "segments.jsonl").open("a") as f:
            f.write('{"file": "segments/0000')
        self.assertEqual(transcriber.transcribe_new_segments(FakeModel(), self.dir), 1)


if __name__ == "__main__":
    unittest.main()
