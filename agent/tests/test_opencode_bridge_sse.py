"""Unit tests for the hand-rolled SSE frame parser (src/opencode_bridge/sse.py).

httpx-sse is deliberately not a dependency (plan D10), so the wire framing
parser is ours and is pinned here: chunk-boundary independence, CRLF
handling, comments, multi-line data, field defaults, and the spec's
dispatch rules.
"""

from __future__ import annotations

from src.opencode_bridge.sse import SseFrame, SseFrameParser


def feed_all(parser: SseFrameParser, *chunks: str) -> list[SseFrame]:
    frames: list[SseFrame] = []
    for chunk in chunks:
        frames.extend(parser.feed(chunk))
    return frames


def test_single_frame_dispatched_on_blank_line() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, 'data: {"type":"session.idle"}\n\n')
    assert frames == [SseFrame(data='{"type":"session.idle"}')]


def test_multiple_frames_in_one_chunk_keep_wire_order() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data: one\n\ndata: two\n\ndata: three\n\n")
    assert [frame.data for frame in frames] == ["one", "two", "three"]


def test_frame_split_across_arbitrary_chunk_boundaries() -> None:
    wire = 'data: {"type":"message.part.delta","properties":{"delta":"x"}}\n\n'
    parser = SseFrameParser()
    frames: list[SseFrame] = []
    for char in wire:  # byte-by-byte: the worst possible chunking
        frames.extend(parser.feed(char))
    assert len(frames) == 1
    assert frames[0].data == '{"type":"message.part.delta","properties":{"delta":"x"}}'


def test_crlf_line_endings_are_accepted() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data: one\r\n\r\ndata: two\r\n\r\n")
    assert [frame.data for frame in frames] == ["one", "two"]


def test_comment_lines_are_ignored() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, ": keep-alive\n\ndata: real\n\n:\n\n")
    assert [frame.data for frame in frames] == ["real"]


def test_multiline_data_joined_with_newline() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data: line1\ndata: line2\n\n")
    assert frames == [SseFrame(data="line1\nline2")]


def test_only_one_leading_space_is_stripped_from_value() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data:  padded\n\ndata:tight\n\n")
    assert [frame.data for frame in frames] == [" padded", "tight"]


def test_field_without_colon_yields_empty_value() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data\n\n")
    assert frames == [SseFrame(data="")]


def test_event_and_id_fields_are_captured_and_id_is_sticky() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "event: custom\nid: 42\ndata: one\n\ndata: two\n\n")
    assert frames[0] == SseFrame(data="one", event="custom", id="42")
    # No event: on the second frame -> spec default "message"; id sticks.
    assert frames[1] == SseFrame(data="two", event="message", id="42")


def test_id_containing_nul_is_ignored() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "id: bad\x00id\ndata: one\n\n")
    assert frames[0].id == ""


def test_retry_and_unknown_fields_are_ignored() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "retry: 3000\nfoo: bar\ndata: one\n\n")
    assert frames == [SseFrame(data="one")]


def test_blank_line_without_data_dispatches_nothing() -> None:
    parser = SseFrameParser()
    assert feed_all(parser, "\n\n\nevent: ghost\n\n") == []


def test_incomplete_frame_at_stream_end_is_discarded() -> None:
    parser = SseFrameParser()
    frames = feed_all(parser, "data: complete\n\n", "data: truncated\n")
    assert [frame.data for frame in frames] == ["complete"]


def test_empty_chunks_are_noops() -> None:
    parser = SseFrameParser()
    assert feed_all(parser, "", "", "data: x\n\n", "") == [SseFrame(data="x")]
