from pathlib import Path

from services.film_drama_service import FilmDramaService


def test_parse_script_and_match_scene_files(tmp_path: Path) -> None:
    script = tmp_path / "script.txt"
    script.write_text(
        "1. INT. HOUSE - NIGHT\nMina enters.\n\n"
        "2. EXT. STREET - DAY\nA car stops.\n",
        encoding="utf-8",
    )
    storyboard = tmp_path / "storyboard"
    storyboard.mkdir()
    (storyboard / "scene_1_panel_1.png").touch()
    footage = tmp_path / "footage"
    footage.mkdir()
    (footage / "scene_1_take_2.mp4").touch()

    service = FilmDramaService()
    scenes = service.parse_script(script)
    panels = service.scan_storyboard(storyboard)
    takes = service.scan_footage(footage)

    assert [scene.scene_number for scene in scenes] == ["1", "2"]
    assert panels[0].scene_number == "1"
    assert panels[0].panel_number == "1"
    assert takes[0].scene_number == "1"
    assert takes[0].take_number == "2"
