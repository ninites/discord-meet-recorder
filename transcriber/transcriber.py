"""Transcrit les meets Discord enregistrés par le bot, avec faster-whisper en local.

Surveille <DATA_DIR>/meetings/*/meeting.json. Chaque prise de parole (un WAV, une seule personne)
est transcrite dès que le bot l'a écrite, pendant le meet, et le résultat est gardé dans
partial.jsonl. Quand le meet passe en « recorded », il ne reste que les dernières prises de parole
à transcrire : le transcript.json est écrit et le meet passe en « transcribed ». L'audio est
supprimé ensuite, sauf KEEP_AUDIO=true.

Si STOP_PHRASE est défini, un second fil surveille les meets en cours et demande l'arrêt au bot
quand quelqu'un dit cette phrase (voir watch_stop_phrase).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from stop_phrase import ends_with_stop_phrase

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MEETINGS = DATA_DIR / "meetings"
MODEL = os.environ.get("WHISPER_MODEL", "medium")
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "fr") or None
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "0"))
# Vocabulaire maison pour aider Whisper sur les noms propres (noms de projets, outils, collègues…).
INITIAL_PROMPT = os.environ.get("WHISPER_INITIAL_PROMPT") or None
KEEP_AUDIO = os.environ.get("KEEP_AUDIO", "false").lower() == "true"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "10"))
# Pendant un meet, intervalle de relecture des nouvelles prises de parole.
LIVE_POLL_SECONDS = float(os.environ.get("LIVE_POLL_SECONDS", "2"))
# Prises de parole d'un meet en cours traitées d'affilée, pour qu'un meet terminé n'attende pas.
LIVE_BATCH = 10
# Transcriptions déjà faites pendant le meet : une ligne {"file", "entries"} par prise de parole.
PARTIAL = "partial.jsonl"
# Phrase qui arrête le meet quand quelqu'un la dit. Vide : arrêt à la voix désactivé.
STOP_PHRASE = os.environ.get("STOP_PHRASE", "").strip()
# Modèle rapide utilisé pendant le meet, seulement pour repérer la phrase d'arrêt.
STOP_MODEL = os.environ.get("WHISPER_STOP_MODEL", "small")
# Au-delà, une prise de parole est trop longue pour être juste la phrase d'arrêt : on ne la transcrit pas en direct.
STOP_MAX_SEGMENT_MS = 15_000

log = logging.getLogger("transcriber")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def set_status(meeting_dir: Path, status: str, **extra) -> dict:
    meeting = read_json(meeting_dir / "meeting.json")
    meeting["status"] = status
    meeting.update(extra)
    write_json(meeting_dir / "meeting.json", meeting)
    return meeting


def read_segments(meeting_dir: Path) -> list[dict]:
    path = meeting_dir / "segments.jsonl"
    if not path.exists():
        return []
    return sorted(read_jsonl(path), key=lambda s: s["offsetMs"])


def read_jsonl(path: Path) -> list[dict]:
    """Lignes JSON d'un fichier en cours d'écriture : une ligne incomplète est ignorée (relue au tour suivant)."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def transcribe_segment(model, meeting_dir: Path, seg: dict) -> list[dict]:
    audio = meeting_dir / seg["file"]
    if not audio.exists():
        log.warning("%s : audio manquant %s", meeting_dir.name, seg["file"])
        return []
    pieces, _info = model.transcribe(
        str(audio),
        language=LANGUAGE,
        beam_size=5,
        vad_filter=True,
        # Chaque fichier est court et ne contient qu'une personne : pas de contexte à reporter,
        # ce qui évite les répétitions en boucle de Whisper.
        condition_on_previous_text=False,
        initial_prompt=INITIAL_PROMPT,
    )
    entries = []
    for piece in pieces:
        text = piece.text.strip()
        if not text:
            continue
        entries.append(
            {
                "speaker": seg["speaker"],
                "userId": seg["userId"],
                "startMs": seg["offsetMs"] + round(piece.start * 1000),
                "endMs": seg["offsetMs"] + round(piece.end * 1000),
                "text": text,
            }
        )
    return entries


