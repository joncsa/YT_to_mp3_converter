import pytest

from app.naming import build_filename, clean_title, derive_names, safe_component
from app.parsing import format_time, parse_line, parse_range, parse_time, split_input


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Song Name (Official Video)", "Song Name"),
        ("Song Name [Official Music Video] [4K]", "Song Name"),
        ("Song Name (Lyrics)", "Song Name"),
        ("Song Name (Official Audio) #shorts #music", "Song Name"),
        ("Song Name ft. Someone", "Song Name (feat. Someone)"),
        ("Song Name (featuring Someone) [HD]", "Song Name (feat. Someone)"),
        ("Song Name (Live at Wembley)", "Song Name (Live at Wembley)"),
        ("Song Name (Remix)", "Song Name (Remix)"),
        ("Song Name | Official Video", "Song Name"),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_names_from_youtube_music_tags():
    info = {"title": "whatever", "artist": "Daft Punk", "track": "Around the World", "channel": "Daft Punk - Topic"}
    assert derive_names(info, "music")[:2] == ("Daft Punk", "Around the World")


def test_names_from_video_title():
    info = {"title": "The Weeknd ft. Daft Punk - Starboy (Official Video)", "channel": "TheWeekndVEVO"}
    assert derive_names(info, "music")[:2] == ("The Weeknd", "Starboy (feat. Daft Punk)")


def test_names_quoted_title():
    info = {"title": 'BLACKPINK "Pink Venom" M/V', "channel": "BLACKPINK"}
    assert derive_names(info, "music")[:2] == ("BLACKPINK", "Pink Venom")


def test_names_fallback_to_channel():
    info = {"title": "Bohemian Rhapsody (Official Video Remastered)", "channel": "Queen Official"}
    artist, title, _ = derive_names(info, "music")
    assert artist == "Queen"
    assert title.startswith("Bohemian Rhapsody")


def test_podcast_names():
    info = {"title": "Huberman Lab - Sleep Toolkit | Episode 84", "channel": "Huberman Lab"}
    assert derive_names(info, "podcast")[:2] == ("Huberman Lab", "Sleep Toolkit | Episode 84")


def test_filename_is_universal():
    assert build_filename('AC/DC', 'What?: "Live" <1991>') == "AC-DC - What - 'Live' 1991.mp3"
    assert safe_component("CON") == "_CON"
    long = build_filename("A" * 150, "B" * 150)
    assert len(long.encode()) <= 184 and long.endswith(".mp3")


def test_parse_lines():
    lines = split_input(
        "https://youtu.be/abc\n\n# comment\npodcast: Lex Fridman 400\nDaft Punk Around the World | Daft Punk - Around the World | 0:05-7:00\nhttps://youtu.be/abc\n",
        "auto",
    )
    assert [p.query for p in lines] == ["https://youtu.be/abc", "Lex Fridman 400", "Daft Punk Around the World"]
    assert lines[0].is_url and lines[1].content_type == "podcast"
    assert lines[2].name_override == "Daft Punk - Around the World"
    assert (lines[2].start_override, lines[2].end_override) == (5.0, 420.0)


def test_time_helpers():
    assert parse_time("1:02:03.5") == 3723.5
    assert parse_time("3:45") == 225
    assert parse_range("0:12.5 - 3:41") == (12.5, 221.0)
    assert parse_range("0:12-") == (12.0, None)
    assert parse_range("Artist - Title") is None
    assert format_time(225) == "3:45.0"
    assert parse_time(format_time(3723.5)) == 3723.5
