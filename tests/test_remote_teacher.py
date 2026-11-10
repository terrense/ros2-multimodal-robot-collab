"""The served teacher: same numbers as the in-process one, over HTTP.

The teacher is too big to live inside the training process, so it is served by
vLLM and queried over HTTP. Two things have to hold for that to be the *same*
teacher rather than a different one:

    * the request carries token ids, so the server cannot re-tokenize the
      student's tokens differently, and
    * the log-probabilities come back in the same alignment the in-process
      path uses, or every span would be scored against the wrong token.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from training.policies.teacher import RemoteTeacherScorer


class _FakeTeacher:
    """A completions endpoint that answers with fixed log-probabilities."""

    def __init__(self, token_logprobs: list[float], *, status: int = 200) -> None:
        self.token_logprobs = token_logprobs
        self.status = status
        self.requests: list[tuple[str, dict, dict]] = []

    def handler(self):
        teacher = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name is the API
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                teacher.requests.append((self.path, payload, dict(self.headers)))
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "text": "",
                                "logprobs": {"token_logprobs": teacher.token_logprobs},
                            }
                        ]
                    }
                ).encode("utf-8")
                self.send_response(teacher.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        return Handler


def _serve(logprobs: list[float], *, status: int = 200):
    teacher = _FakeTeacher(logprobs, status=status)
    server = HTTPServer(("127.0.0.1", 0), teacher.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return teacher, server, f"http://127.0.0.1:{server.server_port}/v1"


def test_the_request_carries_token_ids_not_text() -> None:
    teacher, server, base_url = _serve([0.0, -0.5, -1.0, -2.0])
    try:
        RemoteTeacherScorer(base_url, model="teacher").token_logprobs([10, 11, 12, 13])
    finally:
        server.shutdown()
    path, payload, _headers = teacher.requests[0]
    assert path == "/v1/completions"
    assert payload["prompt"] == [10, 11, 12, 13]
    assert payload["echo"] is True
    assert payload["max_tokens"] == 0
    assert payload["model"] == "teacher"


def test_the_api_key_is_sent_when_configured() -> None:
    teacher, server, base_url = _serve([0.0, -0.5])
    try:
        RemoteTeacherScorer(base_url, model="teacher", api_key="secret").token_logprobs([1, 2])
    finally:
        server.shutdown()
    _path, _payload, headers = teacher.requests[0]
    assert headers["Authorization"] == "Bearer secret"


def test_the_alignment_matches_the_in_process_teacher() -> None:
    """The server returns log p(token_j); the shared convention is log p(token_{i+1})."""
    teacher, server, base_url = _serve([0.0, -0.5, -1.0, -2.0])
    try:
        values = RemoteTeacherScorer(base_url, model="teacher").token_logprobs([10, 11, 12, 13])
    finally:
        server.shutdown()
    assert list(values) == [-0.5, -1.0, -2.0]


def test_the_span_disagreement_is_the_same_arithmetic_as_in_process() -> None:
    teacher, server, base_url = _serve([0.0, -0.5, -1.0, -2.0])
    try:
        scorer = RemoteTeacherScorer(base_url, model="teacher")
        results = scorer.score_spans(
            [10, 11, 12, 13],
            [(1, 3)],
            student_logprobs=[-0.25],
            student_entropies=[0.5],
            reasons=["error"],
            turn_indices=[2],
        )
    finally:
        server.shutdown()
    assert len(results) == 1
    item = results[0]
    # The span covers tokens 1 and 2, whose teacher log-probs are -0.5 and -1.0.
    assert item.teacher_logprob == pytest.approx(-0.75)
    assert item.disagreement == pytest.approx(0.5)
    assert item.turn_index == 2
    assert item.reason == "error"


def test_a_response_without_logprobs_is_an_error_not_a_zero() -> None:
    teacher, server, base_url = _serve([])
    try:
        scorer = RemoteTeacherScorer(base_url, model="teacher")
        with pytest.raises(ValueError):
            scorer.token_logprobs([1, 2, 3])
    finally:
        server.shutdown()


def test_a_server_without_a_base_url_is_refused() -> None:
    from training.policies.teacher import TeacherPlacementError

    with pytest.raises(TeacherPlacementError):
        RemoteTeacherScorer("", model="teacher")