def load_partial(meeting_dir: Path) -> dict[str, list[dict]]:
    path = meeting_dir / PARTIAL
    if not path.exists():
        return {}
    return {row["file"]: row["entries"] for row in read_jsonl(path)}


def transcribe_new_segments(model, meeting_dir: Path, limit: int | None = None, skip: set[str] = frozenset()) -> int:
    """Transcrit les prises de parole pas encore faites (au plus `limit`) et les ajoute à partial.jsonl.
    Renvoie le nombre de prises de parole traitées."""
    done = load_partial(meeting_dir)
    todo = [s for s in read_segments(meeting_dir) if s["file"] not in done and s["file"] not in skip]
    if limit is not None:
        todo = todo[:limit]
    for i, seg in enumerate(todo, 1):
        entries = transcribe_segment(model, meeting_dir, seg)
        with (meeting_dir / PARTIAL).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"file": seg["file"], "entries": entries}, ensure_ascii=False) + "\n")
        if limit is None and i % 20 == 0:
            log.info("%s : %d/%d prises de parole restantes", meeting_dir.name, i, len(todo))
    return len(todo)


def transcribe_meeting(model, meeting_dir: Path) -> int:
    remaining = transcribe_new_segments(model, meeting_dir)
    log.info("%s : %d prises de parole transcrites après l'arrêt", meeting_dir.name, remaining)
    segments = read_segments(meeting_dir)
    done = load_partial(meeting_dir)
    entries = [e for seg in segments for e in done.get(seg["file"], [])]
    entries.sort(key=lambda e: e["startMs"])
    write_json(
        meeting_dir / "transcript.json",
        {
            "meetingId": meeting_dir.name,
            "engine": f"faster-whisper {MODEL}",
            "language": LANGUAGE,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
        },
    )
    (meeting_dir / PARTIAL).unlink(missing_ok=True)
    if not KEEP_AUDIO:
        for seg in segments:
            (meeting_dir / seg["file"]).unlink(missing_ok=True)
    return len(entries)


def meetings_with_status(status: str) -> list[Path]:
    if not MEETINGS.exists():
        return []
    found = []
    for meeting_json in MEETINGS.glob("*/meeting.json"):
        try:
            if read_json(meeting_json).get("status") == status:
                found.append(meeting_json.parent)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(found)


def pending_meetings() -> list[Path]:
    return meetings_with_status("recorded")


# Prises de parole illisibles pendant le meet : pas retentées en direct, la passe finale s'en charge.
live_failures: dict[str, set[str]] = {}


def transcribe_live(model) -> tuple[bool, bool]:
    """Avance la transcription des meets en cours. Renvoie (meet en cours ?, travail fait ?)."""
    recording = meetings_with_status("recording")
    worked = False
    for meeting_dir in recording:
        skip = live_failures.setdefault(meeting_dir.name, set())
        try:
            worked |= transcribe_new_segments(model, meeting_dir, limit=LIVE_BATCH, skip=skip) > 0
        except Exception:  # noqa: BLE001 — une prise de parole illisible ne doit pas bloquer le meet
            log.exception("%s : transcription en direct", meeting_dir.name)
            done = load_partial(meeting_dir)
            for seg in read_segments(meeting_dir):
                if seg["file"] not in done and seg["file"] not in skip:
                    skip.add(seg["file"])
                    break
    for meeting_id in set(live_failures) - {d.name for d in recording}:
        del live_failures[meeting_id]
    return bool(recording), worked


def recover_interrupted() -> None:
    """Un meet resté « transcribing » (transcripteur arrêté en cours de route) est repris."""
    if not MEETINGS.exists():
        return
    for meeting_json in MEETINGS.glob("*/meeting.json"):
        try:
            if read_json(meeting_json).get("status") == "transcribing":
                set_status(meeting_json.parent, "recorded")
                log.info("%s : transcription interrompue, reprise", meeting_json.parent.name)
        except (OSError, json.JSONDecodeError):
            continue


