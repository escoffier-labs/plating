import json

from plating.cast import CastOptions, Step, build_cast

_FAST = CastOptions(width=80, height=10, type_speed=0.0, prompt_pause=0.0,
                    command_pause=0.0, first_line_delay=0.0, line_delay=0.0,
                    after_output_pause=0.0, final_hold=0.0)


def test_header_is_valid_asciicast_v2():
    cast = build_cast([Step("echo hi", "hi\n")], _FAST)
    header = json.loads(cast.splitlines()[0])
    assert header["version"] == 2
    assert header["width"] == 80
    assert header["height"] == 10


def test_commands_and_output_are_present_verbatim():
    cast = build_cast([Step("brigade --version", "brigade 0.13.0\n")], _FAST)
    events = [json.loads(line) for line in cast.splitlines()[1:]]
    assert all(event[1] == "o" for event in events)
    blob = "".join(event[2] for event in events)
    assert "brigade --version" in blob
    assert "brigade 0.13.0" in blob


def test_timestamps_are_monotonic():
    cast = build_cast([Step("a", "b\n"), Step("c", "d\n")])
    times = [json.loads(line)[0] for line in cast.splitlines()[1:]]
    assert times == sorted(times)


def test_prompt_color_can_be_disabled():
    cast = build_cast([Step("ls", "")], CastOptions(prompt_color=""))
    assert "\\u001b[1;32m" not in cast and "\x1b[1;32m" not in cast