def load_model(name: str = MODEL):
    from faster_whisper import WhisperModel

    log.info("chargement du modèle %s (%s, %s)", name, DEVICE, COMPUTE_TYPE)
    return WhisperModel(name, device=DEVICE, compute_type=COMPUTE_TYPE, cpu_threads=CPU_THREADS)


def watch_stop_phrase() -> None:
    """Pendant les meets en cours, transcrit chaque prise de parole courte dès qu'elle est écrite
    et dépose stop-request.json si elle se termine par STOP_PHRASE. Le bot surveille ce fichier."""
    model = load_model(STOP_MODEL)
    log.info("arrêt à la voix actif : « %s »", STOP_PHRASE)
    seen: dict[str, set[str]] = {}
    while True:
        recording = set()
        for meeting_json in MEETINGS.glob("*/meeting.json"):
            meeting_dir = meeting_json.parent
            try:
                if read_json(meeting_json).get("status") != "recording":
                    continue
            except (OSError, json.JSONDecodeError):
                continue
            recording.add(meeting_dir.name)
            if (meeting_dir / "stop-request.json").exists():
                continue
            done = seen.setdefault(meeting_dir.name, set())
            for seg in read_segments(meeting_dir):
                if seg["file"] in done:
                    continue
                done.add(seg["file"])
                if seg["durationMs"] > STOP_MAX_SEGMENT_MS:
                    continue
                try:
                    pieces, _ = model.transcribe(
                        str(meeting_dir / seg["file"]),
                        language=LANGUAGE,
                        beam_size=1,
                        vad_filter=True,
                        condition_on_previous_text=False,
                        # Oriente Whisper vers la bonne orthographe de la phrase.
                        initial_prompt=STOP_PHRASE,
                    )
                    text = " ".join(p.text.strip() for p in pieces)
                except Exception:  # noqa: BLE001 — un fichier illisible ne doit pas arrêter la surveillance
                    log.exception("%s : lecture de %s", meeting_dir.name, seg["file"])
                    continue
                if ends_with_stop_phrase(text, STOP_PHRASE):
                    log.info("%s : phrase d'arrêt dite par %s (« %s »)", meeting_dir.name, seg["speaker"], text)
                    write_json(
                        meeting_dir / "stop-request.json",
                        {
                            "userId": seg["userId"],
                            "speaker": seg["speaker"],
                            "text": text,
                            "requestedAt": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    break
        for meeting_id in set(seen) - recording:
            del seen[meeting_id]
        time.sleep(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    MEETINGS.mkdir(parents=True, exist_ok=True)
    recover_interrupted()
    if STOP_PHRASE:
        threading.Thread(target=watch_stop_phrase, name="stop-phrase", daemon=True).start()
    model = load_model()
    log.info("prêt, surveille %s", MEETINGS)
    while True:
        for meeting_dir in pending_meetings():
            started = time.monotonic()
            log.info("%s : transcription", meeting_dir.name)
            set_status(meeting_dir, "transcribing")
            try:
                count = transcribe_meeting(model, meeting_dir)
                set_status(meeting_dir, "transcribed", transcribedAt=datetime.now(timezone.utc).isoformat())
                log.info("%s : %d phrases en %.0f s", meeting_dir.name, count, time.monotonic() - started)
            except Exception as err:  # noqa: BLE001 — un meet en échec ne doit pas bloquer les suivants
                log.exception("%s : échec", meeting_dir.name)
                set_status(meeting_dir, "failed", error=f"transcription : {err}")
        recording, worked = transcribe_live(model)
        if not worked:
            time.sleep(LIVE_POLL_SECONDS if recording else POLL_SECONDS)


if __name__ == "__main__":
    main()
